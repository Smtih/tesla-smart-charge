import asyncio
import json
import logging
import logging.handlers
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from urllib.parse import urlencode

import aiohttp
import zmq
import zmq.asyncio

from nicegui import app, ui
import hashlib
from tesla_fleet_api import TeslaFleetOAuth
from tesla_fleet_api.const import Scope

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_VERSION = '2026.02.02h'
CHECK_INTERVAL = 60            # Check telemetry-driven state every 60 seconds
WAKE_POLL_INTERVAL = 7200      # Fallback: wake + poll every 2 hours if no telemetry
ZMQ_ENDPOINT = os.environ.get('ZMQ_ENDPOINT', 'tcp://localhost:5284')
DUCKDNS_DOMAIN = 'smtih'
DUCKDNS_INTERVAL = 300  # Update DuckDNS every 5 minutes
WEEKDAY_LIMIT = 75
WEEKEND_LIMIT = 100
CHARGE_TRIGGER = 55       # Only start a weekday charge when SOC drops below this
NO_CHARGE_ABOVE = 75      # Never trigger a charge if SOC is above this (dead-zone / hysteresis)
IDLE_LIMIT = 50           # Resting charge limit — prevents car from auto-charging when plugged in
OVERNIGHT_SKIP = 90       # Skip overnight 100% charge if SOC is already above this
# Charging estimate: LFP Model Y on 10A / 230V wall charger (~2.3 kW)
BATTERY_CAPACITY_KWH = 60
CHARGER_POWER_KW = 2.3    # 10A × 230V
CHARGE_EFFICIENCY = 0.90   # ~10% AC conversion / heat losses
CHARGE_BUFFER_MIN = 30     # Extra time for cell balancing at top end


def _estimate_charge_minutes(percent_needed: float) -> float:
    """Estimate minutes to charge `percent_needed`% on a 10A wall charger."""
    kwh_needed = BATTERY_CAPACITY_KWH * (percent_needed / 100)
    effective_kw = CHARGER_POWER_KW * CHARGE_EFFICIENCY
    return (kwh_needed / effective_kw) * 60 + CHARGE_BUFFER_MIN
TESLA_EMAIL = 'smith.w.da@gmail.com'
TESLA_OWNER_SUB = os.environ.get('TESLA_OWNER_SUB', '5bf3bacf-25b4-49de-90c6-99ff813f121b')
TESLA_CLIENT_ID = '46b3b38b-c7c1-4015-9f6d-51bcaf2729b3'
def _load_client_secret() -> str:
    val = os.environ.get('TESLA_CLIENT_SECRET', '')
    if val:
        return val
    env_file = Path(__file__).parent / '.env'
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith('TESLA_CLIENT_SECRET='):
                return line.split('=', 1)[1].strip()
    return ''

TESLA_CLIENT_SECRET = _load_client_secret()

def _load_env_var(name: str) -> str:
    val = os.environ.get(name, '')
    if val:
        return val
    env_file = Path(__file__).parent / '.env'
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith(f'{name}='):
                return line.split('=', 1)[1].strip()
    return ''

DUCKDNS_TOKEN = _load_env_var('DUCKDNS_TOKEN')
TESLA_REDIRECT_URI = os.environ.get('TESLA_REDIRECT_URI', 'https://auth.tesla.com/void/callback')
PRIVATE_KEY_PATH = str(Path(__file__).parent / 'private-key.pem')
TOKEN_FILE = Path(__file__).parent / 'token.json'

LOG_FILE = Path(__file__).parent / 'smart-charge.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token persistence
# ---------------------------------------------------------------------------
def load_tokens() -> dict | None:
    try:
        if TOKEN_FILE.exists():
            return json.loads(TOKEN_FILE.read_text())
    except Exception:
        pass
    return None


def save_tokens(access_token: str, refresh_token: str, expires: int):
    TOKEN_FILE.write_text(json.dumps({
        'access_token': access_token,
        'refresh_token': refresh_token,
        'expires': expires,
    }))


# ---------------------------------------------------------------------------
# Tesla Manager
# ---------------------------------------------------------------------------
class TeslaManager:
    def __init__(self):
        self.oauth: TeslaFleetOAuth | None = None
        self.vehicle = None  # VehicleSigned instance
        self.vin: str | None = None
        self.session: aiohttp.ClientSession | None = None
        self.authenticated: bool = False
        self.init_done: bool = False

        # State
        self.battery_level: int | None = None
        self.charge_state: str = 'Unknown'
        self.charge_limit: int | None = None
        self.last_telemetry_update: datetime | None = None
        self.manual_override: bool = False
        self.action_log: list[str] = []
        self.last_error: str | None = None
        self.scheduled_charges: list[dict] = []  # Each: {"time": str, "repeat_weekly": bool}
        self.active_scheduled_charge: dict | None = None  # Track which schedule is currently charging
        self._schedule_file = Path(__file__).parent / 'scheduled_charges.json'
        self._load_scheduled_charges()

    # -- helpers -------------------------------------------------------------

    def _log(self, msg: str):
        ts = datetime.now().strftime('%a %H:%M')
        entry = f'[{ts}] {msg}'
        self.action_log.insert(0, entry)
        if len(self.action_log) > 100:
            self.action_log.pop()
        log.info(msg)

    def _load_scheduled_charges(self):
        try:
            if self._schedule_file.exists():
                data = json.loads(self._schedule_file.read_text())
                # Check if old format (list of strings)
                if data and isinstance(data[0], str):
                    # Archive old format
                    old_file = self._schedule_file.with_suffix('.json.old')
                    self._schedule_file.rename(old_file)
                    self._log(f'Archived old schedule format to {old_file.name}')
                    data = None  # Force prefill
                else:
                    self.scheduled_charges = data
                    return

            # Prefill with Saturday & Sunday 8am repeating schedules
            now = datetime.now()
            # Find next Saturday
            days_until_sat = (5 - now.weekday()) % 7
            if days_until_sat == 0 and now.hour >= 8:
                days_until_sat = 7  # Already past this Saturday 8am
            next_sat = (now + timedelta(days=days_until_sat)).replace(hour=8, minute=0, second=0, microsecond=0)
            next_sun = next_sat + timedelta(days=1)

            self.scheduled_charges = [
                {"time": next_sat.isoformat(), "repeat_weekly": True},
                {"time": next_sun.isoformat(), "repeat_weekly": True}
            ]
            self._save_scheduled_charges()
            self._log('Prefilled Saturday & Sunday 8am repeating schedules')
        except Exception as e:
            self._log(f'Error loading schedules: {e}')
            self.scheduled_charges = []

    def _save_scheduled_charges(self):
        try:
            self._schedule_file.write_text(json.dumps(self.scheduled_charges))
        except Exception:
            pass

    # -- auth ----------------------------------------------------------------

    async def init_api(self):
        """Initialize the OAuth API and try to restore saved tokens."""
        self.session = aiohttp.ClientSession()
        tokens = load_tokens()

        self.oauth = TeslaFleetOAuth(
            session=self.session,
            client_id=TESLA_CLIENT_ID,
            client_secret=TESLA_CLIENT_SECRET,
            redirect_uri=TESLA_REDIRECT_URI,
            region='na',
            access_token=tokens['access_token'] if tokens else None,
            refresh_token=tokens['refresh_token'] if tokens else None,
            expires=tokens.get('expires', 0) if tokens else 0,
        )

        # Load private key for signed commands
        await self.oauth.get_private_key(PRIVATE_KEY_PATH)

        if tokens:
            try:
                await self._setup_vehicle()
                self.authenticated = True
                self._log('Restored saved session')
            except Exception as e:
                self._log(f'Saved token failed, re-auth needed: {e}')
                self.authenticated = False
        self.init_done = True

    def get_login_url(self) -> str:
        scopes = [Scope.OPENID, Scope.OFFLINE_ACCESS, Scope.VEHICLE_DEVICE_DATA, Scope.VEHICLE_CMDS, Scope.VEHICLE_CHARGING_CMDS, Scope.VEHICLE_LOCATION]
        params = urlencode({
            'response_type': 'code',
            'client_id': TESLA_CLIENT_ID,
            'redirect_uri': TESLA_REDIRECT_URI,
            'scope': ' '.join(scopes),
            'state': 'login',
        })
        return f'https://auth.tesla.com/oauth2/v3/authorize?{params}'

    async def complete_auth(self, code: str):
        """Exchange authorization code for tokens and set up vehicle."""
        if not TESLA_CLIENT_SECRET:
            raise RuntimeError('TESLA_CLIENT_SECRET env var is not set')

        # Exchange code for tokens
        async with self.session.post(
            'https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token',
            data={
                'grant_type': 'authorization_code',
                'client_id': TESLA_CLIENT_ID,
                'client_secret': TESLA_CLIENT_SECRET,
                'code': code,
                'redirect_uri': TESLA_REDIRECT_URI,
            },
        ) as resp:
            data = await resp.json(content_type=None)
            if not isinstance(data, dict):
                body = await resp.text()
                raise RuntimeError(f'Token exchange returned non-JSON response (HTTP {resp.status}): {body[:300]}')
            log.info(f'Token response keys: {list(data.keys())}')
            if not resp.ok:
                raise RuntimeError(f'Token exchange failed: {data}')
            if not data.get('refresh_token'):
                log.warning('No refresh_token in response — sessions will not persist across restarts')
            self.oauth.refresh_token = data.get('refresh_token')
            self.oauth._access_token = data['access_token']
            self.oauth.expires = int(time.time()) + data['expires_in']

            # Verify identity via id_token (JWT) — no extra API call needed
            id_token = data.get('id_token', '')
            if id_token:
                import base64
                payload_b64 = id_token.split('.')[1]
                payload_b64 += '=' * (4 - len(payload_b64) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload_b64))
                token_sub = claims.get('sub', '')
                if TESLA_OWNER_SUB and token_sub != TESLA_OWNER_SUB:
                    self.oauth._access_token = None
                    self.oauth.refresh_token = None
                    raise RuntimeError(f'Access denied — account {token_sub} is not authorized')
                self._log(f'Authenticated as sub={token_sub}')

        save_tokens(self.oauth._access_token, self.oauth.refresh_token, self.oauth.expires)
        await self._setup_vehicle()
        self.authenticated = True
        self._log('Authentication complete')

    async def _setup_vehicle(self):
        """Discover VIN and create a VehicleSigned instance."""
        # Refresh token if needed
        if self.oauth.expires < time.time() and self.oauth.refresh_token:
            await self.oauth.refresh_access_token()
            save_tokens(self.oauth._access_token, self.oauth.refresh_token, self.oauth.expires)

        resp = await self.oauth.products()
        products = resp.get('response', [])
        vehicles = [p for p in products if 'vin' in p]
        if not vehicles:
            raise RuntimeError('No vehicles found on account')
        self.vin = vehicles[0]['vin']
        self.vehicle = self.oauth.vehicles.createSigned(self.vin)
        self._log(f'Vehicle ready: {self.vin}')

    async def _ensure_token(self):
        """Refresh access token if expired, and persist."""
        if self.oauth.expires < time.time():
            if not self.oauth.refresh_token:
                self.authenticated = False
                raise RuntimeError('Token expired and no refresh token — please re-authenticate')
            try:
                await self.oauth.refresh_access_token()
                save_tokens(self.oauth._access_token, self.oauth.refresh_token, self.oauth.expires)
            except Exception as e:
                self.authenticated = False
                raise RuntimeError(f'Token refresh failed — please re-authenticate: {e}')

    # -- API wrappers --------------------------------------------------------

    async def _wake(self):
        """Wake the vehicle — sends empty JSON body to satisfy Content-Type requirement."""
        await self._ensure_token()
        token = await self.oauth.access_token()
        async with self.session.post(
            f'{self.oauth.server}/api/1/vehicles/{self.vin}/wake_up',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json={},
        ) as resp:
            if not resp.ok:
                text = await resp.text()
                raise RuntimeError(f'wake_up failed ({resp.status}): {text}')

    async def refresh_state(self):
        await self._ensure_token()
        await self._wake()
        # Wait for vehicle to come online
        from tesla_fleet_api.exceptions import VehicleOffline
        for attempt in range(12):
            try:
                data = await self.vehicle.vehicle_data(
                    endpoints=['charge_state']
                )
                break
            except VehicleOffline:
                if attempt == 11:
                    raise
                await asyncio.sleep(5)
        cs = data['response']['charge_state']
        self.battery_level = cs['battery_level']
        self.charge_state = cs['charging_state']
        self.charge_limit = cs['charge_limit_soc']
        self.last_error = None

    async def set_charge_limit(self, pct: int):
        await self._ensure_token()
        await self._wake()
        await self.vehicle.set_charge_limit(percent=pct)
        self.charge_limit = pct
        self._log(f'Set charge limit to {pct}%')

    async def start_charging(self):
        await self._ensure_token()
        await self._wake()
        await self.vehicle.charge_start()
        self._log('Sent start charging command')

    async def stop_charging(self):
        await self._ensure_token()
        await self._wake()
        await self.vehicle.charge_stop()
        self._log('Sent stop charging command')

    # -- active mode label ---------------------------------------------------

    @property
    def active_mode(self) -> str:
        if not self.authenticated:
            return 'Not Authenticated'
        if self.manual_override:
            # Distinguish scheduled vs manual
            if self.active_scheduled_charge is not None:
                return 'Scheduled Charge'
            return 'Manual Override'
        # Top-Off Guard (runs all week)
        if self.battery_level is not None and self.battery_level < CHARGE_TRIGGER:
            return 'Top-Off Guard - Charging'
        return 'Top-Off Guard'

    def update_from_telemetry(self, fields: dict):
        """Apply streamed telemetry fields to local state."""
        changed = False
        if 'Soc' in fields:
            new_level = int(float(fields['Soc']))
            if new_level != self.battery_level:
                self.battery_level = new_level
                changed = True
        if 'DetailedChargeState' in fields:
            self.charge_state = fields['DetailedChargeState']
            changed = True
        if 'ChargeLimitSoc' in fields:
            self.charge_limit = int(float(fields['ChargeLimitSoc']))
            changed = True
        if 'ChargeAmps' in fields:
            pass  # Available for future use
        if changed:
            self.last_telemetry_update = datetime.now()
            self._log(f'Telemetry update — battery {self.battery_level}%, state {self.charge_state}, limit {self.charge_limit}%')


# ---------------------------------------------------------------------------
# Telemetry consumer (ZMQ)
# ---------------------------------------------------------------------------
async def telemetry_listener(mgr: TeslaManager):
    """Subscribe to fleet-telemetry ZMQ publisher and update manager state."""
    ctx = zmq.asyncio.Context()
    sock = ctx.socket(zmq.SUB)
    sock.connect(ZMQ_ENDPOINT)
    sock.setsockopt(zmq.SUBSCRIBE, b'')  # Subscribe to all topics
    mgr._log(f'Telemetry listener connected to {ZMQ_ENDPOINT}')

    try:
        while True:
            try:
                raw = await sock.recv()
                msg = json.loads(raw)
                # fleet-telemetry sends: {"data": [{"key": "Soc", "value": {"stringValue": "79"}}, ...], ...}
                fields = {}
                for item in msg.get('data', []):
                    key = item.get('key', '')
                    value = item.get('value', {})
                    # Typed values: stringValue, intValue, floatValue, etc.
                    val = value.get('stringValue') or value.get('intValue') or value.get('floatValue') or value.get('value')
                    if key and val is not None:
                        fields[key] = str(val)
                if fields:
                    mgr.update_from_telemetry(fields)
            except json.JSONDecodeError:
                log.warning(f'Telemetry: invalid JSON received')
            except zmq.ZMQError as e:
                mgr._log(f'Telemetry ZMQ error: {e}')
                await asyncio.sleep(5)
    finally:
        sock.close()
        ctx.term()


# ---------------------------------------------------------------------------
# Background charge loop
# ---------------------------------------------------------------------------
async def charge_loop(mgr: TeslaManager):
    # Wait for authentication
    while not mgr.authenticated:
        await asyncio.sleep(5)

    # Initial wake + poll to get starting state
    try:
        await mgr.refresh_state()
        mgr._log('Startup — fetched initial state')
    except Exception as e:
        mgr._log(f'Startup refresh failed: {e} — will retry next cycle')

    last_wake_poll = datetime.now()

    while True:
        try:
            # Fallback: wake + poll if no telemetry received for WAKE_POLL_INTERVAL
            now_dt = datetime.now()
            telemetry_stale = (
                mgr.last_telemetry_update is None
                or (now_dt - mgr.last_telemetry_update).total_seconds() > WAKE_POLL_INTERVAL
            )
            wake_poll_due = (now_dt - last_wake_poll).total_seconds() > WAKE_POLL_INTERVAL

            if telemetry_stale and wake_poll_due:
                try:
                    await mgr.refresh_state()
                    last_wake_poll = datetime.now()
                    mgr._log('Fallback wake poll — no telemetry for 2h, fetched state')
                except Exception as e:
                    mgr._log(f'Fallback wake poll failed: {e}')

            now = datetime.now()

            # --- Reset to idle when unplugged ---
            if mgr.charge_state == 'Disconnected':
                # Exit manual override if unplugged
                if mgr.manual_override:
                    mgr.manual_override = False
                    mgr._log('Unplugged during manual override — reverting to automatic mode')

                # Set appropriate charge limit based on battery level
                if mgr.battery_level is not None and mgr.battery_level >= CHARGE_TRIGGER:
                    if mgr.charge_limit is not None and mgr.charge_limit != IDLE_LIMIT:
                        await mgr.set_charge_limit(IDLE_LIMIT)
                        mgr._log(f'Unplugged at {mgr.battery_level}% — reset limit to {IDLE_LIMIT}%')
                elif mgr.battery_level is not None and mgr.battery_level < CHARGE_TRIGGER:
                    if mgr.charge_limit is not None and mgr.charge_limit != WEEKDAY_LIMIT:
                        await mgr.set_charge_limit(WEEKDAY_LIMIT)
                        mgr._log(f'Unplugged at {mgr.battery_level}% (low) — set limit to {WEEKDAY_LIMIT}%')

            # --- Rule 0: Scheduled Charges ---
            if mgr.scheduled_charges and not mgr.manual_override:
                next_schedule = mgr.scheduled_charges[0]
                next_time = datetime.fromisoformat(next_schedule["time"])
                time_until = (next_time - now).total_seconds() / 60  # minutes

                # Remove if scheduled time has passed
                if time_until < 0:
                    mgr._log(f'Scheduled charge time passed ({next_time.strftime("%a %H:%M")}) — removing from schedule')
                    mgr.scheduled_charges.pop(0)
                    mgr._save_scheduled_charges()
                else:
                    # Get current battery level
                    if mgr.battery_level is None:
                        await mgr.refresh_state()

                    if mgr.battery_level is not None:
                        # Skip if battery already high enough AND close to scheduled time
                        if mgr.battery_level >= OVERNIGHT_SKIP and time_until <= 30:
                            mgr._log(f'Scheduled charge — battery at {mgr.battery_level}% (>= {OVERNIGHT_SKIP}%), skipping')
                            mgr.scheduled_charges.pop(0)
                            mgr._save_scheduled_charges()
                        elif mgr.battery_level >= OVERNIGHT_SKIP:
                            mgr._log(f'Scheduled charge — battery at {mgr.battery_level}% (high), monitoring until {next_time.strftime("%H:%M")}')
                        else:
                            percent_needed = 100 - mgr.battery_level
                            minutes_needed = _estimate_charge_minutes(percent_needed)

                            if time_until <= minutes_needed:
                                await mgr.set_charge_limit(WEEKEND_LIMIT)
                                await mgr.start_charging()
                                mgr.manual_override = True
                                mgr.active_scheduled_charge = next_schedule
                                mgr._log(f'Scheduled charge started — need {percent_needed}% (~{int(minutes_needed)} min) in {int(time_until)} min, done by {next_time.strftime("%a %H:%M")}')

            # --- Rule 1: Manual Override / Scheduled Charge Completion ---
            if mgr.manual_override:
                await mgr.refresh_state()
                if mgr.battery_level is not None and mgr.battery_level >= 100:
                    # Check if this was a scheduled charge
                    if mgr.active_scheduled_charge:
                        schedule = mgr.active_scheduled_charge
                        schedule_time = datetime.fromisoformat(schedule["time"])

                        # If it's a repeating schedule, create next instance
                        if schedule.get("repeat_weekly", False):
                            next_time = schedule_time + timedelta(days=7)
                            new_schedule = {
                                "time": next_time.isoformat(),
                                "repeat_weekly": True
                            }
                            mgr.scheduled_charges.append(new_schedule)
                            mgr.scheduled_charges.sort(key=lambda s: s["time"])
                            mgr._log(f'Repeating schedule — created next instance for {next_time.strftime("%a %H:%M")}')

                        # Remove completed schedule
                        if schedule in mgr.scheduled_charges:
                            mgr.scheduled_charges.remove(schedule)
                            mgr._save_scheduled_charges()

                        mgr.active_scheduled_charge = None

                    # Reset to idle limit
                    await mgr.set_charge_limit(IDLE_LIMIT)
                    mgr.manual_override = False
                    mgr._log(f'Charge complete — battery at 100%, limit set to {IDLE_LIMIT}%')
                else:
                    mgr._log(f'Override active — battery at {mgr.battery_level}%')

            # --- Rule 2: Top-Off Guard (runs 7 days a week) ---
            elif not mgr.manual_override:
                if mgr.battery_level is None:
                    mgr._log('Top-off guard — no battery data, skipping')
                elif mgr.battery_level >= NO_CHARGE_ABOVE:
                    if mgr.charge_limit != IDLE_LIMIT:
                        await mgr.set_charge_limit(IDLE_LIMIT)
                        mgr._log(f'Top-off guard — battery at {mgr.battery_level}%, limit set to {IDLE_LIMIT}%')
                    else:
                        mgr._log(f'Top-off guard — battery at {mgr.battery_level}%, all good')
                elif mgr.battery_level < CHARGE_TRIGGER:
                    if mgr.charge_limit != WEEKDAY_LIMIT:
                        await mgr.refresh_state()
                        await mgr.set_charge_limit(WEEKDAY_LIMIT)
                        mgr._log(f'Top-off guard — battery {mgr.battery_level}% < {CHARGE_TRIGGER}%, set limit {WEEKDAY_LIMIT}%')
                    else:
                        mgr._log(f'Top-off guard — battery {mgr.battery_level}% low, limit already {WEEKDAY_LIMIT}%')
                else:
                    mgr._log(f'Top-off guard — battery at {mgr.battery_level}% (hysteresis zone)')

            else:
                mgr._log('No matching rule, idle')

        except Exception as e:
            mgr.last_error = str(e)
            mgr._log(f'Error: {e}')
            log.exception('Charge loop error')

        await asyncio.sleep(CHECK_INTERVAL)


# ---------------------------------------------------------------------------
# NiceGUI Auth Page
# ---------------------------------------------------------------------------
def build_auth_ui(mgr: TeslaManager):
    with ui.column().classes('w-full max-w-md mx-auto p-8 gap-4'):
        ui.label('Tesla Smart-Charge Manager').classes('text-2xl font-bold')
        ui.label('Sign in to connect your Tesla').classes('text-gray-500')

        login_url = mgr.get_login_url()
        ui.link('Sign in with Tesla', login_url).classes('text-blue-500 text-lg')
    ui.label(f'v{APP_VERSION}').classes('text-xs text-gray-400 mt-4')


# ---------------------------------------------------------------------------
# NiceGUI Dashboard
# ---------------------------------------------------------------------------
MODE_DESCRIPTIONS = {
    'Not Authenticated': 'Sign in to connect your Tesla account',
    'Manual Override': f'Charging to {WEEKEND_LIMIT}% — user initiated. Will reset to {IDLE_LIMIT}% when battery reaches 100%.',
    'Scheduled Charge': f'Charging to {WEEKEND_LIMIT}% for scheduled time. Will reset to {IDLE_LIMIT}% when complete. Skips if battery ≥{OVERNIGHT_SKIP}%.',
    'Top-Off Guard': f'Protecting battery from shallow cycles. Charge limit at {IDLE_LIMIT}% until battery drops below {CHARGE_TRIGGER}%.',
    'Top-Off Guard - Charging': f'Battery below {CHARGE_TRIGGER}% — limit raised to {WEEKDAY_LIMIT}% for one full charge session.',
}


def build_ui(mgr: TeslaManager):

    with ui.column().classes('w-full max-w-3xl mx-auto p-4 sm:p-6 gap-4 sm:gap-6'):
        # --- Header ---
        with ui.card().classes('w-full bg-gradient-to-r from-blue-600 to-blue-700'):
            ui.label('Tesla Smart-Charge Manager').classes('text-2xl sm:text-3xl font-bold text-white')
            ui.label('LFP Battery Health Optimizer').classes('text-xs sm:text-sm text-blue-100')

        # --- Status Cards ---
        with ui.row().classes('w-full gap-2 sm:gap-4 items-stretch'):
            with ui.card().classes('flex-1 p-3 sm:p-4 flex flex-col'):
                ui.label('Battery').classes('text-xs uppercase tracking-wide text-gray-500 mb-1')
                battery_label = ui.label('--').classes('text-3xl sm:text-4xl font-bold text-blue-600')

            with ui.card().classes('flex-1 p-3 sm:p-4 flex flex-col'):
                ui.label('State').classes('text-xs uppercase tracking-wide text-gray-500 mb-1')
                state_label = ui.label('--').classes('text-sm sm:text-lg font-medium')

            with ui.card().classes('flex-1 p-3 sm:p-4 flex flex-col'):
                ui.label('Limit').classes('text-xs uppercase tracking-wide text-gray-500 mb-1')
                limit_label = ui.label('--').classes('text-sm sm:text-lg font-medium')

        with ui.card().classes('w-full p-3 sm:p-4'):
            ui.label('Active Mode').classes('text-xs uppercase tracking-wide text-gray-500 mb-2')
            mode_label = ui.label('--').classes('text-xl sm:text-2xl font-bold text-gray-800 mb-1')
            mode_desc_label = ui.label('').classes('text-xs sm:text-sm text-gray-600')
            error_label = ui.label('').classes('text-red-600 text-xs sm:text-sm font-medium mt-2')

        # --- Refresh Button ---
        async def on_refresh():
            try:
                await mgr.refresh_state()
                mgr._log('Manual refresh — data updated')
            except Exception as e:
                mgr._log(f'Refresh failed: {e}')

        ui.button('Refresh State', on_click=on_refresh, icon='refresh').classes('w-full bg-gray-700 hover:bg-gray-800')

        # --- Charge to 100% / Schedule Section ---
        with ui.card().classes('w-full p-3 sm:p-4'):
            ui.label('Charge to 100%').classes('text-base sm:text-lg font-semibold mb-3')

            async def on_override():
                try:
                    mgr.manual_override = True
                    await mgr.refresh_state()
                    await mgr.set_charge_limit(WEEKEND_LIMIT)
                    await mgr.start_charging()
                    mgr._log('Manual override — charging to 100%')
                except Exception as e:
                    mgr._log(f'Override failed: {e}')

            async def on_cancel_override():
                try:
                    mgr.manual_override = False
                    await mgr.set_charge_limit(IDLE_LIMIT)
                    mgr._log(f'Manual override cancelled, limit set to {IDLE_LIMIT}%')
                except Exception as e:
                    mgr._log(f'Cancel override failed: {e}')

            with ui.row().classes('w-full gap-2 sm:gap-3 mb-4'):
                override_btn = ui.button('Charge Now', on_click=on_override, icon='bolt').classes('flex-1 bg-blue-600 hover:bg-blue-700 text-sm sm:text-base')
                cancel_btn = ui.button('Cancel Override', on_click=on_cancel_override, icon='close').classes('flex-1 bg-red-600 hover:bg-red-700 text-sm sm:text-base')

            # --- Schedule ---
            ui.separator()
            ui.label('Schedule 100% Charge (done by)').classes('text-sm font-medium text-gray-600 mt-3 mb-2')
            with ui.row().classes('w-full gap-2 items-end'):
                date_input = ui.input('Date', placeholder='YYYY-MM-DD').classes('flex-1')
                with date_input:
                    with ui.menu() as date_menu:
                        ui.date(on_change=lambda e: (date_input.set_value(e.value), date_menu.close()))
                    with date_input.add_slot('append'):
                        ui.icon('edit_calendar').on('click', date_menu.open).classes('cursor-pointer')

                time_input = ui.input('Time').classes('w-28')
                with time_input:
                    with ui.menu() as time_menu:
                        ui.time(value='20:00', on_change=lambda e: (time_input.set_value(e.value), time_menu.close()))
                    with time_input.add_slot('append'):
                        ui.icon('access_time').on('click', time_menu.open).classes('cursor-pointer')

                repeat_checkbox = ui.checkbox('Repeat weekly').classes('mb-2')

                async def on_schedule():
                    try:
                        t = time_input.value or '20:00'
                        dt = datetime.strptime(f'{date_input.value} {t}', '%Y-%m-%d %H:%M')
                        if dt <= datetime.now():
                            mgr._log('Schedule failed — date/time is in the past')
                            return

                        # Create schedule with repeat flag
                        new_schedule = {
                            "time": dt.isoformat(),
                            "repeat_weekly": repeat_checkbox.value
                        }
                        mgr.scheduled_charges.append(new_schedule)
                        mgr.scheduled_charges.sort(key=lambda s: s["time"])
                        mgr._save_scheduled_charges()

                        repeat_text = " (repeating weekly)" if repeat_checkbox.value else ""
                        mgr._log(f'Scheduled: 100% by {dt.strftime("%a %Y-%m-%d %H:%M")}{repeat_text}')

                        date_input.set_value('')
                        repeat_checkbox.value = False  # Reset checkbox

                        # Check if we need to start charging immediately
                        time_until = (dt - datetime.now()).total_seconds() / 60
                        if mgr.battery_level is None:
                            await mgr.refresh_state()
                        if mgr.battery_level is not None:
                            # Skip if battery already high enough
                            if mgr.battery_level >= OVERNIGHT_SKIP:
                                mgr._log(f'Battery at {mgr.battery_level}% (>= {OVERNIGHT_SKIP}%), charge not needed')
                            else:
                                percent_needed = 100 - mgr.battery_level
                                minutes_needed = _estimate_charge_minutes(percent_needed)
                                if time_until <= minutes_needed:
                                    await mgr.set_charge_limit(WEEKEND_LIMIT)
                                    await mgr.start_charging()
                                    mgr.manual_override = True
                                    mgr.active_scheduled_charge = new_schedule
                                    mgr._log(f'Starting now — need {percent_needed}% in {int(time_until)} min')
                    except ValueError:
                        mgr._log('Schedule failed — invalid date or time format')

                ui.button('Schedule', on_click=on_schedule, icon='schedule').classes('bg-green-600 hover:bg-green-700')

            schedule_container = ui.column().classes('w-full gap-2 mt-3')

        # --- Action Log ---
        with ui.card().classes('w-full p-3 sm:p-4'):
            ui.label('Action Log').classes('text-base sm:text-lg font-semibold mb-3')
            log_container = ui.column().classes('w-full max-h-60 sm:max-h-80 overflow-y-auto gap-1 p-2 bg-gray-50 rounded border border-gray-200')

        # --- Refresh timer ---
        def refresh_ui():
            batt = mgr.battery_level
            battery_label.text = f'{batt}%' if batt is not None else '--'
            state_label.text = mgr.charge_state
            limit_label.text = f'{mgr.charge_limit}%' if mgr.charge_limit is not None else '--'

            mode = mgr.active_mode
            mode_label.text = mode
            mode_desc_label.text = MODE_DESCRIPTIONS.get(mode, '')
            error_label.text = mgr.last_error or ''

            override_btn.visible = mgr.authenticated and not mgr.manual_override
            cancel_btn.visible = mgr.authenticated and mgr.manual_override

            schedule_container.clear()
            with schedule_container:
                if not mgr.scheduled_charges:
                    ui.label('No scheduled charges').classes('text-sm text-gray-400 italic')
                else:
                    for sc in mgr.scheduled_charges:
                        with ui.card().classes('w-full p-2'):
                            with ui.row().classes('items-center gap-3 w-full'):
                                # Icon: repeat if weekly, schedule if one-time
                                icon_name = 'repeat' if sc.get('repeat_weekly', False) else 'schedule'
                                icon_color = 'text-blue-600' if sc.get('repeat_weekly', False) else 'text-green-600'
                                ui.icon(icon_name).classes(icon_color)

                                schedule_time = datetime.fromisoformat(sc["time"])
                                ui.label(schedule_time.strftime('%a %Y-%m-%d %H:%M')).classes('text-sm font-mono flex-1')

                                ui.button(icon='delete', on_click=lambda _, s=sc: (
                                    mgr.scheduled_charges.remove(s),
                                    mgr._save_scheduled_charges(),
                                    mgr._log(f'Removed schedule for {datetime.fromisoformat(s["time"]).strftime("%a %Y-%m-%d %H:%M")}'),
                                )).props('flat dense size=sm').classes('text-red-600')

            log_container.clear()
            with log_container:
                for entry in mgr.action_log[:50]:
                    ui.label(entry).classes('text-xs font-mono text-gray-700 leading-relaxed')

        timer = ui.timer(30, refresh_ui, active=True)
        async def on_disconnect():
            timer.active = False
        app.on_disconnect(on_disconnect)
        refresh_ui()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
mgr = TeslaManager()


@ui.page('/')
async def index():
    # Add PWA meta tags
    ui.add_head_html('''
        <link rel="manifest" href="/manifest.json">
        <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
        <meta name="theme-color" content="#3B82F6">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-status-bar-style" content="default">
        <meta name="apple-mobile-web-app-title" content="Tesla Charge">
        <link rel="apple-touch-icon" href="/static/favicon.svg">
    ''')

    # Wait for init_api() to finish before deciding which UI to show
    while not mgr.init_done:
        await asyncio.sleep(0.2)
    # Check browser session
    session_email = app.storage.user.get('email')
    session_expires = app.storage.user.get('session_expires', 0)

    if session_email == TESLA_EMAIL and time.time() < session_expires:
        # Valid session — show dashboard if server has tokens
        if mgr.authenticated:
            build_ui(mgr)
        else:
            # Session valid but server lost tokens — need re-auth
            app.storage.user.clear()
            build_auth_ui(mgr)
    else:
        # No valid session
        app.storage.user.clear()
        if mgr.authenticated:
            # Server has tokens but browser has no session — need to re-auth
            build_auth_ui(mgr)
        else:
            build_auth_ui(mgr)


@ui.page('/auth/callback')
async def auth_callback(code: str = ''):
    """Handle OAuth callback from Tesla"""
    if code:
        try:
            await mgr.complete_auth(code)
            # Set browser session (30 days)
            app.storage.user['email'] = TESLA_EMAIL
            app.storage.user['session_expires'] = int(time.time()) + 30 * 24 * 3600
            ui.navigate.to('/')
        except Exception as e:
            mgr._log(f'Auth callback failed: {e}')
            ui.label(f'Authentication failed: {e}').classes('text-red-600')
    else:
        ui.label('No authorization code received').classes('text-red-600')


async def duckdns_updater():
    """Update DuckDNS with current public IP every 5 minutes."""
    if not DUCKDNS_TOKEN:
        log.warning('DUCKDNS_TOKEN not set — skipping DuckDNS updates')
        return
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                url = f'https://www.duckdns.org/update?domains={DUCKDNS_DOMAIN}&token={DUCKDNS_TOKEN}&ip='
                async with session.get(url) as resp:
                    result = await resp.text()
                    if result.strip() != 'OK':
                        log.warning(f'DuckDNS update failed: {result}')
            except Exception as e:
                log.warning(f'DuckDNS update error: {e}')
            await asyncio.sleep(DUCKDNS_INTERVAL)


async def startup():
    await mgr.init_api()
    asyncio.create_task(charge_loop(mgr))
    asyncio.create_task(telemetry_listener(mgr))
    asyncio.create_task(duckdns_updater())


# Serve static files for favicon and icons
app.add_static_files('/static', str(Path(__file__).parent / 'static'))

# Add PWA meta tags
app.add_static_file(local_file=str(Path(__file__).parent / 'static' / 'manifest.json'), url_path='/manifest.json')

app.on_startup(startup)
ui.run(
    port=8080,
    host='0.0.0.0',
    title='Tesla Smart-Charge',
    reload=False,
    favicon='⚡',
    storage_secret=hashlib.sha256(TESLA_CLIENT_SECRET.encode()).hexdigest(),
)
