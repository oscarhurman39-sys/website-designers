"""Retry-with-exponential-backoff for external API calls.

Used by vercel_api.py (whole-function decoration) and email_utils.py's
SendGrid send (wrapping just the API call). github_api.py deliberately does
NOT use this decorator: PyGithub accepts a urllib3 Retry at construction,
which retries at the transport layer where idempotency is handled correctly
(POSTs are only retried when the request never reached the server) -- see
github_api._get_client().

Two transience predicates, because "should we retry?" depends on what a
duplicate would cost:

  * is_transient (default): retry connection-level errors (no HTTP status:
    DNS failure, reset, timeout) AND 429/5xx responses. Right for calls
    where an accidental duplicate is harmless (re-deploying a preview,
    re-fetching a project id).
  * is_retriable_http_response: retry ONLY when the server definitively
    answered 429/5xx. Right for email sends: an ambiguous timeout might
    mean the mail already went out, and a retry would send the same cold
    email twice. A 5xx response is SendGrid saying "not accepted" -- safe.

Backoff: base_delay * 2^(attempt-1) plus up to 25% random jitter, i.e.
~2-2.5s then ~4-5s for the default 3 attempts -- the jitter spreads out
retries that would otherwise all fire at the same offset (e.g. several
leads hitting a rate limit in the same cycle and retrying in lockstep).
"""
from __future__ import annotations

import functools
import random
import time
from typing import Any, Callable, Optional, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])


def _http_status(exc: BaseException) -> Optional[int]:
    """Best-effort HTTP status extraction across client libraries:
    GithubException.status, python_http_client's .status_code, and
    requests.HTTPError's .response.status_code."""
    for attr in ("status", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def is_transient(exc: BaseException) -> bool:
    """Default predicate: connection-level errors (no status) and 429/5xx
    responses are worth retrying; any other definitive 4xx is not."""
    status = _http_status(exc)
    if status is None:
        return True  # request likely never completed (DNS/reset/timeout)
    return status == 429 or status >= 500


def is_retriable_http_response(exc: BaseException) -> bool:
    """Strict predicate for non-idempotent sends (email): only retry when
    the server definitively rejected with 429/5xx. Ambiguous failures
    (timeouts, resets -- no status) are NOT retried, because the request
    may have succeeded and a retry would duplicate it."""
    status = _http_status(exc)
    return status is not None and (status == 429 or status >= 500)


def with_retries(
    *,
    attempts: int = 3,
    base_delay: float = 2.0,
    retriable: tuple[type[BaseException], ...],
    transient: Callable[[BaseException], bool] = is_transient,
    label: str = "",
) -> Callable[[_F], _F]:
    """Decorator: retry `retriable` exceptions that `transient` deems worth
    retrying, up to `attempts` total tries with exponential backoff. The
    final failure (or any non-transient one) re-raises unchanged, so callers'
    existing error handling keeps working."""

    def decorator(fn: _F) -> _F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except retriable as exc:  # noqa: PERF203 - retry loop by design
                    if attempt == attempts or not transient(exc):
                        raise
                    delay = base_delay * (2 ** (attempt - 1))
                    delay += random.uniform(0, delay * 0.25)  # jitter: avoid synchronized thundering-herd retries
                    print(
                        f"[retry] {label or fn.__name__}: attempt {attempt}/{attempts} failed "
                        f"({exc.__class__.__name__}: {exc}); retrying in {delay:.0f}s"
                    )
                    time.sleep(delay)
            raise AssertionError("unreachable")  # loop always returns or raises

        return wrapper  # type: ignore[return-value]

    return decorator
