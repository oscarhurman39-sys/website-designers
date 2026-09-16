"""Post one test alert to the Slack channel the pipeline uses.

    python run.py test-alert

`run.py preflight` can prove the bot token is valid, but not that a message
actually lands: the channel may not exist, or the bot may not be allowed to
post in it. This sends a real message through the same `_slack_notify` the
sales agent uses, so a success here means a real positive reply or payment
will reach you too.

Sends nothing to any prospect and touches no lead data.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import config
from agents import sales_agent


def main() -> int:
    if not config.SLACK_BOT_TOKEN.strip():
        print(
            "SLACK_BOT_TOKEN is empty, so alerts are console-only by design.\n"
            "Nothing to test. Watch the pipeline window or the dashboard for replies."
        )
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = (
        f":white_check_mark: Test alert from the Casey Websites pipeline ({stamp}).\n"
        "If you can read this, real reply and payment alerts will reach you here."
    )
    print(f"Posting a test alert to {config.SLACK_ALERT_CHANNEL} ...")
    if sales_agent._slack_notify(text):
        print(f"Sent. Check {config.SLACK_ALERT_CHANNEL} in Slack -- the message should already be there.")
        return 0

    print(
        f"FAILED. The message did not reach {config.SLACK_ALERT_CHANNEL}. Common causes:\n"
        f"  - the channel does not exist yet (create {config.SLACK_ALERT_CHANNEL} in Slack)\n"
        f"  - it is a PRIVATE channel (chat:write.public only covers public ones -- invite the\n"
        f"    bot with `/invite @Casey Alerts` inside the channel)\n"
        "  - the token is not the Bot User OAuth Token (it must start with xoxb-)\n"
        "  - the app was never installed to the workspace\n"
        "Any Slack error above this line is the specific reason."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
