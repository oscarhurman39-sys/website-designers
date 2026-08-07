# Cloud deployment (always-on, phone-manageable)

This runs the **whole pipeline 24/7 in the cloud**: the orchestrator loop
(research → design → send → inbox polling) and the webhook server
(unsubscribe / click-tracking / Stripe) in one container, with all state
on a persistent volume. Written for Railway because the whole flow works
from a phone browser; the same Dockerfile works on Render or Fly.io.

## What you need before starting

- This repo pushed to GitHub (it is).
- Your filled-in `.env` values to hand (you'll paste them into Railway's
  dashboard — the `.env` file itself is never uploaded).
- A Railway account (railway.com — sign in with GitHub). Hobby plan is
  ~$5/mo which comfortably covers this.

## Steps (all doable from your phone)

1. **Create the project.** Railway dashboard → New Project → *Deploy from
   GitHub repo* → pick `website-designers`. It detects the Dockerfile and
   starts building (the first build takes a few minutes — Chromium for
   screenshots is included).

2. **Add the volume.** On the service: right-hand panel → *Volumes* (or
   ⌘K → "volume") → attach a volume with **mount path `/data`**. This is
   where the leads DB, screenshots, and traces live so they survive
   restarts and redeploys. Without it the pipeline forgets who it emailed
   — do not skip this.

3. **Set the environment variables.** Service → *Variables* → paste each
   of these (same values as your local `.env`):

   Required: `GITHUB_TOKEN`, `VERCEL_TOKEN`, `EMAIL_HOST`, `EMAIL_PORT`,
   `EMAIL_USER`, `EMAIL_PASSWORD`, `HF_API_TOKEN`, `STRIPE_SECRET_KEY`,
   `STRIPE_WEBHOOK_SECRET`, `ADMIN_EMAIL`, `SENDING_DOMAIN`,
   `PHYSICAL_ADDRESS`.

   **Also required in the cloud — `SECRET_KEY`.** Locally the pipeline
   auto-generates this and saves it to a file; in a container that file is
   wiped on every redeploy, which would break every unsubscribe link
   already sent out. Generate one once (`openssl rand -hex 32`, or any
   long random string) and set it explicitly. Never change it afterwards.

   Optional but recommended: `SLACK_BOT_TOKEN` + `SLACK_ALERT_CHANNEL` —
   this is how your phone gets pinged at work when a lead replies
   positively. `SENDGRID_API_KEY` + `SENDGRID_FROM_EMAIL` if you send via
   SendGrid instead of SMTP.

   For hands-off lead flow: `GOOGLE_PLACES_API_KEY` plus
   `DISCOVER_NICHES` and `DISCOVER_LOCATIONS` (comma-separated, e.g.
   `plumber,electrician` / `Leeds UK,York UK`) — the loop then refills
   its own lead queue from Google Places whenever it runs low, so the
   pipeline feeds itself all day without you uploading CSVs.

   Leave `DB_PATH` / `TRACES_PATH` alone — the image already points them
   at `/data`.

4. **Expose it and set `PUBLIC_BASE_URL`.** Service → *Settings* →
   *Networking* → Generate Domain. Copy the resulting URL (e.g.
   `https://website-designers-production.up.railway.app`) and add it as
   the `PUBLIC_BASE_URL` variable — no trailing slash. Railway redeploys
   automatically when variables change. Every unsubscribe/click link in
   every email uses this URL, so set it before the first real send.

5. **Point Stripe at it.** Stripe dashboard → Developers → Webhooks →
   Add endpoint → `https://<your-domain>/webhook/stripe`, event
   `checkout.session.completed`. If this gives you a new signing secret,
   update `STRIPE_WEBHOOK_SECRET` to match.

6. **Check it's alive.** Open `https://<your-domain>/` (any response
   beats a connection error), and watch the deploy logs: you should see
   `[main] Pipeline starting.` followed by the 60s loop ticking.

## Feeding it leads and acting on hot ones

- **Leads in:** commit a CSV to the repo and load it via a one-off shell
  (Railway service → ⌘K → "shell", or `railway shell` in the CLI):
  `cd pipeline && python -m agents.lead_agent ../your_leads.csv`
- **Operator commands** (`takeover 12`, `payment ready 12`,
  `transfer 12`, `status`, `pause`/`resume`): these read from the
  terminal, so run them from a Railway shell attached to the service the
  same way. From a phone, the Railway web shell works; an SSH app
  (Termius) is comfier.
- **Hot-lead alerts:** with the Slack variables set you get a
  notification the moment someone replies positively — automation for
  that lead pauses on its own until you act.

## Known limitations of this setup

- The Streamlit dashboard isn't served by this container (one public
  port per Railway service). The pipeline's state is still fully visible
  via logs, Slack alerts, and `status` in the shell. If the dashboard
  matters day-to-day, it can be added as a second service later.
- Operator commands need a shell session (above) — they're not exposed
  over the web on purpose, since they move money and hand over repos.
- A brand-new sending domain still needs warming up before real volume —
  see the rate-limiting note in `pipeline/config.py`.
