# Tesla Smart-Charge Manager — Full Setup Log (2026-02-02)

This document records every step taken to get the Tesla Smart-Charge Manager running on an OCI VM with live telemetry from the vehicle.

---

## Infrastructure

- **VM**: OCI `tesla-telemetry-amd` at `168.138.10.36` (ap-melbourne-1)
- **OS**: Ubuntu (user `ubuntu`)
- **SSH**: `ssh -i ~/.oci/tesla-vm-key ubuntu@168.138.10.36`
- **Domain**: `smtih.duckdns.org` (DuckDNS, pointing to `168.138.10.36`)
- **VIN**: `LRWYHCFS2PC243983`

## Architecture

```
Internet
  │
  ├─ :443   → public-key-server (nginx) — serves /.well-known/appspecific/com.tesla.3p.public-key.pem
  ├─ :4443  → fleet-telemetry — receives vehicle WebSocket telemetry
  ├─ :8080  → nginx reverse proxy (TLS) → app (NiceGUI on :8080)
  └─ :4430  → vehicle-command-proxy — signs commands for Tesla Fleet API
```

All services run as Docker containers via `docker-compose.yml`.

---

## Step-by-Step Setup

### 1. Domain Setup (DuckDNS)

Created `smtih.duckdns.org` on [duckdns.org](https://www.duckdns.org) and pointed it to the VM's public IP `168.138.10.36`.

> **Note**: Originally used `smtihtesla.duckdns.org` but Tesla rejects domains containing "tesla". Migrated to `smtih.duckdns.org`.

Token stored in `.env`:
```
DUCKDNS_TOKEN=2c3d125-e854-4b3a-b82b-526fb1e2ac80
```

### 2. TLS Certificates (Let's Encrypt via DNS-01)

Port 80 is blocked by OCI, so we use DNS-01 challenge via DuckDNS.

```bash
python scripts/setup-certs.py
```

This script:
1. Generates an ACME account key
2. Registers with Let's Encrypt
3. Creates a domain key + CSR for `smtih.duckdns.org`
4. Sets a DuckDNS TXT record for DNS-01 validation
5. Answers the challenge and finalizes the certificate
6. Saves `certs/fullchain.pem` and `certs/privkey.pem`

Certificates expire every 90 days — re-run the script to renew.

### 3. EC Key Pair for Tesla Fleet API

Tesla requires an **EC prime256v1** key pair (RSA keys are rejected as "too large").

Generated directly on the VM:
```bash
openssl ecparam -name prime256v1 -genkey -noout -out private-key.pem
openssl ec -in private-key.pem -pubout -out public-key.pem
```

- `private-key.pem` — used by vehicle-command-proxy and the app to sign commands
- `public-key.pem` — served at `https://smtih.duckdns.org/.well-known/appspecific/com.tesla.3p.public-key.pem`

> **Important**: The local repo has stale RSA keys. The correct EC keys are only on the VM.

### 4. Tesla Developer Portal Configuration

At [developer.tesla.com](https://developer.tesla.com):

- **Client ID**: configured in `main.py` as `TESLA_CLIENT_ID`
- **Client Secret**: stored in `.env` as `TESLA_CLIENT_SECRET`
- **Allowed Origin**: `https://smtih.duckdns.org`
- **Redirect URI**: `https://auth.tesla.com/void/callback`
- **Public Key URL**: `https://smtih.duckdns.org/.well-known/appspecific/com.tesla.3p.public-key.pem`
- **Scopes enabled**: `openid`, `offline_access`, `vehicle_device_data`, `vehicle_cmds`, `vehicle_charging_cmds`, `vehicle_location`

### 5. Partner Account Registration

Tesla requires a partner registration before the virtual key can be installed or telemetry configured.

Done via a `client_credentials` token:
```bash
# Get partner token
curl -X POST "https://auth.tesla.com/oauth2/v3/token" \
  -d "grant_type=client_credentials&client_id=$CLIENT_ID&client_secret=$CLIENT_SECRET&scope=openid vehicle_device_data vehicle_cmds"

# Register partner
curl -X POST "https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/partner_accounts" \
  -H "Authorization: Bearer $PARTNER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"domain":"smtih.duckdns.org"}'
```

Tesla fetches the public key from the domain's `.well-known` path during registration.

### 6. Virtual Key Installation

The vehicle owner must install the third-party virtual key on the car:

1. Open this URL on a phone near the car: `https://tesla.com/_ak/smtih.duckdns.org`
2. Tap the key card on the center console when prompted
3. The key appears in the car's Settings > Locks > Keys

This allows the vehicle-command-proxy to send signed commands to the car.

### 7. OAuth Login Flow

The app uses Tesla's OAuth2 with the **void callback** pattern:

1. User clicks "Login with Tesla" → redirected to `https://auth.tesla.com/oauth2/v3/authorize?...`
2. After consent, Tesla redirects to `https://auth.tesla.com/void/callback?code=...`
3. User copies the `code` from the URL and pastes it back into the app
4. App exchanges the code for access + refresh tokens
5. Identity verified by decoding the JWT `id_token` and checking the `sub` claim matches the owner (`5bf3bacf-25b4-49de-90c6-99ff813f121b`)

Token is persisted to `token.json` (volume-mounted from host).

### 8. Telemetry Config Registration

Sent through the vehicle-command-proxy (required for signed commands):

```bash
ACCESS_TOKEN=$(python3 -c "import json; print(json.load(open('token.json'))['access_token'])")
CA_CERT=$(cat certs/fullchain.pem)

python3 -c "
import json, urllib.request, ssl
ca = open('certs/fullchain.pem').read()
token = json.load(open('token.json'))['access_token']
payload = json.dumps({
    'vins': ['LRWYHCFS2PC243983'],
    'config': {
        'hostname': 'smtih.duckdns.org',
        'port': 4443,
        'ca': ca,
        'fields': {
            'ChargeState': {'interval_seconds': 60},
            'BatteryLevel': {'interval_seconds': 60},
            'ChargeLimitSoc': {'interval_seconds': 60},
            'ChargeAmps': {'interval_seconds': 60},
            'ChargeCurrentRequest': {'interval_seconds': 60},
            'ChargePortLatch': {'interval_seconds': 300},
            'Odometer': {'interval_seconds': 300},
            'VehicleName': {'interval_seconds': 3600}
        },
        'alert_types': ['service']
    }
})
req = urllib.request.Request(
    'https://localhost:4430/api/1/vehicles/fleet_telemetry_config',
    data=payload.encode(),
    headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
    method='POST'
)
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
resp = urllib.request.urlopen(req, context=ctx)
print(resp.read().decode())
"
```

Response: `{"response":{"updated_vehicles":1}}`

> **Note**: `Location` field was excluded because it requires the `vehicle_location` OAuth scope, which Tesla's consent screen wasn't granting despite being requested. All charging-related fields work without it.

### 9. OCI Security List (Firewall)

The VM's subnet (`tesla-public-subnet`) uses security list `Default Security List for tesla-vcn`. Ingress rules were added via OCI CLI:

```bash
oci network security-list update \
  --security-list-id ocid1.securitylist.oc1.ap-melbourne-1.aaaaaaaaaufya2h5oc6vp5iqtjw4yk6cdnhfsc4d2pduych7fyecwwe462lq \
  --ingress-security-rules '[
    {"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":22,"max":22}}},
    {"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":443,"max":443}}},
    {"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":4443,"max":4443}}},
    {"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":8080,"max":8080}}},
    {"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":8443,"max":8443}}}
  ]' --force
```

Ports open:
| Port | Service |
|------|---------|
| 22 | SSH |
| 443 | Public key server (nginx) |
| 4443 | Fleet telemetry (vehicle WebSocket) |
| 8080 | App (nginx → NiceGUI) |
| 8443 | Legacy (unused, can be removed) |

iptables on the VM also has port 4443 allowed.

---

## Docker Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `fleet-telemetry` | `tesla/fleet-telemetry:latest` | 4443 | Receives vehicle telemetry via WebSocket, publishes to ZMQ |
| `public-key-server` | `nginx:alpine` | 443 | Serves the EC public key at `.well-known` path |
| `vehicle-command-proxy` | `tesla/vehicle-command:latest` | 4430 | Signs and forwards commands to Tesla Fleet API |
| `app` | Built from `Dockerfile` | 8080 (internal) | NiceGUI smart-charge manager |
| `nginx` | `nginx:alpine` | 8080 (external) | TLS reverse proxy to the app |

### Data Flow

```
Tesla Vehicle
  ↓ WebSocket (mTLS on :4443)
fleet-telemetry
  ↓ ZMQ (tcp://fleet-telemetry:5284)
app (main.py)
  ↓ reads telemetry, manages charge schedules
  ↓ sends commands via vehicle-command-proxy (:4430)
Tesla Fleet API
  ↓
Vehicle
```

---

## Common Operations

### Deploy code changes
```bash
ssh -i ~/.oci/tesla-vm-key ubuntu@168.138.10.36
cd ~/tesla-app
git pull
docker compose build --no-cache app
docker compose up -d app
```

### Renew TLS certificates
```bash
# On local machine
python scripts/setup-certs.py
# Then copy certs to VM or push via git and pull
```

### Check telemetry is flowing
```bash
docker compose logs -f fleet-telemetry
# Look for "socket_connected" with user_agent "Hermes/... (vehicle_device)"
```

### Check app logs
```bash
docker compose logs -f app
# Look for "Telemetry update" lines
```

### Re-register telemetry config
Needed if fields change or token expires. Run the Python script from Step 8 on the VM.

---

## Bugs Encountered and Fixed

### Auth: `'NoneType' object has no attribute 'get'`
- **Cause**: Token exchange used a throwaway `aiohttp.ClientSession()` instead of `self.session`, and `resp.json()` returned `None` due to content-type mismatch.
- **Fix**: Use `self.session` and pass `content_type=None` to `resp.json()`.

### Auth: Tesla `/users/me` returns `{"response": null}`
- **Cause**: The `user_data` scope isn't available. `(data.get('response') or {})` was needed instead of `data.get('response', {})` because the key exists with value `None`.
- **Fix**: Removed `/users/me` call entirely. Identity is now verified via JWT `id_token` `sub` claim.

### Docker container not updating after git pull
- **Cause**: Docker build cache serving old layers.
- **Fix**: Always use `docker compose build --no-cache app`.

### Tesla partner registration: "public key too large"
- **Cause**: RSA 2048-bit key. Tesla requires EC prime256v1.
- **Fix**: Generated EC key pair with `openssl ecparam -name prime256v1`.

### Virtual key: "invalid domain name"
- **Cause**: Port number in the virtual key URL (`smtih.duckdns.org:8443`). Tesla requires port 443.
- **Fix**: Moved public-key-server to port 443.

### Telemetry config: "must use Vehicle Command HTTP Proxy"
- **Cause**: `fleet_telemetry_config` is a signed command that must go through the vehicle-command-proxy, not directly to Tesla's API.
- **Fix**: Added `vehicle-command-proxy` Docker service, sent config through `https://localhost:4430`.

### Telemetry config: "Unauthorized missing scopes vehicle_location"
- **Cause**: OAuth token didn't include `vehicle_location` scope.
- **Fix**: Removed `Location` field from telemetry config. All charging fields work without it.

### Port 4443 unreachable from internet
- **Cause**: OCI Security List didn't have an ingress rule for port 4443.
- **Fix**: Added TCP 4443 ingress rule via `oci network security-list update`.

---

## Files Reference

| File | Purpose |
|------|---------|
| `main.py` | Main application (NiceGUI, OAuth, charge scheduling, telemetry) |
| `docker-compose.yml` | All 5 services |
| `Dockerfile` | App container build |
| `.env` | `TESLA_CLIENT_SECRET`, `DUCKDNS_TOKEN` |
| `nginx.conf` | Public key server config (port 443) |
| `nginx-app.conf` | App reverse proxy config (port 8080 → app:8080) |
| `fleet-telemetry/config.json` | Fleet telemetry server config |
| `private-key.pem` | EC private key (signing commands) — **VM only, local is stale RSA** |
| `public-key.pem` | EC public key (served at .well-known) — **VM only** |
| `certs/fullchain.pem` | Let's Encrypt TLS cert chain |
| `certs/privkey.pem` | Let's Encrypt TLS private key |
| `token.json` | OAuth access/refresh tokens (auto-managed) |
| `scripts/setup-certs.py` | Let's Encrypt DNS-01 cert renewal |

## OCI Resources

| Resource | ID |
|----------|----|
| Instance | `ocid1.instance.oc1.ap-melbourne-1.anwwkljrdqk7kvycs6lqai7df4ihfgue7dotwxsqrbt7jqkdwsnowwus7rda` |
| Subnet | `ocid1.subnet.oc1.ap-melbourne-1.aaaaaaaauz6nhxmr42edyxodmknibdvym7x7otsj6ahe3j6rocquhu6ztqua` |
| Security List | `ocid1.securitylist.oc1.ap-melbourne-1.aaaaaaaaaufya2h5oc6vp5iqtjw4yk6cdnhfsc4d2pduych7fyecwwe462lq` |
