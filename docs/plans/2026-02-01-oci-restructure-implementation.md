# OCI Cloud Restructure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fully containerize the Tesla Smart-Charge Manager for OCI deployment with HTTPS, account-restricted auth, and GitHub Actions CD.

**Architecture:** 4-container Docker Compose stack (app, fleet-telemetry, nginx reverse proxy, public-key-server). Nginx terminates TLS and proxies to the NiceGUI app. OAuth restricted to owner's email. GitHub Actions SSH-deploys on push to main.

**Tech Stack:** Python 3.12, NiceGUI, Docker Compose, Nginx, Let's Encrypt TLS, GitHub Actions, Tesla Fleet API

**Design doc:** `docs/plans/2026-02-01-oci-restructure-design.md`

---

### Task 1: Create .dockerignore

**Files:**
- Create: `.dockerignore`

**Step 1: Create the file**

```
.env
token.json
scheduled_charges.json
private-key.pem
public-key.pem
*.pem
*.log
certs/
venv/
.venv/
.git/
.gitignore
__pycache__/
nul
docs/
*.md
fleet-telemetry/
nginx.conf
nginx-app.conf
docker-compose.yml
oci-create-*.sh
setup-certs.*
ORACLE-VM.md
SETUP.md
```

**Step 2: Commit**

```bash
git add .dockerignore
git commit -m "chore: add .dockerignore to exclude secrets and non-app files from image"
```

---

### Task 2: Create Dockerfile

**Files:**
- Create: `Dockerfile`

**Step 1: Create the Dockerfile**

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

**Step 2: Verify it builds**

Run: `docker build -t tesla-app .`
Expected: Successful build, image created

**Step 3: Commit**

```bash
git add Dockerfile
git commit -m "feat: add Dockerfile for containerized Python app"
```

---

### Task 3: Create nginx reverse proxy config

**Files:**
- Create: `nginx-app.conf`

**Step 1: Create the nginx config**

```nginx
server {
    listen 443 ssl;
    ssl_certificate     /etc/nginx/ssl/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/privkey.pem;

    location / {
        proxy_pass http://app:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support (required by NiceGUI)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }
}
```

**Step 2: Commit**

```bash
git add nginx-app.conf
git commit -m "feat: add nginx reverse proxy config with TLS and WebSocket support"
```

---

### Task 4: Update docker-compose.yml

**Files:**
- Modify: `docker-compose.yml` (replace entire file)

**Step 1: Rewrite docker-compose.yml with all 4 services**

```yaml
version: "3.8"

services:
  fleet-telemetry:
    image: tesla/fleet-telemetry:latest
    ports:
      - "443:443"
    volumes:
      - ./fleet-telemetry/config.json:/etc/fleet-telemetry/config.json:ro
      - ./certs:/certs:ro
    restart: unless-stopped

  public-key-server:
    image: nginx:alpine
    ports:
      - "8443:443"
    volumes:
      - ./public-key.pem:/usr/share/nginx/html/.well-known/appspecific/com.tesla.3p.public-key.pem:ro
      - ./certs/fullchain.pem:/etc/nginx/ssl/fullchain.pem:ro
      - ./certs/privkey.pem:/etc/nginx/ssl/privkey.pem:ro
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
    restart: unless-stopped

  app:
    build: .
    volumes:
      - ./.env:/app/.env:ro
      - ./private-key.pem:/app/private-key.pem:ro
      - ./token.json:/app/token.json
      - ./scheduled_charges.json:/app/scheduled_charges.json
      - ./smart-charge.log:/app/smart-charge.log
    environment:
      - ZMQ_ENDPOINT=tcp://fleet-telemetry:5284
      - TESLA_REDIRECT_URI=https://smtihtesla.duckdns.org:8080/auth/callback
    depends_on:
      - fleet-telemetry
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    ports:
      - "8080:443"
    volumes:
      - ./certs/fullchain.pem:/etc/nginx/ssl/fullchain.pem:ro
      - ./certs/privkey.pem:/etc/nginx/ssl/privkey.pem:ro
      - ./nginx-app.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on:
      - app
    restart: unless-stopped
```

Key changes from the original:
- `fleet-telemetry` no longer exposes port 5284 to host (only internal network)
- `app` service added — built from Dockerfile, bind-mounts all persistent data
- `ZMQ_ENDPOINT` env var points to `fleet-telemetry:5284` (Docker DNS)
- `TESLA_REDIRECT_URI` updated to HTTPS URL
- `nginx` service added — terminates TLS on host port 8080, proxies to app
- `token.json` and `scheduled_charges.json` mounted read-write (app writes to them)

**Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add app and nginx services for fully containerized stack"
```

---

### Task 5: Make ZMQ endpoint configurable in main.py

**Files:**
- Modify: `main.py:26`

**Step 1: Update the ZMQ_ENDPOINT constant**

Change line 26 from:
```python
ZMQ_ENDPOINT = 'tcp://localhost:5284'  # Fleet telemetry ZMQ publisher
```
To:
```python
ZMQ_ENDPOINT = os.environ.get('ZMQ_ENDPOINT', 'tcp://localhost:5284')
```

This picks up the `ZMQ_ENDPOINT=tcp://fleet-telemetry:5284` env var from docker-compose, and falls back to localhost for local dev.

**Step 2: Commit**

```bash
git add main.py
git commit -m "feat: make ZMQ endpoint configurable via env var for Docker networking"
```

---

### Task 6: Update TESLA_REDIRECT_URI default

**Files:**
- Modify: `main.py:74`

**Step 1: Update the default redirect URI to HTTPS**

Change line 74 from:
```python
TESLA_REDIRECT_URI = os.environ.get('TESLA_REDIRECT_URI', 'http://168.138.10.36:8080/auth/callback')
```
To:
```python
TESLA_REDIRECT_URI = os.environ.get('TESLA_REDIRECT_URI', 'https://smtihtesla.duckdns.org:8080/auth/callback')
```

**Step 2: Commit**

```bash
git add main.py
git commit -m "feat: update default redirect URI to HTTPS DuckDNS domain"
```

---

### Task 7: Restrict OAuth to owner's account

**Files:**
- Modify: `main.py` — `complete_auth` method (lines 227-256)

**Step 1: Add email verification after token exchange**

After the token exchange succeeds and before calling `_setup_vehicle()`, add a check that fetches the user's email from the Tesla API and rejects non-owner accounts.

In the `complete_auth` method, after line 251 (`self.oauth.expires = ...`) and before line 253 (`save_tokens(...)`), insert:

```python
            # Verify the authenticated user is the owner
            async with self.session.get(
                'https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/users/me',
                headers={'Authorization': f'Bearer {self.oauth._access_token}'},
            ) as me_resp:
                me_data = await me_resp.json()
                user_email = me_data.get('response', {}).get('email', '')
                if user_email.lower() != TESLA_EMAIL.lower():
                    self.oauth._access_token = None
                    self.oauth.refresh_token = None
                    raise RuntimeError(f'Access denied — account {user_email} is not authorized')
```

**Step 2: Commit**

```bash
git add main.py
git commit -m "feat: restrict OAuth login to owner's Tesla account only"
```

---

### Task 8: Add browser session management

**Files:**
- Modify: `main.py` — `index` route (lines 797-817) and `auth_callback` route (lines 820-831)

**Step 1: Add session check to the index route**

Replace the `index` function (lines 797-817) with:

```python
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
            ui.navigate.to(mgr.get_login_url(), new_tab=False)
    else:
        # No valid session — redirect to Tesla OAuth
        app.storage.user.clear()
        if not mgr.authenticated:
            ui.navigate.to(mgr.get_login_url(), new_tab=False)
        else:
            # Server is authenticated but browser has no session
            # (e.g. new browser or session expired) — re-auth required
            ui.navigate.to(mgr.get_login_url(), new_tab=False)
```

**Step 2: Set session cookie after successful auth callback**

Replace the `auth_callback` function (lines 820-831) with:

```python
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
```

**Step 3: Add storage secret to ui.run()**

NiceGUI's `app.storage.user` requires a `storage_secret`. Update the `ui.run()` call at line 866:

```python
ui.run(
    port=8080,
    host='0.0.0.0',
    title='Tesla Smart-Charge',
    reload=False,
    favicon='⚡',
    storage_secret=hashlib.sha256(TESLA_CLIENT_SECRET.encode()).hexdigest(),
)
```

This derives the storage secret from the client secret (already imported `hashlib` at line 17).

**Step 4: Commit**

```bash
git add main.py
git commit -m "feat: add 30-day browser sessions with NiceGUI storage"
```

---

### Task 9: Ensure persistent files exist before Docker mount

**Files:**
- Modify: `docker-compose.yml` (minor note)

Docker bind mounts will fail if the host file doesn't exist for a file mount (as opposed to a directory mount). The `token.json`, `scheduled_charges.json`, and `smart-charge.log` files may not exist on a fresh deploy.

**Step 1: Add an entrypoint script**

Create `entrypoint.sh`:

```bash
#!/bin/sh
# Ensure writable files exist (Docker bind mounts require them)
touch /app/token.json /app/scheduled_charges.json /app/smart-charge.log
exec "$@"
```

**Step 2: Update Dockerfile to use entrypoint**

Add before `CMD`:

```dockerfile
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh
ENTRYPOINT ["./entrypoint.sh"]
```

**Step 3: Commit**

```bash
git add entrypoint.sh Dockerfile
git commit -m "feat: add entrypoint to ensure writable files exist before app starts"
```

---

### Task 10: Create GitHub Actions deploy workflow

**Files:**
- Create: `.github/workflows/deploy.yml`

**Step 1: Create the workflow file**

```yaml
name: Deploy to OCI

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.OCI_HOST }}
          username: ubuntu
          key: ${{ secrets.OCI_SSH_KEY }}
          script: |
            cd ~/tesla-app
            git pull
            sudo docker compose build app
            sudo docker compose up -d
```

**Step 2: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "feat: add GitHub Actions workflow for auto-deploy to OCI on push"
```

**Step 3: Document required GitHub secrets**

The user needs to add these in GitHub repo settings > Secrets:
- `OCI_SSH_KEY`: Contents of `C:/Users/smith/.oci/tesla-vm-key`
- `OCI_HOST`: `168.138.10.36`

---

### Task 11: Update ORACLE-VM.md

**Files:**
- Modify: `ORACLE-VM.md`

**Step 1: Update the documentation**

Replace the "What's Running on the VM" section and "Useful Commands" section to reflect the fully containerized architecture:

**What's Running:**
- 4 Docker containers via `sudo docker compose up -d`
  1. `fleet-telemetry` — ports 443 (vehicle WebSocket)
  2. `public-key-server` — port 8443 (Tesla virtual key)
  3. `app` — internal only (NiceGUI dashboard)
  4. `nginx` — port 8080 (TLS reverse proxy to app)

**Useful Commands:**
```bash
# All containers
sudo docker compose ps
sudo docker compose logs -f app
sudo docker compose restart app
sudo docker compose build app && sudo docker compose up -d

# Full rebuild
sudo docker compose down && sudo docker compose build --no-cache && sudo docker compose up -d
```

**Deployment:**
- Auto-deploys via GitHub Actions on push to `main`
- Manual: `cd ~/tesla-app && git pull && sudo docker compose build app && sudo docker compose up -d`

**First-time setup:**
1. Clone repo to `~/tesla-app`
2. SCP secrets: `.env`, `private-key.pem`
3. Ensure certs exist in `certs/` (from `setup-certs.py`)
4. Ensure `token.json`, `scheduled_charges.json` exist (can be empty files)
5. `sudo docker compose up -d`
6. Visit `https://smtihtesla.duckdns.org:8080` and complete OAuth

**Step 2: Commit**

```bash
git add ORACLE-VM.md
git commit -m "docs: update ORACLE-VM.md for fully containerized architecture"
```

---

### Task 12: Final verification

**Step 1: Build all images locally**

Run: `docker compose build`
Expected: app image builds successfully

**Step 2: Verify .dockerignore works**

Run: `docker build -t tesla-app-test . && docker run --rm tesla-app-test ls -la /app/`
Expected: Only `main.py`, `requirements.txt`, `static/`, `entrypoint.sh` — no `.env`, no `*.pem`, no `certs/`

**Step 3: Push to GitHub and verify Actions workflow syntax**

The workflow should appear in the Actions tab. It won't deploy successfully until `OCI_SSH_KEY` and `OCI_HOST` secrets are configured.

**Step 4: Deploy to OCI**

```bash
ssh -i "C:/Users/smith/.oci/tesla-vm-key" ubuntu@168.138.10.36
cd ~/tesla-app && git pull
sudo docker compose down
sudo docker compose build
sudo docker compose up -d
sudo docker compose ps  # All 4 containers should be running
```

**Step 5: Test the app**

1. Visit `https://smtihtesla.duckdns.org:8080` — should redirect to Tesla OAuth
2. Complete OAuth — should land on dashboard
3. Close browser, reopen — should go straight to dashboard (session cookie)
4. Check fleet telemetry: `sudo docker compose logs -f fleet-telemetry`
