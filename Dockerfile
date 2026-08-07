# Always-on cloud deployment for the full pipeline: main.py orchestrator
# loop + Flask webhook server (unsubscribe / click-tracking / Stripe) in
# one container. See DEPLOY.md for the step-by-step host setup.
FROM python:3.11-slim

WORKDIR /app

# Install Python deps first so code edits don't bust this (slow) layer.
COPY pipeline/requirements.txt pipeline/requirements.txt
RUN pip install --no-cache-dir -r pipeline/requirements.txt gunicorn

# Chromium for preview screenshots (utils/screenshot.py). --with-deps pulls
# the system libraries headless Chromium needs on slim Debian.
RUN playwright install --with-deps chromium

COPY . .

# Persistent state (SQLite DB, screenshots, traces) lives on a mounted
# volume at /data -- see DEPLOY.md. These envs are defaults; the host's
# dashboard can override them.
ENV DB_PATH=/data/leads.db \
    TRACES_PATH=/data/traces.json

CMD ["bash", "start.sh"]
