# Custodian store

Sells the Pro and Cloud plans through Stripe and delivers them, with no manual steps:

- **Pro** (self-hosted): payment issues a signed license key for a year; the buyer's order page
  shows it, and each yearly renewal's key appears on the same page.
- **Cloud** (hosted): payment also creates the buyer's own Custodian container with its own
  secrets, data volume and HTTPS subdomain. Renewals put the new key into it; a cancelled
  subscription stops the container and keeps the data.

Custodian is single-user by design, so Cloud runs one isolated container per customer rather
than sharing one server. Keys are verified offline by each Custodian, so nothing a customer
runs ever contacts the store.

## Run it

Run it on the vendor's machine, next to the license signing key, never on a customer's server.

```bash
pip install -r store/requirements.txt          # or reuse backend/.venv
export STRIPE_SECRET_KEY=sk_live_... STRIPE_WEBHOOK_SECRET=whsec_...
export STRIPE_PRICE_PRO=price_... STRIPE_PRICE_CLOUD=price_...
export STORE_BASE_URL=https://store.example.com
uvicorn store.server:app --host 0.0.0.0 --port 8100     # from the repository root
```

In Stripe: create a yearly price for Pro and a monthly price for Cloud, and add a webhook to
`https://store.example.com/stripe/webhook` for `checkout.session.completed`, `invoice.paid` and
`customer.subscription.deleted`. Then point the app's "Get a license" link at the store with
`CUSTODIAN_PURCHASE_URL=https://store.example.com` in each Custodian's `backend/.env`.

| Variable | Purpose |
|---|---|
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | From the Stripe dashboard |
| `STRIPE_PRICE_PRO` / `STRIPE_PRICE_CLOUD` | Price ids; a plan without one isn't sold |
| `STORE_BASE_URL` | Where buyers come back to after paying |
| `LICENSE_SIGNING_KEY` | Default `~/.custodian/license-signing-key.pem` (`python -m app.cli license-keygen`) |
| `STORE_DB` | Orders database, default `store.db` |
| `PROVISION_DIR` | Where Cloud tenants' files go, default `cloud/` |
| `CLOUD_DOMAIN` | Tenants get `<name>.CLOUD_DOMAIN`; point a wildcard DNS record at the server |
| `CLOUD_IMAGE` | Default `custodian:latest` (`docker build -t custodian .`) |
| `CLOUD_NEBIUS_API_KEY` / `_MODEL_ID` / `_BASE_URL` | The model Cloud tenants use (you pay for it; the Cloud plan's monthly allowance caps it) |
| `CLOUD_DEFAULT_TIMEZONE` | New tenants' time zone |
| `PROVISION_APPLY` | `true` runs `docker compose up -d` after each change |

## Operations

```bash
python -m store.cli orders       # orders and license expiry
python -m store.cli tenants      # Cloud tenants and their status
python -m store.cli suspend NAME | resume NAME
cd cloud && docker compose up -d # apply the generated compose file yourself (without PROVISION_APPLY)
```

Tests: `backend/.venv/Scripts/python -m pytest -c store/pytest.ini --rootdir store store/tests`.

## Not covered

- **Emailing the key.** Buyers see it on their order page; Stripe sends the receipt. Add an
  SMTP step in `checkout_completed` if you want it emailed too.
- **Several Cloud servers.** All tenants run on the store's Docker host; past a few dozen,
  split `PROVISION_DIR` across hosts.
- **Tax, invoices and refunds** stay in Stripe's dashboard.
