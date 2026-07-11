#!/usr/bin/env python3
"""One-shot integration check for the cold-email pipeline (Expert 5 audit).

Run from the repo root on the branch you want to verify:

    python verify_email_setup.py

Prints PASS/FAIL for four independent checks and exits non-zero if any fail:

  1. ENV LOADING        -- .env exists at the repo root and python-dotenv loads it
  2. SENDGRID KEY       -- SENDGRID_API_KEY is set and looks like a real key ("SG.")
  3. SEND PATH          -- which transport a cold email would actually use
                           (SendGrid API vs SMTP), and whether the SendGrid code
                           path even exists on this branch
  4. TEMPLATE RENDERING -- templates/default renders via Jinja2 with a dummy
                           lead and leaves no unreplaced {{ placeholders }}

No email is sent and nothing is written except pipeline/.secret_key (which the
pipeline itself creates on first import anyway).
"""
from __future__ import annotations

import os
import re
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "pipeline"))

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


# --- Check 1: .env loading ---------------------------------------------------
def check_env_loading() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        record("ENV LOADING", False, f"{env_path} does not exist -- copy .env.example to .env and fill it in")
        return
    try:
        from dotenv import dotenv_values, load_dotenv
    except ImportError:
        record("ENV LOADING", False, "python-dotenv is not installed (pip install -r pipeline/requirements.txt)")
        return
    values = {k: v for k, v in dotenv_values(env_path).items() if v}
    load_dotenv(env_path)
    if not values:
        record("ENV LOADING", False, f"{env_path} exists but every line is blank")
        return
    record("ENV LOADING", True, f"{env_path} loaded; {len(values)} non-empty variables")


# --- Check 2: SendGrid key presence -------------------------------------------
def check_sendgrid_key() -> None:
    key = (os.getenv("SENDGRID_API_KEY") or "").strip()
    if not key:
        record("SENDGRID KEY", False, "SENDGRID_API_KEY is empty/missing in .env -- sends will fall back to SMTP")
        return
    if not key.startswith("SG."):
        record("SENDGRID KEY", False, f"SENDGRID_API_KEY is set (length {len(key)}) but does not start with 'SG.' -- probably a placeholder")
        return
    record("SENDGRID KEY", True, f"SENDGRID_API_KEY present (length {len(key)}, starts with 'SG.')")


# --- Check 3: sending path selection ------------------------------------------
def check_send_path() -> None:
    try:
        import config  # noqa: F401  (import also validates .env parsing inside the pipeline)
        from utils import email_utils
    except Exception as exc:  # noqa: BLE001
        record("SEND PATH", False, f"could not import pipeline modules: {exc}")
        return

    has_sendgrid_fn = hasattr(email_utils, "send_email_sendgrid")
    has_config_key = hasattr(config, "SENDGRID_API_KEY")
    selector_src = ""
    try:
        from agents import sales_agent
        selector_src = getattr(sales_agent, "_send_via_configured_transport", None) and "selector-present" or ""
    except Exception as exc:  # noqa: BLE001
        # sales_agent pulls in heavier deps (huggingface_hub, playwright); an
        # import failure here is reported but doesn't hide the transport verdict.
        selector_src = f"(sales_agent import failed: {exc})"

    if not has_sendgrid_fn or not has_config_key:
        missing = []
        if not has_sendgrid_fn:
            missing.append("email_utils.send_email_sendgrid()")
        if not has_config_key:
            missing.append("config.SENDGRID_API_KEY")
        record("SEND PATH", False,
               "SendGrid code path MISSING on this branch: no " + " / ".join(missing)
               + " -- every send will use raw SMTP (this is the 'SmtpClientAuthentication is disabled' bug)")
        return

    key_set = bool(getattr(config, "SENDGRID_API_KEY", ""))
    if selector_src.startswith("(sales_agent import failed"):
        record("SEND PATH", False,
               f"send_email_sendgrid() exists but sales_agent could not be imported to confirm it's used {selector_src}"
               " -- install pipeline/requirements.txt and re-run")
        return
    if selector_src != "selector-present":
        record("SEND PATH", False,
               "send_email_sendgrid() exists but sales_agent has no _send_via_configured_transport()"
               " -- cold emails still hardcode the SMTP path")
        return
    if key_set:
        record("SEND PATH", True, "cold emails will send via the SendGrid v3 API")
    else:
        record("SEND PATH", False, "SendGrid code path exists but SENDGRID_API_KEY is blank -- sends will use SMTP fallback")


# --- Check 4: template rendering ----------------------------------------------
def check_template_rendering() -> None:
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError:
        record("TEMPLATE RENDERING", False, "Jinja2 is not installed (pip install -r pipeline/requirements.txt)")
        return
    tpl_dir = REPO_ROOT / "templates" / "default"
    if not tpl_dir.exists():
        record("TEMPLATE RENDERING", False, f"{tpl_dir} does not exist")
        return
    context = {
        "business_name": "Testy Autos", "phone": "0100 555 0100", "location": "Testville",
        "pain_point_solution": "Your site loads slowly.", "testimonial": "Great service.",
        "hero_image_url": "https://example.com/x.jpg", "year": 2026,
        "hero_headline": "Testy Autos -- Trusted Local Vehicle Repair",
        "preview_url": "https://example.com/preview", "niche_display": "Vehicle Repair",
        "hero_tagline": "Keeping Testville drivers on the road",
        "services": ["MOT & Servicing", "Brake Repairs"],
        "nav_labels": ["Home", "About", "Services", "Reviews", "Contact"],
        "google_rating": None, "specialty": None,
    }
    try:
        env = Environment(loader=FileSystemLoader(str(tpl_dir)),
                          autoescape=select_autoescape(enabled_extensions=("html",)))
        problems = []
        for filename in ("index.html", "style.css"):
            rendered = env.get_template(filename).render(**context)
            leftovers = re.findall(r"\{\{[^}]*\}\}|\{%[^}]*%\}", rendered)
            if leftovers:
                problems.append(f"{filename}: unreplaced {leftovers[:3]}")
            if filename == "index.html" and context["business_name"] not in rendered:
                problems.append(f"{filename}: business_name never appears in output")
        if problems:
            record("TEMPLATE RENDERING", False, "; ".join(problems))
        else:
            record("TEMPLATE RENDERING", True, "templates/default index.html + style.css render cleanly with a dummy lead")
    except Exception as exc:  # noqa: BLE001
        record("TEMPLATE RENDERING", False, f"Jinja2 raised: {exc.__class__.__name__}: {exc}")


def main() -> int:
    for check in (check_env_loading, check_sendgrid_key, check_send_path, check_template_rendering):
        try:
            check()
        except Exception:  # noqa: BLE001 - one broken check must not hide the others
            record(check.__name__, False, "crashed:\n" + traceback.format_exc())
    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n" + ("ALL CHECKS PASSED" if not failed else f"{len(failed)} CHECK(S) FAILED: {', '.join(failed)}"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
