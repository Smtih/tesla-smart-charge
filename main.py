import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

import teslapy
from nicegui import app, ui

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CHECK_INTERVAL = 1800  # 30 minutes
WEEKDAY_LIMIT = 80
WEEKEND_LIMIT = 100
DAYCARE_THRESHOLD = 50
TESLA_EMAIL = 'smith.w.da@gmail.com'
TESLA_CLIENT_ID = '46b3b38b-c7c1-4015-9f6d-51bcaf2729b3'
TESLA_REDIRECT_URI = 'https://auth.tesla.com/void/callback'

logging.basicConfig(level=logging.INFO, format='%(asctime)s  %(message)s')
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tesla Manager
# ---------------------------------------------------------------------------
class TeslaManager:
    def __init__(self, email: str):
        self.tesla = teslapy.Tesla(email)
        vehicles = self.tesla.vehicle_list()
        self.vehicle = vehicles[0]

        # State
        self.battery_level: int | None = None
        self.charge_state: str = 'Unknown'
        self.charge_limit: int | None = None
        self.manual_override: bool = False
        self.friday_push_sent: bool = False
        self.action_log: list[str] = []
        self.last_error: str | None = None
        self.scheduled_charges: list[datetime] = []  # one-time 100% charges
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

    def _wake(self):
        self.vehicle.sync_wake_up()

    def _load_scheduled_charges(self):
        try:
            if self._schedule_file.exists():
                data = json.loads(self._schedule_file.read_text())
                self.scheduled_charges = [datetime.fromisoformat(d) for d in data]
                self.scheduled_charges.sort()
        except Exception:
            self.scheduled_charges = []

    def _save_scheduled_charges(self):
        try:
            data = [d.isoformat() for d in self.scheduled_charges]
            self._schedule_file.write_text(json.dumps(data))
        except Exception:
            pass

    # -- API wrappers --------------------------------------------------------

    def refresh_state(self):
        self._wake()
        data = self.vehicle.get_vehicle_data()
        cs = data['charge_state']
        self.battery_level = cs['battery_level']
        self.charge_state = cs['charging_state']  # Charging, Stopped, Disconnected, Complete
        self.charge_limit = cs['charge_limit_soc']
        self.last_error = None

    def set_charge_limit(self, pct: int):
        self._wake()
        self.vehicle.command('CHANGE_CHARGE_LIMIT', percent=pct)
        self.charge_limit = pct
        self._log(f'Set charge limit to {pct}%')

    def start_charging(self):
        self._wake()
        self.vehicle.command('START_CHARGE')
        self._log('Sent start charging command')

    def stop_charging(self):
        self._wake()
        self.vehicle.command('STOP_CHARGE')
        self._log('Sent stop charging command')

    # -- active mode label ---------------------------------------------------

    @property
    def active_mode(self) -> str:
        if self.manual_override:
            return 'Manual Override'
        now = datetime.now()
        wd = now.weekday()  # 0=Mon
        if wd <= 3 or (wd == 4 and now.hour < 20):
            return 'Daycare Protection'
        if wd == 4 and now.hour >= 20:
            return 'Friday Push'
        if wd in (5, 6):
            return 'Weekend Hold'
        return 'Idle'


# ---------------------------------------------------------------------------
# Background charge loop
# ---------------------------------------------------------------------------
async def charge_loop(mgr: TeslaManager):
    first_run = True
    while True:
        try:
            # Always refresh on first run so rules have real data
            if first_run:
                try:
                    mgr.refresh_state()
                    mgr._log('Startup — fetched initial state')
                except Exception as e:
                    mgr._log(f'Startup refresh failed: {e} — will retry next cycle')
                first_run = False

            now = datetime.now()
            wd = now.weekday()  # 0=Mon .. 6=Sun
            needs_wake = False

            # Reset friday flag on Monday
            if wd == 0:
                mgr.friday_push_sent = False

            # --- Rule 0: Scheduled Charges ---
            triggered = [s for s in mgr.scheduled_charges if s <= now]
            if triggered:
                for s in triggered:
                    mgr.scheduled_charges.remove(s)
                mgr.refresh_state()
                mgr.set_charge_limit(WEEKEND_LIMIT)
                mgr.start_charging()
                mgr.manual_override = True
                mgr._save_scheduled_charges()
                mgr._log(f'Scheduled charge triggered — charging to 100%')

            # --- Rule 1: Manual Override ---
            if mgr.manual_override:
                needs_wake = True
                mgr.refresh_state()
                if mgr.battery_level is not None and mgr.battery_level >= 100:
                    mgr.set_charge_limit(WEEKDAY_LIMIT)
                    mgr.manual_override = False
                    mgr._log('Override complete — battery at 100%, reset to 80%')
                else:
                    mgr._log(f'Override active — battery at {mgr.battery_level}%')

            # --- Rule 2: Friday Push ---
            elif wd == 4 and now.hour >= 20 and not mgr.friday_push_sent:
                needs_wake = True
                mgr.refresh_state()
                mgr.set_charge_limit(WEEKEND_LIMIT)
                mgr.start_charging()
                mgr.friday_push_sent = True
                mgr._log('Friday push — set 100% and started charging')

            # --- Rule 3: Weekend Hold ---
            elif wd == 5 or (wd == 6 and now.hour < 22):
                # Only wake if we think limit might not be 100%
                if mgr.charge_limit != WEEKEND_LIMIT:
                    needs_wake = True
                    mgr.refresh_state()
                    if mgr.charge_limit != WEEKEND_LIMIT:
                        mgr.set_charge_limit(WEEKEND_LIMIT)
                        mgr._log('Weekend hold — ensured limit at 100%')
                    else:
                        mgr._log('Weekend hold — limit already 100%, no action')
                else:
                    mgr._log('Weekend hold — limit already 100%, car sleeping')

            # --- Rule 4: Smart Reset (Sunday >= 22:00) ---
            elif wd == 6 and now.hour >= 22:
                needs_wake = True
                mgr.refresh_state()
                if mgr.battery_level is not None and mgr.battery_level >= 100:
                    mgr.set_charge_limit(WEEKDAY_LIMIT)
                    mgr._log('Smart reset — battery full, limit reset to 80% for Monday')
                else:
                    mgr._log(f'Sunday night — battery at {mgr.battery_level}%, keeping 100% limit')

            # --- Rule 5: Daycare Protection (Mon-Thu + Friday before 8 PM) ---
            elif 0 <= wd <= 3 or (wd == 4 and now.hour < 20):
                if mgr.battery_level is not None and mgr.battery_level < DAYCARE_THRESHOLD:
                    # Low battery — allow charging up to 80%
                    if mgr.charge_limit != WEEKDAY_LIMIT:
                        needs_wake = True
                        mgr.refresh_state()
                        mgr.set_charge_limit(WEEKDAY_LIMIT)
                        mgr._log(f'Daycare — battery {mgr.battery_level}% < 50%, set limit 80%')
                    else:
                        mgr._log(f'Daycare — battery low, limit already 80%, car sleeping')
                elif mgr.battery_level is not None:
                    # Above threshold — cap at 50% to prevent unwanted charging
                    if mgr.charge_limit != DAYCARE_THRESHOLD:
                        needs_wake = True
                        mgr.refresh_state()
                        mgr.set_charge_limit(DAYCARE_THRESHOLD)
                        mgr._log(f'Daycare — battery {mgr.battery_level}% >= 50%, capped limit to 50%')
                    else:
                        mgr._log(f'Daycare — battery at {mgr.battery_level}%, limit already 50%, car sleeping')
                else:
                    mgr._log('Daycare — no battery data, skipping')

            else:
                mgr._log('No matching rule, idle')

        except Exception as e:
            mgr.last_error = str(e)
            mgr._log(f'Error: {e}')
            log.exception('Charge loop error')

        await asyncio.sleep(CHECK_INTERVAL)


# ---------------------------------------------------------------------------
# NiceGUI Dashboard
# ---------------------------------------------------------------------------
def build_ui(mgr: TeslaManager):

    with ui.column().classes('w-full max-w-2xl mx-auto p-4 gap-4'):
        ui.label('Tesla Smart-Charge Manager').classes('text-2xl font-bold')

        # --- Status Cards ---
        with ui.row().classes('w-full gap-4'):
            with ui.card().classes('flex-1'):
                ui.label('Battery').classes('text-sm text-gray-500')
                battery_label = ui.label('--').classes('text-3xl font-bold')

            with ui.card().classes('flex-1'):
                ui.label('Charge State').classes('text-sm text-gray-500')
                state_label = ui.label('--').classes('text-xl')

            with ui.card().classes('flex-1'):
                ui.label('Charge Limit').classes('text-sm text-gray-500')
                limit_label = ui.label('--').classes('text-xl')

        with ui.card().classes('w-full'):
            ui.label('Active Mode').classes('text-sm text-gray-500')
            mode_label = ui.label('--').classes('text-xl font-semibold')
            error_label = ui.label('').classes('text-red-500 text-sm')

        # --- Action Buttons ---
        with ui.row().classes('w-full gap-2'):
            def on_refresh():
                try:
                    mgr.refresh_state()
                    mgr._log('Manual refresh — data updated')
                except Exception as e:
                    mgr._log(f'Refresh failed: {e}')

            ui.button('Refresh State', on_click=on_refresh).classes('bg-gray-600')

            def on_override():
                try:
                    mgr.manual_override = True
                    mgr.refresh_state()
                    mgr.set_charge_limit(WEEKEND_LIMIT)
                    mgr.start_charging()
                    mgr._log('Manual override — charging to 100%')
                except Exception as e:
                    mgr._log(f'Override failed: {e}')

            def on_cancel_override():
                try:
                    mgr.manual_override = False
                    mgr.set_charge_limit(WEEKDAY_LIMIT)
                    mgr._log('Manual override cancelled, reset to 80%')
                except Exception as e:
                    mgr._log(f'Cancel override failed: {e}')

            override_btn = ui.button('Charge to 100% Now', on_click=on_override).classes('bg-blue-600')
            cancel_btn = ui.button('Cancel Override', on_click=on_cancel_override).classes('bg-red-600')

        # --- Schedule a Charge ---
        ui.label('Schedule a 100% Charge').classes('text-lg font-semibold mt-4')
        with ui.row().classes('w-full gap-2 items-end'):
            date_input = ui.input('Date', placeholder='YYYY-MM-DD').classes('flex-1')
            with date_input:
                with ui.menu() as date_menu:
                    ui.date(on_change=lambda e: (date_input.set_value(e.value), date_menu.close()))
                with date_input.add_slot('append'):
                    ui.icon('edit_calendar').on('click', date_menu.open).classes('cursor-pointer')

            time_input = ui.input('Time', placeholder='HH:MM', value='20:00').classes('w-28')

            def on_schedule():
                try:
                    dt = datetime.strptime(f'{date_input.value} {time_input.value}', '%Y-%m-%d %H:%M')
                    if dt <= datetime.now():
                        mgr._log('Schedule failed — date/time is in the past')
                        return
                    mgr.scheduled_charges.append(dt)
                    mgr.scheduled_charges.sort()
                    mgr._save_scheduled_charges()
                    mgr._log(f'Scheduled 100% charge for {dt.strftime("%a %Y-%m-%d %H:%M")}')
                    date_input.set_value('')
                except ValueError:
                    mgr._log('Schedule failed — invalid date or time format')

            ui.button('Schedule', on_click=on_schedule).classes('bg-green-600')

        schedule_container = ui.column().classes('w-full gap-1')

        # --- Behaviour Info ---
        with ui.expansion('How charging works', icon='info').classes('w-full'):
            ui.markdown('''
**Monday — Friday before 8 PM (Daycare Protection)**
- Battery below 50%: charge limit set to 80%, car charges up to 80%
- Battery above 50%: charge limit capped to 50%, preventing unwanted charging

**Friday at 8:00 PM (Weekly LFP Calibration)**
- Charge limit set to 100% and charging starts immediately
- Slow charger has all night to reach full

**Saturday — Sunday (Weekend Hold)**
- Charge limit stays at 100%

**Sunday at 10:00 PM (Smart Reset)**
- If battery is at 100%, limit resets to 80% ready for Monday

**Manual Override**
- "Charge to 100% Now" overrides all rules and starts charging
- Automatically resets to 80% once the battery hits 100%

**Scheduled Charges**
- One-time 100% charges at a date/time you pick
- Behaves like a manual override once triggered

**How often does it check?**
- Every 30 minutes — the car is only woken when an action is needed
''').classes('text-sm')

        # --- Action Log ---
        ui.label('Action Log').classes('text-lg font-semibold mt-4')
        log_container = ui.column().classes('w-full max-h-96 overflow-y-auto gap-1')

        # --- Refresh timer ---
        def refresh_ui():
            batt = mgr.battery_level
            battery_label.text = f'{batt}%' if batt is not None else '--'
            state_label.text = mgr.charge_state
            limit_label.text = f'{mgr.charge_limit}%' if mgr.charge_limit is not None else '--'
            mode_label.text = mgr.active_mode
            error_label.text = mgr.last_error or ''

            override_btn.visible = not mgr.manual_override
            cancel_btn.visible = mgr.manual_override

            schedule_container.clear()
            with schedule_container:
                if not mgr.scheduled_charges:
                    ui.label('No scheduled charges').classes('text-sm text-gray-400')
                else:
                    for sc in mgr.scheduled_charges:
                        with ui.row().classes('items-center gap-2'):
                            ui.label(sc.strftime('%a %Y-%m-%d %H:%M')).classes('text-sm font-mono')
                            dt_to_remove = sc
                            ui.button(icon='delete', on_click=lambda _, d=dt_to_remove: (
                                mgr.scheduled_charges.remove(d),
                                mgr._save_scheduled_charges(),
                                mgr._log(f'Removed scheduled charge for {d.strftime("%a %Y-%m-%d %H:%M")}'),
                            )).props('flat dense size=sm color=red')

            log_container.clear()
            with log_container:
                for entry in mgr.action_log[:50]:
                    ui.label(entry).classes('text-sm font-mono')

        ui.timer(30, refresh_ui)
        refresh_ui()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
mgr = TeslaManager(TESLA_EMAIL)


@ui.page('/')
def index():
    build_ui(mgr)


app.on_startup(lambda: asyncio.create_task(charge_loop(mgr)))
ui.run(port=8080, host='0.0.0.0', title='Tesla Smart-Charge', reload=False)
