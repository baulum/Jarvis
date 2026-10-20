"""Agent monitor: turns the stream-json job log into a live picture of what a coding job is doing.

Pure functions over the log entries written by ``CodingRunner`` (``coding-logs/<job>.jsonl``), so the
snapshot is the same while a job runs, after it finished and after a core restart.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

MAX_STEPS = 200
TERMINAL = ("completed", "failed", "cancelled")


def tool_detail(name: str | None, inp: dict[str, Any]) -> str:
    """One-line argument summary of a tool call (command, file, pattern …)."""
    for key in ("command", "file_path", "pattern", "path", "url", "query", "description", "prompt"):
        v = inp.get(key)
        if isinstance(v, str) and v.strip():
            first = v.strip().splitlines()[0]
            return first[:300]
    return ""


def result_summary(content: Any) -> str:
    """Short text of a tool_result block (string or list of text blocks)."""
    if isinstance(content, list):
        content = " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    text = " ".join(str(content or "").split())
    return text[:300]


def compact_events(ev: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    """Log entries for one stream-json event. One entry per tool call / text / tool result."""
    ts = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    t = ev.get("type")
    out: list[dict[str, Any]] = []
    if t == "assistant":
        msg = ev.get("message", {}) or {}
        usage = msg.get("usage") or {}
        first = True
        for block in msg.get("content", []) or []:
            entry: dict[str, Any] | None = None
            if block.get("type") == "text":
                entry = {"type": "assistant", "text": str(block.get("text", ""))[:1500]}
            elif block.get("type") == "tool_use":
                inp = block.get("input", {}) or {}
                entry = {
                    "type": "assistant",
                    "tool": block.get("name"),
                    "tool_id": block.get("id"),
                    "detail": tool_detail(block.get("name"), inp),
                }
            if entry is None:
                continue
            entry["ts"] = ts
            if first and usage:
                # one message may be streamed as several events with the same id: dedupe in build_monitor
                entry["msg_id"] = msg.get("id")
                entry["usage"] = {
                    "input": int(usage.get("input_tokens") or 0),
                    "output": int(usage.get("output_tokens") or 0),
                    "cache_read": int(usage.get("cache_read_input_tokens") or 0),
                    "cache_creation": int(usage.get("cache_creation_input_tokens") or 0),
                }
            first = False
            out.append(entry)
    elif t == "user":
        content = (ev.get("message", {}) or {}).get("content", [])
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                out.append(
                    {
                        "type": "tool_result",
                        "ts": ts,
                        "tool_id": block.get("tool_use_id"),
                        "result": result_summary(block.get("content")),
                        "is_error": bool(block.get("is_error")),
                    }
                )
    elif t == "result":
        out.append(
            {
                "type": "result",
                "ts": ts,
                **{k: ev.get(k) for k in ("subtype", "is_error", "num_turns", "duration_ms")},
                "text": str(ev.get("result", ""))[:1500],
                "cost_usd": ev.get("total_cost_usd"),
            }
        )
    elif t == "system":
        out.append({"type": "system", "ts": ts, "subtype": ev.get("subtype")})
    return out or ([{"type": t, "ts": ts}] if t else [])


def _parse(ts: Any) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        d = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _secs(a: datetime | None, b: datetime | None) -> float | None:
    return None if a is None or b is None else max(0.0, (b - a).total_seconds())


def build_monitor(
    job: dict[str, Any], entries: list[dict[str, Any]], *, now: datetime | None = None, pending_messages: int = 0
) -> dict[str, Any]:
    """Snapshot for the monitor UI: current step, tool calls with results, runtime, tokens, cost."""
    now = now or datetime.now(UTC)
    status = str(job.get("status"))
    finished = _parse(job.get("finished_at"))
    active = status in ("queued", "running", "paused")
    end = now if active else (finished or now)

    steps: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    seen_msgs: set[str] = set()
    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    cost = 0.0
    cost_seen = False
    paused_total = 0.0
    paused_since: datetime | None = None
    started: datetime | None = None
    last_text = ""
    last_ts: datetime | None = None
    notes: list[dict[str, Any]] = []  # messages sent to the agent

    for e in entries:
        ts = _parse(e.get("ts"))
        if ts:
            started = started or ts
            last_ts = ts
        et = e.get("type")
        if et == "control":
            if e.get("action") == "paused" and paused_since is None:
                paused_since = ts
            elif e.get("action") == "resumed" and paused_since is not None and ts:
                paused_total += _secs(paused_since, ts) or 0
                paused_since = None
        elif et == "user_message":
            notes.append({"text": e.get("text", ""), "ts": e.get("ts"), "immediate": bool(e.get("immediate"))})
        elif et == "assistant":
            mid = e.get("msg_id")
            u = e.get("usage")
            if u and (mid is None or mid not in seen_msgs):
                if mid:
                    seen_msgs.add(mid)
                for k in usage:
                    usage[k] += int(u.get(k) or 0)
            if e.get("tool"):
                step = {
                    "id": e.get("tool_id") or f"step-{len(steps)}",
                    "tool": e["tool"],
                    "detail": e.get("detail") or "",
                    "status": "running",
                    "started_at": e.get("ts"),
                    "finished_at": None,
                    "duration_s": None,
                    "result": None,
                }
                steps.append(step)
                by_id[str(step["id"])] = step
            elif e.get("text"):
                last_text = str(e["text"])
        elif et == "tool_result":
            called = by_id.get(str(e.get("tool_id")))
            if called is not None:
                called["status"] = "error" if e.get("is_error") else "ok"
                called["finished_at"] = e.get("ts")
                called["duration_s"] = _secs(_parse(called["started_at"]), ts)
                called["result"] = e.get("result") or ""
        elif et == "result" and e.get("cost_usd") is not None:
            cost += float(e["cost_usd"])
            cost_seen = True

    if paused_since is not None:
        paused_total += _secs(paused_since, end) or 0
    start = started or _parse(job.get("created_at"))
    elapsed = max(0.0, (_secs(start, end) or 0) - paused_total)

    for s in steps:  # tool calls that never got a result although the job is over
        if s["status"] == "running" and not active:
            s["status"] = "aborted"
        if s["status"] == "running" and status == "paused":
            s["status"] = "paused"

    running = [s for s in steps if s["status"] in ("running", "paused")]
    if status == "paused":
        current = {"kind": "paused", "label": "Pausiert", "tool": running[-1]["tool"] if running else None}
    elif status == "queued":
        current = {"kind": "waiting", "label": "Wartet auf Start"}
    elif running:
        s = running[-1]
        label = f"{s['tool']}: {s['detail']}" if s["detail"] else str(s["tool"])
        current = {"kind": "tool", "label": label, "tool": s["tool"], "since": s["started_at"]}
    elif status == "running":
        current = {"kind": "thinking", "label": "Denkt nach …", "since": last_ts.isoformat() if last_ts else None}
    else:
        done = {"completed": "Abgeschlossen", "failed": "Fehlgeschlagen", "cancelled": "Abgebrochen"}
        current = {"kind": "done", "label": done.get(status, status)}

    total = usage["input"] + usage["output"] + usage["cache_read"] + usage["cache_creation"]
    result = job.get("result") or {}
    return {
        "job_id": job.get("id"),
        "title": job.get("title"),
        "status": status,
        "model": job.get("model"),
        "effort": job.get("effort"),
        "branch": job.get("branch"),
        "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at"),
        "elapsed_s": round(elapsed, 1),
        "snapshot_at": now.isoformat(),
        "current": current,
        "steps": steps[-MAX_STEPS:],
        "step_count": len(steps),
        "running_tools": running,
        "last_text": last_text,
        "messages": notes[-20:],
        "pending_messages": pending_messages,
        "usage": {
            "input_tokens": usage["input"],
            "output_tokens": usage["output"],
            "cache_read_tokens": usage["cache_read"],
            "cache_creation_tokens": usage["cache_creation"],
            "total_tokens": total,
            "cost_usd": round(cost, 4) if cost_seen else None,
            # the CLI reports the cost only when a run ends: while running it is a lower bound
            "cost_final": cost_seen and not active,
        },
        "error": result.get("error") if isinstance(result, dict) else None,
        "can_pause": status == "running",
        "can_resume": status == "paused",
        "can_cancel": active,
        "can_message": active,
    }
