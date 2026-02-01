# OCI Cloud Restructure Design

## Problem

The Tesla Smart-Charge Manager is running on an Oracle Cloud VM but the Python app runs outside Docker (via venv/nohup), there's no HTTPS on the dashboard, no browser authentication, and no automated deployment. The app is publicly accessible on port 8080 over plain HTTP.

## Design

### Container Architecture

Four Docker containers orchestrated by `docker-compose.yml`:

| Service | Image | Host Port | Purpose |
|---------|-------|-----------|---------|
| `fleet-telemetry` | tesla/fleet-telemetry | 443 | Vehicle WebSocket + ZMQ publisher (5284 internal) |
| `public-key-server` | nginx:alpine | 8443 | Tesla virtual key verification |
| `nginx` | nginx:alpine | 8080 | TLS termination + reverse proxy to app |
| `app` | Custom Dockerfile | none (internal) | Python/NiceGUI dashboard |

**Dashboard traffic**: Browser -> HTTPS :8080 -> nginx -> HTTP app:8080 (internal)

**Telemetry traffic**: Vehicle -> TLS :443 -> fleet-telemetry -> ZMQ :5284 -> app (internal)

### HTTPS via Nginx Reverse Proxy

New `nginx-app.conf`:

- Listens on 443 SSL inside container, mapped to host port 8080
- Reuses existing Let's Encrypt certs (`certs/fullchain.pem`, `certs/privkey.pem`)
- Proxies to `http://app:8080` via Docker internal DNS
- WebSocket upgrade headers for NiceGUI's reactive UI
- `X-Forwarded-For` and `X-Forwarded-Proto` headers

App URL: `https://smtihtesla.duckdns.org:8080`

### Authentication

Two layers:

**1. OAuth account restriction (server-side)**

After OAuth token exchange, call Tesla `/api/1/users/me` to get the authenticated email. If it doesn't match `smith.w.da@gmail.com`, reject the login, delete tokens, show "Access denied."

**2. Browser session (client-side)**

- NiceGUI's `app.storage.user` with a secure cookie
- 30-day session expiry
- On page load: valid session -> dashboard, no session -> OAuth login
- Cookie is `Secure` + `HttpOnly` (behind HTTPS)

The server-side Tesla API tokens (`token.json`) are independent of browser sessions. The background charge loop runs 24/7 regardless of whether anyone has the browser open.

### Persistent Data

Bind-mounted from `~/tesla-app/` on the host into the app container:

| File | Purpose | Access |
|------|---------|--------|
| `.env` | Client secret, DuckDNS token | Read |
| `private-key.pem` | Signed vehicle commands | Read |
| `token.json` | OAuth tokens (auto-refreshed) | Read + Write |
| `scheduled_charges.json` | Charge schedules | Read + Write |
| `smart-charge.log` | Rotating app logs | Write |
| `certs/` | TLS certs (shared across containers) | Read |

None of these are baked into the Docker image (excluded via `.dockerignore`).

### Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY static/ static/
EXPOSE 8080
CMD ["python", "main.py"]
```

### GitHub Actions CD

On push to `main` (plus manual `workflow_dispatch`):

1. SSH into OCI VM
2. `cd ~/tesla-app && git pull`
3. `sudo docker compose build app`
4. `sudo docker compose up -d`

GitHub repo secrets: `OCI_SSH_KEY`, `OCI_HOST`

Uses `appleboy/ssh-action`. No Docker registry needed — image built on the server.

### Secrets Placement (One-Time via SCP)

```bash
scp -i ~/.oci/tesla-vm-key .env ubuntu@168.138.10.36:~/tesla-app/.env
scp -i ~/.oci/tesla-vm-key private-key.pem ubuntu@168.138.10.36:~/tesla-app/private-key.pem
```

Certs are already on the server from `setup-certs.py`.

## Files Changed

| File | Action |
|------|--------|
| `Dockerfile` | New |
| `docker-compose.yml` | Updated (add app + nginx services) |
| `nginx-app.conf` | New |
| `.dockerignore` | New |
| `.github/workflows/deploy.yml` | New |
| `main.py` | Updated (ZMQ endpoint configurable, OAuth email gate, browser sessions) |
| `ORACLE-VM.md` | Updated (new architecture, commands, setup steps) |

## Verification

1. `docker compose build` — all images build
2. `docker compose up -d` — all 4 containers start
3. `https://smtihtesla.duckdns.org:8080` — shows OAuth login
4. Login with owner account — works; other account — rejected
5. Close browser, reopen — session persists (no re-login for 30 days)
6. Push commit to `main` — GitHub Actions deploys automatically
7. Fleet telemetry still receives vehicle data on port 443
8. Public key still served at port 8443
