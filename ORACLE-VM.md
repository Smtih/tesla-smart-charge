# Oracle Cloud VM Setup

## Instance Details
- **Name**: tesla-telemetry-amd
- **Shape**: VM.Standard.E2.1.Micro (Always Free)
- **OS**: Ubuntu 24.04 Minimal (x86_64)
- **Region**: ap-melbourne-1
- **Availability Domain**: YbPA:AP-MELBOURNE-1-AD-1
- **Public IP**: 168.138.10.36
- **Tailscale IP**: 100.83.209.126
- **Instance OCID**: ocid1.instance.oc1.ap-melbourne-1.anwwkljrdqk7kvycs6lqai7df4ihfgue7dotwxsqrbt7jqkdwsnowwus7rda

## Oracle Cloud IDs
- **Tenancy OCID**: ocid1.tenancy.oc1..aaaaaaaa4w24kbdlfhu7czwdhyf5kr4tjvzk3zt6krwtx5x7hgfhttoyxosq
- **User OCID**: ocid1.user.oc1..aaaaaaaayrzlavwzcapkylwpu2kgdbqxwtaxfyw5ytgvkyypkzyergaj6esq
- **VCN OCID**: ocid1.vcn.oc1.ap-melbourne-1.amaaaaaadqk7kvyaqushjiqvqjqhyhlctdocj2vbmp2dq42onkgdoj4e5daa
- **Subnet OCID**: ocid1.subnet.oc1.ap-melbourne-1.aaaaaaaauz6nhxmr42edyxodmknibdvym7x7otsj6ahe3j6rocquhu6ztqua
- **Security List OCID**: ocid1.securitylist.oc1.ap-melbourne-1.aaaaaaaaaufya2h5oc6vp5iqtjw4yk6cdnhfsc4d2pduych7fyecwwe462lq
- **Internet Gateway OCID**: ocid1.internetgateway.oc1.ap-melbourne-1.aaaaaaaakaipkkfttqkbaphwvuom5mzgjbqusl34hpgtb4ybspbp4cuvo7ra
- **Route Table OCID**: ocid1.routetable.oc1.ap-melbourne-1.aaaaaaaaemmm6avp3uh7jzutlp3ixxavgbazobxc4p5frl7enzclrhg37obq

## SSH Access
```bash
ssh -i "C:/Users/smith/.oci/tesla-vm-key" ubuntu@168.138.10.36
```
- **SSH key**: C:/Users/smith/.oci/tesla-vm-key (ed25519)
- **User**: ubuntu

## OCI CLI Config
- **Config file**: C:/Users/smith/.oci/config
- **API key**: C:/Users/smith/.oci/oci_api_key.pem
- **API public key**: C:/Users/smith/.oci/oci_api_key_public.pem
- **API key fingerprint**: 22:dc:d4:a6:26:10:00:80:45:f2:f4:f3:57:04:b0:1e

## Network Security Rules (Ingress)
| Port | Protocol | Purpose |
|------|----------|---------|
| 22   | TCP      | SSH |
| 443  | TCP      | Fleet telemetry (vehicle WebSocket) |
| 8443 | TCP      | Public key server (nginx) |
| 8080 | TCP      | NiceGUI dashboard |

## OS Firewall (iptables)
- All Tailscale interface traffic allowed (`-i tailscale0 -j ACCEPT`)
- Ports 443, 8443, 8080 open from all sources
- Rules persisted to /etc/iptables/rules.v4

## Swap
- 2GB swap file at /swapfile (VM only has 1GB RAM)

## What's Running on the VM

### Docker Containers (all 4 via docker compose)
```bash
cd ~/tesla-app && sudo docker compose up -d
```

| Container | Image | Ports | Purpose |
|-----------|-------|-------|---------|
| fleet-telemetry | tesla/fleet-telemetry:latest | 443 | Vehicle WebSocket (TLS) |
| public-key-server | nginx:alpine | 8443 | Tesla virtual key verification |
| app | Custom (Dockerfile) | internal | NiceGUI dashboard + charge loop |
| nginx | nginx:alpine | 8080 | TLS reverse proxy to app |

- App connects to fleet-telemetry via Docker internal network (ZMQ tcp://fleet-telemetry:5284)
- Nginx terminates TLS and proxies to app on port 8080
- Browser access: https://smtihtesla.duckdns.org:8080

### Tailscale
- Authenticated to smith.w.da@ account
- VM hostname: tesla-telemetry-amd

## Deployment

### Auto-Deploy (GitHub Actions)
Every push to `main` triggers `.github/workflows/deploy.yml`:
1. SSH into OCI VM
2. `git pull`
3. `docker compose build app`
4. `docker compose up -d`

**GitHub repo secrets required:**
- `OCI_SSH_KEY`: Contents of `C:/Users/smith/.oci/tesla-vm-key`
- `OCI_HOST`: `168.138.10.36`

### Manual Deploy
```bash
ssh -i "C:/Users/smith/.oci/tesla-vm-key" ubuntu@168.138.10.36
cd ~/tesla-app && git pull && sudo docker compose build app && sudo docker compose up -d
```

### First-Time Setup
1. Clone repo to `~/tesla-app`
2. SCP secrets to server:
   ```bash
   scp -i ~/.oci/tesla-vm-key .env ubuntu@168.138.10.36:~/tesla-app/.env
   scp -i ~/.oci/tesla-vm-key private-key.pem ubuntu@168.138.10.36:~/tesla-app/private-key.pem
   ```
3. Ensure certs exist in `certs/` (from `setup-certs.py`)
4. Create empty data files: `touch token.json scheduled_charges.json`
5. `sudo docker compose up -d`
6. Visit `https://smtihtesla.duckdns.org:8080` and complete OAuth

### Secrets on Server (never in git)
| File | Purpose |
|------|---------|
| `.env` | Tesla client secret, DuckDNS token |
| `private-key.pem` | Tesla Fleet API command signing key |
| `token.json` | OAuth tokens (written by app at runtime) |
| `certs/` | TLS certificates (Let's Encrypt) |

## DuckDNS
- Domain: smtihtesla.duckdns.org
- Pointed to VM IP: 168.138.10.36
- Token: stored in .env (DUCKDNS_TOKEN)

## TLS Certs
- Located at ~/tesla-app/certs/fullchain.pem and privkey.pem
- Issued by Let's Encrypt for smtihtesla.duckdns.org
- Generated locally via setup-certs.py using DNS-01 challenge with DuckDNS

## File Layout on VM
```
~/tesla-app/
├── main.py
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── nginx.conf               # public-key-server config
├── nginx-app.conf            # app reverse proxy config
├── requirements.txt
├── .env                      # secrets (not in git)
├── private-key.pem           # Tesla API key (not in git)
├── public-key.pem
├── token.json                # OAuth tokens (not in git)
├── scheduled_charges.json    # charge schedules (not in git)
├── smart-charge.log
├── certs/
│   ├── fullchain.pem
│   └── privkey.pem
├── fleet-telemetry/
│   └── config.json
├── static/
│   └── (favicon, manifest, icons)
└── .github/
    └── workflows/
        └── deploy.yml
```

## Useful Commands
```bash
# Check all containers
sudo docker compose ps
sudo docker compose logs -f app
sudo docker compose logs -f fleet-telemetry

# Restart app only
sudo docker compose restart app

# Rebuild and restart app (after code changes)
sudo docker compose build app && sudo docker compose up -d

# Full rebuild (all containers)
sudo docker compose down && sudo docker compose build --no-cache && sudo docker compose up -d

# Check memory
free -m

# Check Tailscale
sudo tailscale status
```
