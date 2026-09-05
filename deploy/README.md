# Deploying to a VPS (always-on)

The pipeline only sells while it is running: reply polling, negotiation
replies, payment links and the Stripe webhook all stop when the machine
running it is off. A tiny VPS (Hetzner CX22 / DigitalOcean basic, ~£4/month)
is enough. These steps take about 30 minutes on Ubuntu 22.04/24.04.

## 1. Server basics

```bash
sudo apt update && sudo apt install -y python3-venv git caddy
sudo useradd --system --create-home --shell /bin/bash pipeline
sudo git clone <your-repo-url> /opt/website-designers
sudo chown -R pipeline:pipeline /opt/website-designers
sudo -u pipeline bash -c 'cd /opt/website-designers && python3 -m venv venv && venv/bin/pip install -r deploy/requirements-vps.txt'
```

## 2. Configuration

Copy your working `.env` from the dev machine to `/opt/website-designers/.env`
(scp, or paste it), then change two lines:

```
PUBLIC_BASE_URL=https://track.caseywebsites.com
ENABLE_LIVE_SEND=true        # only once you are ready to send for real
```

Copy the SQLite database too if you want to keep existing leads:
`pipeline/leads.db` (or whatever `DB_PATH` points at). Lock the file down:
`chmod 600 /opt/website-designers/.env`.

## 3. Public URL (Caddy + DNS)

1. At Porkbun add an **A** record `track` -> the VPS's public IP.
2. `sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy`
   Caddy fetches a Let's Encrypt certificate automatically.
3. In the Stripe dashboard, point the webhook endpoint at
   `https://track.caseywebsites.com/webhook/stripe` and put its signing
   secret in `.env` as `STRIPE_WEBHOOK_SECRET`.

## 4. Services

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now website-designers-webhook website-designers
sudo systemctl status website-designers website-designers-webhook
tail -f /opt/website-designers/pipeline/scheduler.log
```

`curl https://track.caseywebsites.com/payment-success` should return 200.

## 5. Updating

```bash
cd /opt/website-designers && sudo -u pipeline git pull
sudo systemctl restart website-designers website-designers-webhook
```

## Operator console

The optional `transfer` / `status` / `pause` / `resume` commands need a
terminal. Stop the service temporarily and run `main.py` inside tmux when
you need them; the automated sales flow never needs them.
