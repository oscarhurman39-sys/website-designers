"""Read-only, offline audit of active preview records."""
from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import config

MAX_PREVIEWS = 10


@dataclass(frozen=True)
class PreviewAudit:
    lead_id: int
    business_name: str
    ok: bool
    issues: tuple[str, ...]


def _valid_http_url(value: Optional[str]) -> bool:
    parsed = urlparse((value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _resolved_file(value: Optional[str], repo_root: Path) -> Optional[Path]:
    if not value or not value.strip():
        return None
    path = Path(value.strip())
    return path if path.is_absolute() else repo_root / path


def _read_preview_rows(db_path: str, limit: int) -> list[sqlite3.Row]:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    try:
        return conn.execute(
            """SELECT w.lead_id, l.business_name, l.contact_email,
                      w.repo_url, w.repo_full_name, w.preview_url,
                      w.vercel_project_id, w.screenshot_path
               FROM websites AS w
               JOIN leads AS l ON l.id = w.lead_id
               WHERE w.torn_down_at IS NULL
               ORDER BY w.id ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def _audit_row(row: sqlite3.Row, repo_root: Path) -> PreviewAudit:
    issues: list[str] = []
    if not _valid_http_url(row["preview_url"]):
        issues.append("preview URL missing or invalid")
    if not _valid_http_url(row["repo_url"]):
        issues.append("repository URL missing or invalid")
    screenshot = _resolved_file(row["screenshot_path"], repo_root)
    if screenshot is None:
        issues.append("screenshot file path missing")
    elif not screenshot.is_file():
        issues.append(f"screenshot file missing ({screenshot})")
    for field, label in (
        ("contact_email", "contact email"),
        ("repo_full_name", "repository handoff name"),
        ("vercel_project_id", "Vercel project handoff id"),
    ):
        if not (row[field] or "").strip():
            issues.append(f"{label} missing")
    return PreviewAudit(int(row["lead_id"]), row["business_name"], not issues, tuple(issues))


def audit_previews(db_path: Optional[str] = None, repo_root: Optional[Path] = None,
                   limit: int = MAX_PREVIEWS) -> list[PreviewAudit]:
    """Audit at most ten active previews without changing pipeline state."""
    if not 1 <= limit <= MAX_PREVIEWS:
        raise ValueError(f"limit must be between 1 and {MAX_PREVIEWS}")
    rows = _read_preview_rows(db_path or config.DB_PATH, limit)
    root = repo_root or Path(__file__).resolve().parents[1]
    return [_audit_row(row, root) for row in rows]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit up to ten preview records offline; never sends or deploys.")
    parser.add_argument("--limit", type=int, default=MAX_PREVIEWS,
                        help=f"maximum active previews to audit (1-{MAX_PREVIEWS})")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= MAX_PREVIEWS:
        parser.error(f"--limit must be between 1 and {MAX_PREVIEWS}")
    try:
        audits = audit_previews(limit=args.limit)
    except (FileNotFoundError, sqlite3.Error) as exc:
        print(f"Preview audit could not read the local database: {exc}")
        return 1
    print(f"Preview audit (offline; up to {args.limit})")
    if not audits:
        print("No active preview records found.")
        return 0
    failures = 0
    for audit in audits:
        if audit.ok:
            print(f"PASS lead {audit.lead_id}: {audit.business_name}")
        else:
            failures += 1
            print(f"FAIL lead {audit.lead_id}: {audit.business_name} -- {'; '.join(audit.issues)}")
    print(f"Summary: {len(audits)} audited, {len(audits) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
