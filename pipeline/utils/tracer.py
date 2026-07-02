"""Hierarchical tracing for pipeline agents: trace -> agent -> tool spans.

Local trace history (pipeline/traces.json) is ALWAYS written, regardless of
VoltAgent configuration -- it's the dashboard's source of truth. When
VOLTAGENT_PUBLIC_KEY and VOLTAGENT_SECRET_KEY are both set, every span is
additionally mirrored to VoltAgent Cloud using the real, async
`voltagent.VoltAgentSDK` (async context manager for the trace, plain async
calls for add_agent()/add_tool(), async success()/error() to close a span --
this matches the SDK's actual shape, verified by reading its source, not
guessed). That SDK is intentionally write-only (create/update history and
events; no list/query endpoint exists), so it cannot serve as the
dashboard's read path even in cloud mode -- the local file always can.

Call sites in agents/*.py stay fully synchronous: `run_traced()` is the only
public entrypoint, and it bridges to the SDK's native async API internally
via `asyncio.run()`. Each pipeline operation (one lead, one email) is
infrequent enough that spinning up an event loop per call has no
measurable cost, so this avoids rewriting the rest of the (already
synchronous, already-verified) pipeline into async code.
"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

import config

T = TypeVar("T")

_USE_CLOUD = bool(config.VOLTAGENT_PUBLIC_KEY and config.VOLTAGENT_SECRET_KEY)
_write_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Local trace file (always-on, dashboard's source of truth) -------------

def load_local_traces() -> list[dict[str, Any]]:
    """Read every trace recorded so far, most recent last. Used by dashboard.py."""
    path = Path(config.TRACES_PATH)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _append_local_trace(record: dict[str, Any]) -> None:
    path = Path(config.TRACES_PATH)
    with _write_lock:
        traces = load_local_traces()
        traces.append(record)
        path.write_text(json.dumps(traces, indent=2, default=str))


# --- VoltAgent Cloud mirroring (best-effort, never blocks the pipeline) ----

async def _open_cloud_span(agent_id: str, agent_name: str, tool_name: str, input_data: dict[str, Any]):
    """Start a trace -> agent -> tool span in VoltAgent Cloud. Returns
    (trace_cm, trace_ctx, agent_ctx, tool_ctx) or all-None on any failure --
    callers must treat cloud mirroring as optional and never let it raise."""
    try:
        from voltagent import VoltAgentSDK
        from voltagent.types import AgentOptions, ToolOptions

        sdk = VoltAgentSDK(
            base_url=config.VOLTAGENT_BASE_URL,
            public_key=config.VOLTAGENT_PUBLIC_KEY,
            secret_key=config.VOLTAGENT_SECRET_KEY,
        )
        trace_cm = sdk.trace(agent_id=agent_id, input=input_data)
        trace_ctx = await trace_cm.__aenter__()
        agent_ctx = await trace_ctx.add_agent(AgentOptions(name=agent_name, input=input_data))
        tool_ctx = await agent_ctx.add_tool(ToolOptions(name=tool_name, input=input_data))
        return sdk, trace_cm, trace_ctx, agent_ctx, tool_ctx
    except Exception as exc:  # noqa: BLE001 - cloud mirroring must never break the pipeline
        print(f"[tracer] VoltAgent Cloud span start failed, continuing with local tracing only: {exc}")
        return None, None, None, None, None


async def _close_cloud_span(sdk, trace_cm, tool_ctx, agent_ctx, error: Optional[Exception]) -> None:
    if trace_cm is None:
        return
    try:
        if error is None:
            if tool_ctx:
                await tool_ctx.success(output={"status": "ok"})
            if agent_ctx:
                await agent_ctx.success(output={"status": "ok"})
            await trace_cm.__aexit__(None, None, None)
        else:
            if tool_ctx:
                await tool_ctx.error(str(error))
            if agent_ctx:
                await agent_ctx.error(str(error))
            await trace_cm.__aexit__(type(error), error, error.__traceback__)
    except Exception as exc:  # noqa: BLE001 - same rationale as above
        print(f"[tracer] VoltAgent Cloud span close failed (local trace is unaffected): {exc}")
    finally:
        try:
            if sdk is not None:
                await sdk.shutdown()
        except Exception:
            pass


# --- Unified entrypoint ------------------------------------------------------

async def _run_traced_async(
    agent_id: str, agent_name: str, tool_name: str, input_data: dict[str, Any], fn: Callable[[], T]
) -> T:
    local_record: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "input": input_data,
        "status": "working",
        "start_time": _now(),
        "end_time": None,
        "agents": [
            {
                "name": agent_name,
                "input": input_data,
                "output": None,
                "status": "running",
                "start_time": _now(),
                "end_time": None,
                "tools": [
                    {
                        "name": tool_name,
                        "input": input_data,
                        "output": None,
                        "status": "running",
                        "start_time": _now(),
                        "end_time": None,
                        "error": None,
                    }
                ],
            }
        ],
    }
    agent_span = local_record["agents"][0]
    tool_span = agent_span["tools"][0]

    sdk = trace_cm = trace_ctx = agent_ctx = tool_ctx = None
    if _USE_CLOUD:
        sdk, trace_cm, trace_ctx, agent_ctx, tool_ctx = await _open_cloud_span(
            agent_id, agent_name, tool_name, input_data
        )

    error: Optional[Exception] = None
    try:
        result = fn()
    except Exception as exc:
        error = exc
        tool_span["status"] = agent_span["status"] = local_record["status"] = "error"
        tool_span["error"] = str(exc)
        agent_span["output"] = {"error": str(exc)}
        raise
    else:
        tool_span["status"] = agent_span["status"] = local_record["status"] = "completed"
        tool_span["output"] = agent_span["output"] = {"status": "ok"}
        return result
    finally:
        tool_span["end_time"] = agent_span["end_time"] = local_record["end_time"] = _now()
        _append_local_trace(local_record)
        if _USE_CLOUD:
            await _close_cloud_span(sdk, trace_cm, tool_ctx, agent_ctx, error)


def run_traced(
    agent_id: str, agent_name: str, tool_name: str, input_data: dict[str, Any], fn: Callable[[], T]
) -> T:
    """Run `fn()` wrapped in a trace -> agent -> tool span; return its result
    (or re-raise its exception) unchanged. `fn` is called exactly once."""
    return asyncio.run(_run_traced_async(agent_id, agent_name, tool_name, input_data, fn))
