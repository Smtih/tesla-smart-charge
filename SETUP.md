# Fleet Telemetry Setup Guide

Self-hosted Tesla Fleet Telemetry on your home network. The car streams battery/charge data over WebSocket to a local Docker server, eliminating polling and vehicle wakes.

## Prerequisites

- Docker Desktop installed
- Router with port forwarding access
- Tesla app on your phone (for virtual key pairing)

## 1. DuckDNS Free Domain

1. Go to [duckdns.org](https://www.duckdns.org/) and sign in
2. Create a subdomain (e.g. `smtihtesla.duckdns.org`)
3. It will auto-detect your public IP
4. Set up the DuckDNS update script to keep your IP current:

```powershell
# Add to Windows Task Scheduler (every 5 minutes)
Invoke-RestMethod "https://www.duckdns.org/update?domains=smtihtesla&token=YOUR_DUCKDNS_TOKEN&ip="
```

## 2. Let's Encrypt TLS Certificate

Install Certbot and get a certificate for your DuckDNS domain:

```bash
# Using certbot with manual DNS challenge
certbot certonly --manual --preferred-challenges dns -d smtihtesla.duckdns.org
```

When prompted, create a TXT record at `_acme-challenge.smtihtesla.duckdns.org`. DuckDNS supports this via:

```
https://www.duckdns.org/update?domains=smtihtesla&token=YOUR_TOKEN&txt=THE_CHALLENGE_VALUE
```

Place the resulting certificates in the `certs/` directory:
- `certs/fullchain.pem`
- `certs/privkey.pem`

## 3. Router Port Forwarding

Forward these ports to your machine's local IP:
- **443** → 443 (fleet-telemetry WebSocket)
- **8443** → 8443 (public key server for Tesla developer verification)

## 4. Host Your Public Key

The `docker-compose.yml` includes an nginx container that serves `public-key.pem` at:

```
https://smtihtesla.duckdns.org:8443/.well-known/appspecific/com.tesla.3p.public-key.pem
```

Make sure this URL is accessible from the internet.

## 5. Tesla Developer App Registration

Your app is already registered with client ID `46b3b38b-c7c1-4015-9f6d-51bcaf2729b3`.

Update the app's **Allowed Origin** in the Tesla Developer portal to include your DuckDNS domain:
- `https://smtihtesla.duckdns.org`

Register the domain with Tesla's partner endpoint (one-time):

```bash
curl -X POST https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/partner_accounts \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"domain": "smtihtesla.duckdns.org"}'
```

## 6. Pair Virtual Key to Vehicle

Open this URL in the Tesla mobile app (tap the link on your phone):

```
https://tesla.com/_ak/smtihtesla.duckdns.org
```

Follow the prompts to authorize the virtual key on your vehicle.

## 7. Start Fleet Telemetry Server

```bash
cd "Tesla App"
docker compose up -d
```

Verify it's running:
```bash
docker compose logs fleet-telemetry
# Should show "listening on :443"
```

## 8. Configure Vehicle Telemetry

Send the telemetry configuration to your vehicle via the Fleet API:

```bash
curl -X POST "https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/vehicles/YOUR_VIN/fleet_telemetry_config" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "vins": ["YOUR_VIN"],
    "config": {
      "hostname": "smtihtesla.duckdns.org",
      "port": 443,
      "ca": "PASTE_FULLCHAIN_PEM_CONTENTS_HERE",
      "fields": {
        "Soc": {"interval_seconds": 60},
        "DetailedChargeState": {"interval_seconds": 10},
        "ChargeAmps": {"interval_seconds": 10},
        "ChargeLimitSoc": {"interval_seconds": 60}
      }
    }
  }'
```

Wait for the response to show `"synced": true`.

## 9. Verify Data Flow

1. Check fleet-telemetry logs for incoming vehicle connections:
   ```bash
   docker compose logs -f fleet-telemetry
   ```

2. The Python app will log telemetry updates:
   ```
   Telemetry update — battery 85%, state Charging, limit 100%
   ```

3. If no telemetry arrives for 2 hours, the app automatically falls back to a wake + poll.

## Troubleshooting

- **Car on home WiFi can't connect**: Tesla may block local IP connections. The car will stream over LTE instead. Enable NAT hairpin/loopback on your router if you want WiFi to work.
- **No data arriving**: Check `docker compose logs fleet-telemetry` for TLS errors. Verify your cert matches the domain.
- **Certificate expired**: Renew with `certbot renew` and restart Docker.
