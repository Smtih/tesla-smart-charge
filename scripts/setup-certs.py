"""
Obtain a Let's Encrypt TLS cert for a DuckDNS domain via DNS-01 challenge.
No admin rights needed. Uses the acme + josepy libraries (installed with certbot).
"""
import datetime, json, os, sys, time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import josepy as jose
from acme import client as acme_client, messages, challenges, crypto_util
import requests


def _make_key_pem(bits=2048):
    """Generate an RSA private key and return PEM bytes."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )

# ---- Config ----
DOMAIN = 'smtih'
FQDN = f'{DOMAIN}.duckdns.org'
PROJECT_DIR = Path(__file__).resolve().parent.parent
CERTS_DIR = PROJECT_DIR / 'certs'
ACME_DIR = 'https://acme-v02.api.letsencrypt.org/directory'
EMAIL = 'smith.w.da@gmail.com'

env_file = PROJECT_DIR / '.env'
DUCKDNS_TOKEN = ''
for line in env_file.read_text().splitlines():
    if line.startswith('DUCKDNS_TOKEN='):
        DUCKDNS_TOKEN = line.split('=', 1)[1].strip()
if not DUCKDNS_TOKEN:
    sys.exit('ERROR: DUCKDNS_TOKEN not found in .env')


def main():
    print(f'=== Let\'s Encrypt certificate for {FQDN} ===\n')

    # 1. Generate account key
    print('[1/8] Generating ACME account key...')
    account_key_pem = _make_key_pem()
    account_key = jose.JWKRSA(
        key=serialization.load_pem_private_key(account_key_pem, password=None)
    )

    # 2. Register account
    print('[2/8] Registering with Let\'s Encrypt...')
    directory = messages.Directory.from_json(requests.get(ACME_DIR).json())
    net = acme_client.ClientNetwork(account_key, user_agent='tesla-smart-charge/1.0')
    acme = acme_client.ClientV2(directory, net)
    reg = messages.NewRegistration.from_data(email=EMAIL, terms_of_service_agreed=True)
    try:
        acme.new_account(reg)
    except Exception as e:
        if 'already' in str(e).lower():
            print('  Account already exists, continuing.')
        else:
            raise
    print('  Account ready.')

    # 3. Generate domain private key + CSR
    print('[3/8] Generating domain key and CSR...')
    domain_key_pem = _make_key_pem()
    csr_pem = crypto_util.make_csr(domain_key_pem, [FQDN])

    # 4. Create new order
    print(f'[4/8] Requesting certificate for {FQDN}...')
    order = acme.new_order(csr_pem)

    # 5. Find DNS-01 challenge
    print('[5/8] Finding DNS-01 challenge...')
    authz = order.authorizations[0]
    dns_challenge = None
    for chall_body in authz.body.challenges:
        if isinstance(chall_body.chall, challenges.DNS01):
            dns_challenge = chall_body
            break
    if not dns_challenge:
        sys.exit('ERROR: No DNS-01 challenge offered')

    # 6. Set DuckDNS TXT record
    validation = dns_challenge.validation(account_key)
    print(f'[6/8] Setting DuckDNS TXT record: {validation[:20]}...')
    url = f'https://www.duckdns.org/update?domains={DOMAIN}&token={DUCKDNS_TOKEN}&txt={validation}'
    resp = requests.get(url)
    if resp.text.strip() != 'OK':
        sys.exit(f'ERROR: DuckDNS update failed: {resp.text}')
    print('  TXT record set. Waiting 30s for DNS propagation...')
    time.sleep(30)

    # 7. Answer challenge and finalize
    print('[7/8] Answering challenge...')
    acme.answer_challenge(dns_challenge, dns_challenge.response(account_key))

    deadline = datetime.datetime.now() + datetime.timedelta(minutes=5)
    print('  Polling for validation (up to 5 min)...')
    finalized = acme.poll_and_finalize(order, deadline)

    # Clean up TXT record
    requests.get(f'https://www.duckdns.org/update?domains={DOMAIN}&token={DUCKDNS_TOKEN}&txt=&clear=true')

    # 8. Save certificates
    print('[8/8] Saving certificates...')
    CERTS_DIR.mkdir(exist_ok=True)

    (CERTS_DIR / 'privkey.pem').write_bytes(domain_key_pem)
    (CERTS_DIR / 'fullchain.pem').write_text(finalized.fullchain_pem)

    print(f'\n=== Done! ===')
    print(f'  {CERTS_DIR / "privkey.pem"}')
    print(f'  {CERTS_DIR / "fullchain.pem"}')


if __name__ == '__main__':
    main()
