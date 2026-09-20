# Moving the sales pipeline off the PC onto a Linux VPS

Written 2026-09-20 for Oscar. Read-only review of `deploy/`, `tools/`, `pipeline/` and the Windows box; nothing was run or changed. Every price and fact below has a source at the end, with the date it was checked.

## 1. What the move changes, and what it costs

Today the loop (`main.py`), the Flask webhook on :5000 and an ngrok tunnel all live on the PC, kept alive by a Windows scheduled task (`CaseyPipelineWatchdog`, every 10 minutes). When the PC sleeps, replies wait, payment links die and Stripe's webhook has nobody to talk to.

After the move, a small Ubuntu 24.04 server runs the same two programs under systemd (which restarts them itself), Caddy gives `track.caseywebsites.com` a free HTTPS certificate, and ngrok is no longer needed. Nothing about the emails, Zoho, Vercel, GitHub or Stripe keys changes. Only three settings move: `PUBLIC_BASE_URL`, `STRIPE_WEBHOOK_SECRET` (a new webhook endpoint gets a new secret) and, if you use it, `CLICK_TRACKING_ENABLED`.

| Option | Spec | Price (ex VAT) | About | Region |
|---|---|---|---|---|
| Hetzner CX23 | 2 vCPU, 4 GB, 40 GB, IPv4 included | €5.49/mo | £4.71 (£5.65 with 20% VAT) | Germany or Finland only; no UK site |
| DigitalOcean Basic 2 GB | 1 vCPU, 2 GB, 50 GB | $12/mo | £8.99 (£10.79 with VAT) | London (LON1) available |
| DigitalOcean Basic 1 GB | 1 vCPU, 1 GB, 25 GB | $6/mo | £4.50 (£5.40 with VAT) | London; needs the swap step below for Chromium |

Pick Hetzner CX23 if the Cloud Console lets you order one (on 2026-09-20 Hetzner's own plan pages showed CX23 and CPX22 as "currently unavailable", and the docs say CPX22 is now €19.49 in Germany/Finland, so check the price in the console before you click Buy). Otherwise DigitalOcean 2 GB in London. The ngrok free static domain and the Porkbun domain you already own cost nothing extra, and the PC no longer has to stay on. Budget: about £5 to £11 a month.

## 2. Things only you can do (before the commands)

1. **Buy the server.** Ubuntu 24.04 LTS, add your SSH public key when ordering, note the public IPv4 address. (On Windows: `ssh-keygen` once in PowerShell if you have no key; the public half is `C:\Users\<you>\.ssh\id_ed25519.pub`.)
2. **DNS at Porkbun.** Add an **A** record: host `track`, answer `<VPS IP>`, TTL 600. Do this early; Caddy cannot get a certificate until it resolves.
3. **Stripe (live mode).** Workbench, Webhooks tab, *Create an event destination*, Your account, event `checkout.session.completed`, destination *Webhook endpoint*, URL `https://track.caseywebsites.com/webhook/stripe`. On the endpoint page click *Reveal secret*; you will paste that value as `STRIPE_WEBHOOK_SECRET` in the **server's** `.env` in step 3.9. Leave the old ngrok endpoint alive until section 5 passes. Each endpoint has its own secret, so the PC's `.env` keeps working unchanged for rollback.
4. **Zoho.** No change to SPF/DKIM: Zoho's SPF record authorises Zoho's mail servers for your domain, and the pipeline still sends through `smtppro.zoho.eu`, so recipients see the same sending IPs. Two things to watch: Zoho flags logins that differ from previous behaviour (its security report lists client IP and source such as IMAP / SMTP_OUT), so after the first server start open Admin Console, Security, Suspicious logins and confirm the new IP; and Zoho can suspend IMAP for "simultaneous connections from multiple clients", which is another reason the PC loop must be stopped first. If you ever set an *Allowed IPs* list in Zoho, add the VPS IP.
5. **Google Places / GitHub / Vercel / Slack** need nothing, unless you restricted the Places API key to the PC's IP.

## 3. Commands, in order

`<VPS IP>` is your server address. Blocks marked **PC** run in PowerShell on the Windows box (repo root `D:\projects\website-designers`); blocks marked **VPS** run over `ssh root@<VPS IP>`. The PC keeps running normally until section 4.

**3.1 VPS: base packages, timezone, firewall** (one line each: update, install, set local time so `SEND_WINDOW_*` hours mean UK time, open only SSH/80/443)
```bash
apt update && apt install -y python3-venv git caddy ufw
timedatectl set-timezone Europe/London
ufw allow OpenSSH && ufw allow 80,443/tcp && ufw --force enable
```

**3.2 VPS (only on a 1 GB droplet): 2 GB swap so headless Chromium cannot OOM-kill the loop**
```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

**3.3 VPS: service user** (no password, own home so Playwright's browser cache has somewhere to live)
```bash
useradd --system --create-home --shell /bin/bash pipeline
```

**3.4 PC: bundle the code and send it.** The working branch is not on GitHub (87 commits ahead of `origin/main`, never pushed), so `git clone` would install old code. This tar skips the venv, secrets, databases, logs and screenshots; those move in section 4.
```powershell
tar --exclude=venv --exclude=.claude --exclude=__pycache__ --exclude=dist --exclude=build --exclude=workshop --exclude=reports --exclude=pipeline/logs --exclude=pipeline/out --exclude=pipeline/archive --exclude=pipeline/proofs --exclude=pipeline/.run --exclude="*.pid" --exclude=".env*" --exclude=pipeline/.secret_key --exclude="*.db*" --exclude=pipeline/backups --exclude=pipeline/screenshots --exclude=pipeline/traces*.json -czf $env:TEMP\wd-code.tgz .
scp $env:TEMP\wd-code.tgz root@<VPS IP>:/tmp/
```

**3.5 VPS: unpack, create the venv, install pinned packages plus gunicorn** (Ubuntu 24.04's `python3` is 3.12; every pin in `pipeline/requirements.txt` has 3.12 wheels, so the PC's 3.14 is not required)
```bash
mkdir -p /opt/website-designers && tar -xzf /tmp/wd-code.tgz -C /opt/website-designers && chown -R pipeline:pipeline /opt/website-designers
sudo -u pipeline -H bash -c 'cd /opt/website-designers && python3 -m venv venv && venv/bin/pip install -r deploy/requirements-vps.txt'
```

**3.6 VPS: Chromium for preview screenshots** (first line installs the OS libraries as root; second downloads the browser into `/home/pipeline/.cache/ms-playwright`, the user the service runs as)
```bash
/opt/website-designers/venv/bin/playwright install-deps chromium
sudo -u pipeline -H /opt/website-designers/venv/bin/playwright install chromium
```

**3.7 VPS: Caddy** (install the site file, reload, then confirm a real certificate; a `502` here is correct because the app is not up yet)
```bash
cp /opt/website-designers/deploy/Caddyfile /etc/caddy/Caddyfile && systemctl reload caddy
sleep 20 && curl -sI https://track.caseywebsites.com/health | head -1
```

**3.8 VPS: install the two services but do not start them yet**
```bash
cp /opt/website-designers/deploy/systemd/*.service /etc/systemd/system/ && systemctl daemon-reload
```

Now do section 4 (stop the PC, move the data), then continue here.

**3.9 VPS: edit the server copy of `.env`** (`nano`, Ctrl-O Enter Ctrl-X to save). Change `PUBLIC_BASE_URL=https://track.caseywebsites.com`; paste the new endpoint's secret as `STRIPE_WEBHOOK_SECRET`; delete any `DB_PATH=`, `DB_BACKUP_DIR=` or `TRACES_PATH=` line that holds a `D:\` path so the Linux defaults apply; optionally `CLICK_TRACKING_ENABLED=true` now that a real domain fronts the links. Leave `ENABLE_LIVE_SEND` and every cap exactly as they were.
```bash
nano /opt/website-designers/.env
chown pipeline:pipeline /opt/website-designers/.env /opt/website-designers/pipeline/.secret_key && chmod 600 /opt/website-designers/.env /opt/website-designers/pipeline/.secret_key
```

**3.10 VPS: start, webhook first, then the loop**
```bash
systemctl enable --now website-designers-webhook && sleep 5 && systemctl enable --now website-designers
systemctl status website-designers-webhook website-designers --no-pager
```

## 4. Moving the database and backups safely

`leads.db` is SQLite in WAL mode; the loop, the webhook and the dashboard all write it. Two loops polling one inbox would each classify the same reply (and Zoho may block IMAP for the duplicate connection), so the PC must be fully stopped, and the watchdog disabled so it cannot restart the loop ten minutes later.

**4.1 PC (PowerShell as Administrator): freeze**
```powershell
Disable-ScheduledTask -TaskName "CaseyPipelineWatchdog"
Get-ScheduledTask -TaskName "website-designers" -ErrorAction SilentlyContinue | Disable-ScheduledTask
cd D:\projects\website-designers
.\venv\Scripts\python.exe run.py ops stop
.\venv\Scripts\python.exe run.py ops status
```
Expected: `webhook down`, `ngrok down`, `loop down`. Close any `start_all.bat` windows still open.

**4.2 PC: make a consistent copy of the database.** Never copy `leads.db` by hand while a `-wal` file sits next to it; the repo's own `backup_database()` uses SQLite's backup API, which is safe under WAL, and writes a plain file into `pipeline\backups\`. This imports config but runs no pipeline stage.
```powershell
.\venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'pipeline'); from utils import db; print(db.backup_database())"
```
It prints the new file's path, for example `pipeline\backups\leads-20260920-153000.db`. Use that name below.

**4.3 PC: send data and secrets** (database copy, existing backups, screenshots that sent emails still link to, the trace file the dashboard reads, `.env`, and `.secret_key`, which signs every unsubscribe and click token already in customers' inboxes; without it those links all break)
```powershell
scp pipeline\backups\leads-<STAMP>.db root@<VPS IP>:/opt/website-designers/pipeline/leads.db
scp -r pipeline\backups pipeline\screenshots root@<VPS IP>:/opt/website-designers/pipeline/
scp pipeline\traces.json pipeline\.secret_key root@<VPS IP>:/opt/website-designers/pipeline/
scp .env root@<VPS IP>:/opt/website-designers/.env
```

**4.4 VPS: ownership**
```bash
chown -R pipeline:pipeline /opt/website-designers && chmod 600 /opt/website-designers/pipeline/leads.db
```
Then return to 3.9. From here on the daily backup runs on the server (`pipeline/backups/`, newest 7 kept); copy that folder to the PC now and then with `scp -r root@<VPS IP>:/opt/website-designers/pipeline/backups D:\pipeline-backups`.

Downtime is the gap between 4.1 and 3.10, about 15 minutes. A payment landing in that gap is safe twice over: Stripe retries failed deliveries for up to three days, and the loop asks Stripe directly every ten minutes about every open checkout (`_maybe_reconcile_payments`), and `record_payment` is idempotent.

## 5. Verification checklist

Run on the VPS unless stated. Every line should match.

1. `curl -s https://track.caseywebsites.com/health` prints `{"ok": true, "service": "website-designers-webhook"}`.
2. `curl -s -o /dev/null -w "%{http_code}\n" https://track.caseywebsites.com/payment-success` prints `200`.
3. `curl -s -o /dev/null -w "%{http_code}\n" -X POST https://track.caseywebsites.com/webhook/stripe` prints `400` (the request reached Flask and the signature check rejected it, which is correct).
4. `systemctl is-active website-designers website-designers-webhook` prints `active` twice. This is the `ops status` equivalent; `ops.py` itself is Windows-and-ngrok specific.
5. `tail -n 30 /opt/website-designers/pipeline/scheduler.log` shows `[main] Pipeline starting. DB: /opt/website-designers/pipeline/leads.db` and no traceback. `journalctl -u website-designers-webhook -n 20` shows gunicorn booting two workers.
6. `cd /opt/website-designers && sudo -u pipeline -H venv/bin/python run.py preflight` ends `GO (ARMED)` with the public-URL and mailbox-login lines passing.
7. `sudo -u pipeline -H venv/bin/python run.py test-alert` and the message appears in Slack `#leads`.
8. Stripe Workbench, Webhooks: the new endpoint shows *Enabled*; after the first real payment its *Event deliveries* tab shows `200`.
9. Zoho Admin Console, Security reports: the VPS IP appears as a normal IMAP / SMTP_OUT login, not a block.
10. PC: `python run.py ops status` still says everything is down, and Task Scheduler shows `CaseyPipelineWatchdog` *Disabled*.

When all ten pass: in Stripe, open the old ngrok endpoint and *Disable* it. Links in emails already sent still point at the ngrok domain (unsubscribe, click, screenshot URLs), and unsubscribe must keep working for 30 days after a send; the cheap bridge is to run the ngrok agent on the VPS for a month, pointed at the same port: `snap install ngrok`, `ngrok config add-authtoken <your token>`, then `tmux new -d -s ngrok "ngrok http --url=<your ngrok static domain> 5000"`.

## 6. Rollback

1. VPS: `systemctl disable --now website-designers website-designers-webhook`.
2. If the server ran for more than a few minutes it has newer data (sends, replies, payments). Copy it back before the PC restarts, or the PC will re-send: on the VPS `sudo -u pipeline -H /opt/website-designers/venv/bin/python -c "import sys; sys.path.insert(0,'/opt/website-designers/pipeline'); from utils import db; print(db.backup_database())"`, then on the PC `scp root@<VPS IP>:/opt/website-designers/pipeline/backups/leads-<STAMP>.db pipeline\leads.db` (delete `pipeline\leads.db-wal` and `-shm` first if present) and `scp -r root@<VPS IP>:/opt/website-designers/pipeline/screenshots pipeline\`.
3. PC: `Enable-ScheduledTask -TaskName "CaseyPipelineWatchdog"` then `.\venv\Scripts\python.exe run.py ops start`. The PC's `.env` was never edited, so it still points at ngrok and the old webhook secret.
4. Stripe: re-enable the old ngrok endpoint if you disabled it. The new one can stay disabled.

## 7. Gaps found in `deploy/` (proposed fixes; none applied)

1. **`git clone` installs stale code.** The branch on the PC (`loop/20260911-sweep-fix-...`) has never been pushed; `origin/main` is 87 commits behind. `deploy/README.md` line 13:
   ```diff
   -sudo git clone <your-repo-url> /opt/website-designers
   +# The PC's branch is not on GitHub. Bundle the tree on the PC (see runbook 3.4) and:
   +sudo mkdir -p /opt/website-designers && sudo tar -xzf /tmp/wd-code.tgz -C /opt/website-designers
   ```
   The "Updating" section's `git pull` has the same problem until the branch is pushed.
2. **No Playwright step.** `requirements-vps.txt` installs the Python package only; the first design run would fail with "Executable doesn't exist" and every cold email would go out with no preview image. README after line 15:
   ```diff
   +sudo /opt/website-designers/venv/bin/playwright install-deps chromium
   +sudo -u pipeline -H /opt/website-designers/venv/bin/playwright install chromium
   ```
3. **Timezone.** `sales_agent._inside_send_window` uses `now.astimezone()`, so on a UTC server the 08:00 to 22:00 window runs an hour early in summer. README line 11 and the loop unit:
   ```diff
    sudo apt update && sudo apt install -y python3-venv git caddy
   +sudo timedatectl set-timezone Europe/London
   ```
   ```diff
    WorkingDirectory=/opt/website-designers/pipeline
   +Environment=TZ=Europe/London
   ```
4. **Log output is buffered and never rotated.** `website-designers.service` redirects stdout to `pipeline/scheduler.log`; Python block-buffers when stdout is a file, so lines appear hours late, and nothing rotates the file (`ops.py` expects `pipeline/logs/*.log` instead, so the two tools disagree too). Fix in `deploy/systemd/website-designers.service`:
   ```diff
    WorkingDirectory=/opt/website-designers/pipeline
   +Environment=PYTHONUNBUFFERED=1
   -StandardOutput=append:/opt/website-designers/pipeline/scheduler.log
   -StandardError=append:/opt/website-designers/pipeline/scheduler.log
   +StandardOutput=journal
   +StandardError=journal
   ```
   and README line 48 `tail -f .../scheduler.log` becomes `journalctl -u website-designers -f`. (Until that lands, section 5 step 5 above reads the file.)
5. **Files the README forgets.** Section 2 mentions only `.env` and `leads.db`, but `pipeline/.secret_key`, `pipeline/screenshots/`, `pipeline/backups/` and `pipeline/traces.json` are all gitignored and needed; losing `.secret_key` invalidates every link in already-sent emails. README lines 28-29:
   ```diff
   -Copy the SQLite database too if you want to keep existing leads:
   -`pipeline/leads.db` (or whatever `DB_PATH` points at).
   +With the old machine stopped, copy a fresh `db.backup_database()` file as `pipeline/leads.db`
   +(never the raw file while a `-wal` exists), plus `pipeline/.secret_key`, `pipeline/screenshots/`,
   +`pipeline/backups/`, `pipeline/traces.json`. Blank any `DB_PATH=`/`DB_BACKUP_DIR=`/`TRACES_PATH=` Windows paths.
   ```
6. **No firewall step.** README line 11:
   ```diff
   +sudo apt install -y ufw && sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw --force enable
   ```
7. **`ops.py` and the watchdog are Windows-only.** `tools/ops.py` line 33 hard-codes `venv/Scripts/python.exe` and `status()` requires ngrok on :4040, so `run.py ops status` can never say `ALL UP` on the VPS; `tools/watchdog.ps1` is PowerShell. systemd's `Restart=always` is the replacement, and the README should say so. Minimal fix so `ops logs` and `run-now-proof` work on Linux:
   ```diff
   -PY = REPO / "venv" / "Scripts" / "python.exe"
   +PY = REPO / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
   ```
8. **ngrok assumptions elsewhere.** `README.md` "Keeping the public URL up" and `pipeline/serve_public.py` only know ngrok, and the `CLICK_TRACKING_ENABLED` comment in `config.py` says to turn it on once a real domain fronts the links; `deploy/README.md` should tell the reader to set it and to ignore `serve-public` on the VPS.
9. **Stale price and plan name.** README line 5 says "Hetzner CX22 ... ~£4/month"; CX22 was replaced by CX23, priced €5.49 (about £4.71 ex VAT) since 15 June 2026:
   ```diff
   -running it is off. A tiny VPS (Hetzner CX22 / DigitalOcean basic, ~£4/month)
   +running it is off. A tiny VPS (Hetzner CX23 at €5.49/month, or DigitalOcean 2 GB at $12/month)
   ```
10. **Startup validation skipped under gunicorn.** `webhook_server.py` only calls `config.validate()` and `db.init_db()` under `if __name__ == "__main__"`; gunicorn imports `app` directly, so a blank `STRIPE_WEBHOOK_SECRET` surfaces only as `400`s in Stripe's deliveries tab. Fix: call `config.validate()` as the first line of `create_app()`.
11. **Pins drift from what the PC runs.** The PC's venv has pandas 2.3.3 and streamlit 1.59.0; `pipeline/requirements.txt` pins 2.2.3 and 1.38.0. The VPS will get the pins, which is fine for the loop (pandas only matters to report/dashboard), but `pip freeze > deploy/requirements-lock.txt` on the PC before the move would make the two identical.

## Sources (all accessed 2026-09-20)

- Hetzner price adjustment, 15 June 2026 (CX23 €5.49, CPX22 €19.49): https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/
- Hetzner Cost-Optimized plans page (CX23 spec, "currently unavailable", price incl. IPv4): https://www.hetzner.com/cloud/cost-optimized/
- Hetzner locations (no UK): https://docs.hetzner.com/cloud/general/locations/
- Hetzner CX22 launch press release (June 2024, superseded): https://www.hetzner.com/pressroom/new-cx-plans/
- DigitalOcean Droplet pricing ($4/$6/$12 Basic tiers, per-second billing capped monthly): https://www.digitalocean.com/pricing/droplets
- DigitalOcean regions (LON1 available): https://docs.digitalocean.com/platform/regional-availability/
- ECB reference rates 2026-09-18 (EUR/GBP 0.8588, EUR/USD 1.1460): https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml
- Ubuntu 24.04 release notes (Python defaults to 3.12): https://discourse.ubuntu.com/t/noble-numbat-release-notes/39890
- deadsnakes PPA, only if a future pin needs 3.13: https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa
- Playwright Python system requirements (Python 3.8+, Ubuntu 22.04/24.04/26.04): https://playwright.dev/python/docs/intro
- Playwright browsers, `install-deps`, `~/.cache/ms-playwright`, `PLAYWRIGHT_BROWSERS_PATH`: https://playwright.dev/python/docs/browsers
- Caddy install (Debian/Ubuntu, runs as systemd service `caddy`): https://caddyserver.com/docs/install
- Caddy automatic HTTPS (DNS must point at the server, ports 80/443 open, Let's Encrypt with ZeroSSL fallback): https://caddyserver.com/docs/automatic-https
- Zoho SPF (record authorises Zoho's servers for the domain): https://www.zoho.com/mail/help/adminconsole/spf-configuration.html
- Zoho IMAP/SMTP hosts and app-specific passwords: https://www.zoho.com/mail/help/imap-access.html
- Zoho IMAP blocks, 5 minutes to 5 days, "simultaneous connections from multiple clients": https://www.zoho.com.cn/mail/help/faq/imap-access1.html
- Zoho suspicious-login report (client IP, IMAP/SMTP_OUT source, based on previous behaviour): https://www.zoho.com/mail/help/adminconsole/security-reports.html
- Stripe webhooks: Workbench endpoint creation, Reveal secret, one secret per endpoint, 3-day retries, HTTPS required: https://docs.stripe.com/webhooks
- Stripe API, updating an endpoint edits `url`/events/status only (secret unchanged unless rolled): https://docs.stripe.com/api/webhook_endpoints/update
