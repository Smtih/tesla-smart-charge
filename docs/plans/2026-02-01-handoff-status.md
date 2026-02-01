# Project Handoff: Tesla Smart-Charge Manager — OCI Restructure

## What This Project Is

A self-hosted Tesla charging optimization app (Python/NiceGUI) that:
- Streams real-time telemetry from the car via Tesla Fleet Telemetry (ZMQ)
- Manages battery health with a "Top-Off Guard" that prevents shallow charge cycles
- Supports scheduled 100% charges (e.g. weekend mornings)
- Runs as a PWA on phone/desktop

## What Was Done This Session

### 1. Fully Containerized the App
- Created `Dockerfile` for the Python app (previously ran via venv/nohup)
- Created `entrypoint.sh` to ensure writable files exist before app starts
- Updated `docker-compose.yml` from 2 services to 4:
  - `fleet-telemetry` — Tesla vehicle WebSocket on port 443
  - `public-key-server` — nginx serving Tesla virtual key on port 8443
  - `app` — Python/NiceGUI dashboard (internal only, no host port)
  - `nginx` — TLS reverse proxy on port 8080, proxies to app

### 2. Added HTTPS
- Created `nginx-app.conf` — reverse proxy with TLS termination and WebSocket support
- App is now accessible at `https://smtihtesla.duckdns.org:8080`
- Uses existing Let's Encrypt certs (shared across containers)

### 3. Added Authentication
- **OAuth account restriction**: After token exchange, calls `/api/1/users/me` to verify email matches `smith.w.da@gmail.com`. Rejects all other accounts.
- **Browser sessions**: 30-day session via NiceGUI `app.storage.user` with secure cookie. Login once, stay logged in for a month.
- **Manual code-paste flow**: Tesla Developer Portal rejects DuckDNS domains for redirect URIs. Using `https://auth.tesla.com/void/callback` — user clicks Tesla login, copies code from URL, pastes into app.

### 4. Made ZMQ Endpoint Configurable
- `ZMQ_ENDPOINT` now reads from env var, defaults to `tcp://localhost:5284`
- Docker Compose sets it to `tcp://fleet-telemetry:5284` for container networking

### 5. GitHub Actions CD
- `.github/workflows/deploy.yml` — auto-deploys on push to `main`
- SSH into OCI VM → git pull → docker compose build app → docker compose up -d
- GitHub repo secrets configured: `OCI_SSH_KEY`, `OCI_HOST`

### 6. Repo Cleanup
- Moved OCI scripts and cert setup to `scripts/`
- Committed all config files (fleet-telemetry, nginx, requirements.txt)
- Committed design docs to `docs/plans/`
- Added `.dockerignore`

### 7. Server Setup
- `~/tesla-app` on OCI VM is now a proper git clone (was manually copied files before)
- Secrets backed up to `~/tesla-backup/` and restored
- All 4 containers running

## Current State

### What's Working
- All 4 Docker containers are up on OCI VM (`168.138.10.36`)
- GitHub Actions deploy pipeline working (triggers on push to main)
- HTTPS via nginx reverse proxy on port 8080
- Fleet telemetry receiving vehicle data on port 443

### What Needs Testing / May Need Fixing
- **OAuth login flow**: The redirect URI was changed to `https://auth.tesla.com/void/callback`. The manual code-paste auth UI (`build_auth_ui`) should appear when visiting the app. This needs to be tested end-to-end — visit `https://smtihtesla.duckdns.org:8080`, go through OAuth, paste code, verify dashboard loads.
- **Token restoration**: The existing `token.json` was backed up and restored. If it has valid tokens, the app should auto-authenticate on startup. But the browser session won't exist yet, so the auth UI will show. The user needs to re-auth once to establish a browser session.
- **NiceGUI behind proxy**: NiceGUI uses WebSockets. The nginx config has WebSocket upgrade headers, but if NiceGUI generates `ws://` URLs instead of `wss://`, the browser will block mixed content. May need to pass `--forwarded-secret` or configure NiceGUI to trust the proxy. Watch for WebSocket connection errors in browser console.

### Key Files
| File | Purpose |
|------|---------|
| `main.py` | Entire app — auth, telemetry, charge loop, UI |
| `Dockerfile` | Containerizes the Python app |
| `docker-compose.yml` | 4-service stack definition |
| `nginx-app.conf` | TLS reverse proxy for app |
| `nginx.conf` | Public key server config |
| `entrypoint.sh` | Ensures writable files exist |
| `.github/workflows/deploy.yml` | CD pipeline |
| `ORACLE-VM.md` | Full VM setup documentation |
| `docs/plans/2026-02-01-oci-restructure-design.md` | Architecture design |
| `docs/plans/2026-02-01-oci-restructure-implementation.md` | Implementation plan |

### Secrets (on server only, never in git)
| File | Location on VM |
|------|---------------|
| `.env` | `~/tesla-app/.env` — contains `TESLA_CLIENT_SECRET`, `DUCKDNS_TOKEN` |
| `private-key.pem` | `~/tesla-app/private-key.pem` — Tesla Fleet API command signing |
| `public-key.pem` | `~/tesla-app/public-key.pem` — Tesla virtual key verification |
| `token.json` | `~/tesla-app/token.json` — OAuth tokens (written by app) |
| `certs/` | `~/tesla-app/certs/` — Let's Encrypt TLS certs |

### SSH Access
```bash
ssh -i "C:/Users/smith/.oci/tesla-vm-key" ubuntu@168.138.10.36
```

### Useful Commands on VM
```bash
cd ~/tesla-app
sudo docker compose ps          # Check containers
sudo docker compose logs -f app # App logs
sudo docker compose restart app # Restart app
sudo docker compose build app && sudo docker compose up -d  # Rebuild
```

### Git State
- Branch: `main`
- Remote: `https://github.com/Smtih/tesla-smart-charge.git`
- Last commit: `fix: use Tesla void callback for OAuth redirect URI`
- All changes pushed to origin

## Next Steps
1. Test the OAuth login flow end-to-end on the live server
2. Verify WebSocket connectivity works through nginx (NiceGUI reactive UI)
3. If WebSocket issues, investigate NiceGUI proxy configuration
4. Remove the `version: "3.8"` from docker-compose.yml (Docker warns it's obsolete)
5. Consider cert auto-renewal strategy (current certs are manually generated)
