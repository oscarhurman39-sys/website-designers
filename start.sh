#!/usr/bin/env bash
# Container entrypoint: run the webhook server (gunicorn) and the pipeline
# orchestrator (scheduler.py foreground supervisor around main.py) side by
# side. If either process dies, exit the container so the host platform
# restarts it -- half-alive (webhooks up, pipeline down, or vice versa) is
# worse than a clean restart.
set -euo pipefail

mkdir -p /data/screenshots

# utils/screenshot.py writes to pipeline/screenshots (a fixed path); point
# it at the volume so screenshots survive restarts between design and send.
rm -rf /app/pipeline/screenshots
ln -sfn /data/screenshots /app/pipeline/screenshots

cd /app/pipeline

# $PORT is injected by Railway/Render/Fly; default matches local dev.
gunicorn --bind "0.0.0.0:${PORT:-5000}" --workers 2 webhook_server:app &
WEBHOOK_PID=$!

python scheduler.py &
PIPELINE_PID=$!

# Exit as soon as either process exits; the platform's restart policy
# brings the whole container back up.
wait -n "$WEBHOOK_PID" "$PIPELINE_PID"
exit 1
