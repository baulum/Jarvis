"""Policy-enforcing tool executor with approvals and an idempotency ledger.

Every tool invocation — from Hermes (via MCP), playbooks, the scheduler, Telegram or the
phone — goes through ``ToolExecutor.execute``. The order of checks is fixed:

1. tool exists and is enabled            5. approval required? -> create/return approval
2. arguments validate against schema      6. idempotency ledger (write tools)
3. kill switch                            7. execute with timeout, audit, taint session
4. role ceiling + rate limits
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jsonschema
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from jarvis.config import RISK_ORDER, Permissions
from jarvis.db import session_scope
from jarvis.db.models import Approval, ChatSession, ToolExecution
from jarvis.events import activity, bus, current_activity
from jarvis.security.audit import AuditUnavailable, audit
from jarvis.security.auth import Principal
from jarvis.security.guards import KillSwitch, rate_limiter
from jarvis.tools.approval_mode import ApprovalPolicy
from jarvis.tools.registry import ToolContext, ToolRegistry, ToolSpec

logger = logging.getLogger(__name__)
Status = Literal["ok", "approval_required", "denied", "error", "duplicate", "in_progress"]


@dataclass(slots=True)
class ToolResult:
    status: Status
    tool: str
    data: Any = None
    message: str = ""
    approval_id: uuid.UUID | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status, "tool": self.tool}
        if self.message:
            out["message"] = self.message
        if self.data is not None:
            out["data"] = self.data
        if self.approval_id:
            out["approval_id"] = str(self.approval_id)
        return out


def canonical_args(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)


def args_hash(tool: str, args: dict[str, Any]) -> str:
    return hashlib.sha256(f"{tool}:{canonical_args(args)}".encode()).hexdigest()


# Tools whose arguments are content Sir typed/pasted (could be passwords): the audit log keeps only the length.
_CONTENT_KEYS = {"text", "value", "content", "string"}
_CONTENT_ARG_TOOLS = {"mac_type_text", "mac_clipboard_set", "mac_ui_set_value"}
LEDGER_STALE_AFTER = timedelta(minutes=15)


def audit_args(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool in _CONTENT_ARG_TOOLS:
        return {k: f"[{len(v)} Zeichen]" if k in _CONTENT_KEYS and isinstance(v, str) else v for k, v in args.items()}
    return {k: (v[:1500] + "…" if isinstance(v, str) and len(v) > 1500 else v) for k, v in args.items()}


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        permissions: Permissions,
        kill_switch: KillSwitch,
        approval_policy: ApprovalPolicy | None = None,
    ) -> None:
        self.registry = registry
        self.permissions = permissions
        self.kill_switch = kill_switch
        self.approval_policy = approval_policy

    # ------------------------------------------------------------------ public API
    async def execute(self, name: str, args: dict[str, Any] | None, ctx: ToolContext) -> ToolResult:
        args = dict(args or {})
        spec = self.registry.get(name)
        if spec is None:
            return ToolResult("error", name, message=f"Unbekanntes Werkzeug: {name}")
        if not self.registry.is_enabled(spec):
            return ToolResult(
                "denied", name, message=f"{name} ist deaktiviert oder der Connector ist nicht eingerichtet."
            )
        try:
            jsonschema.validate(args, spec.parameters)
        except jsonschema.ValidationError as e:
            return ToolResult("error", name, message=f"Ungültige Argumente: {e.message}")

        risk = self.registry.effective_risk(spec)
        role = self.permissions.roles.get(ctx.principal.role)
        if role is None:
            return ToolResult("denied", name, message=f"Unbekannte Rolle {ctx.principal.role}")

        if self.kill_switch.engaged and RISK_ORDER[risk] > RISK_ORDER["read"]:
            await audit(ctx.principal.label, f"tool.{name}", outcome="blocked_killswitch")
            return ToolResult("denied", name, message="Not-Aus ist aktiv. Aktionen sind gesperrt.")

        if RISK_ORDER[risk] > RISK_ORDER[role.max_risk]:
            await audit(
                ctx.principal.label,
                f"tool.{name}",
                outcome="denied_role",
                details={"risk": risk, "role": ctx.principal.role},
            )
            return ToolResult(
                "denied", name, message=f"Rolle '{ctx.principal.role}' darf {name} (Risiko {risk}) nicht ausführen."
            )

        rl = self.permissions.rate_limits
        if not rate_limiter.check(f"tools:{ctx.principal.label}", rl.tool_calls_per_minute, 60):
            return ToolResult("denied", name, message="Rate-Limit für Werkzeugaufrufe erreicht.")
        if RISK_ORDER[risk] >= RISK_ORDER["high"] and not rate_limiter.check(
            f"high:{ctx.principal.label}", rl.high_risk_per_hour, 3600
        ):
            return ToolResult("denied", name, message="Rate-Limit für Aktionen mit hohem Risiko erreicht.")

        if not ctx.approved:
            decision, reason = await self._approval_decision(spec, risk, ctx)
            if decision == "deny":
                await audit(ctx.principal.label, f"tool.{name}", outcome="denied_policy", details={"risk": risk})
                return ToolResult("denied", name, message=reason)
            if decision == "ask":
                return await self._request_approval(spec, args, risk, reason, ctx)

        return await self._run(spec, args, risk, ctx)

    # ------------------------------------------------------------------ approvals
    async def _tainted(self, ctx: ToolContext) -> bool:
        if not ctx.session_id:
            return False
        async with session_scope() as s:
            sess = await s.get(ChatSession, ctx.session_id)
            return bool(sess is not None and sess.tainted)

    async def _approval_decision(self, spec: ToolSpec, risk: str, ctx: ToolContext) -> tuple[str, str]:
        """auto / ask / deny for this call, from the approval mode (or the role defaults without one)."""
        if RISK_ORDER[risk] == RISK_ORDER["read"]:
            return "auto", ""
        tainted = self.permissions.taint_requires_approval and await self._tainted(ctx)
        if self.approval_policy is not None:
            return self.approval_policy.decide(spec.name, risk, tainted)
        role = self.permissions.roles[ctx.principal.role]
        if RISK_ORDER[risk] > RISK_ORDER[role.auto_approve]:
            return "ask", f"Risikostufe {risk}"
        if tainted:
            return "ask", "Sitzung enthält nicht vertrauenswürdige externe Inhalte"
        return "auto", ""

    async def _request_approval(
        self, spec: ToolSpec, args: dict[str, Any], risk: str, reason: str, ctx: ToolContext
    ) -> ToolResult:
        key = ctx.idempotency_key or (
            f"approval:{args_hash(spec.name, args)}:{ctx.session_id or ctx.task_step_id or ctx.principal.label}"
        )
        summary = spec.describe(args)
        async with session_scope() as s:
            existing = (await s.execute(select(Approval).where(Approval.idempotency_key == key))).scalar_one_or_none()
            if existing is not None and existing.status in ("pending", "approved", "executed"):
                return ToolResult(
                    "approval_required" if existing.status == "pending" else "duplicate",
                    spec.name,
                    approval_id=existing.id,
                    data=(existing.result or {}).get("data"),
                    message=_approval_message(existing.status, summary),
                )
            if existing is not None:  # rejected/expired/failed -> allow a fresh request
                await s.delete(existing)
                await s.flush()
            approval = Approval(
                tool=spec.name,
                args=args,
                summary=summary,
                risk=risk,
                reason=reason,
                requested_by_role=ctx.principal.role,
                requested_by=ctx.principal.label,
                channel=ctx.principal.channel,
                session_id=ctx.session_id,
                task_step_id=ctx.task_step_id,
                idempotency_key=key,
                expires_at=datetime.now(UTC) + timedelta(minutes=self.permissions.approval_ttl_minutes),
            )
            s.add(approval)
            await s.flush()
            approval_id = approval.id
        await audit(
            ctx.principal.label,
            f"approval.request.{spec.name}",
            target=str(approval_id),
            outcome="pending",
            details={"risk": risk, "reason": reason, "summary": summary},
        )
        await bus.publish(
            "approval.requested",
            approval_id=str(approval_id),
            tool=spec.name,
            summary=summary,
            risk=risk,
            reason=reason,
            channel=ctx.principal.channel,
            session_id=str(ctx.session_id) if ctx.session_id else None,
        )
        return ToolResult(
            "approval_required", spec.name, approval_id=approval_id, message=_approval_message("pending", summary)
        )

    async def decide(
        self, approval_id: uuid.UUID, approve: bool, decider: Principal, note: str | None = None
    ) -> dict[str, Any]:
        """Approve or reject. Approved actions are executed immediately (exactly once)."""
        role = self.permissions.roles.get(decider.role)
        if role is None or not role.can_approve:
            raise PermissionError(f"Rolle {decider.role} darf keine Freigaben erteilen")
        async with session_scope() as s:
            ap = await s.get(Approval, approval_id, with_for_update=True)
            if ap is None:
                raise LookupError("Freigabe nicht gefunden")
            if ap.status != "pending":
                return {"status": ap.status, "result": ap.result}
            if ap.expires_at < datetime.now(UTC):
                ap.status = "expired"
                return {"status": "expired"}
            if (
                ap.risk == "critical"
                and approve
                and (decider.role not in self.permissions.critical_approval_roles or decider.device_id is None)
            ):
                raise PermissionError("Kritische Aktionen dürfen nur am gepaarten Desktop-Gerät freigegeben werden")
            ap.status = "approved" if approve else "rejected"
            ap.decided_by = decider.label
            ap.decided_at = datetime.now(UTC)
            ap.decision_note = note
            snapshot = {
                k: getattr(ap, k)
                for k in (
                    "tool",
                    "args",
                    "session_id",
                    "task_step_id",
                    "idempotency_key",
                    "requested_by_role",
                    "requested_by",
                    "channel",
                    "summary",
                )
            }
        await audit(
            decider.label,
            f"approval.{'approve' if approve else 'reject'}.{snapshot['tool']}",
            target=str(approval_id),
            details={"note": note or ""},
        )

        result_payload: dict[str, Any] = {}
        final_status = "rejected"
        if approve:
            requester = Principal(
                id=snapshot["requested_by"], role=snapshot["requested_by_role"], channel=snapshot["channel"]
            )
            ctx = ToolContext(
                principal=requester,
                session_id=snapshot["session_id"],
                task_step_id=snapshot["task_step_id"],
                idempotency_key=f"exec:{snapshot['idempotency_key']}",
                approved=True,
                approval_id=approval_id,
            )
            res = await self.execute(snapshot["tool"], snapshot["args"], ctx)
            result_payload = res.to_dict()
            final_status = "executed" if res.status in ("ok", "duplicate") else "failed"
            async with session_scope() as s:
                ap = await s.get(Approval, approval_id)
                if ap is not None:
                    ap.status = final_status
                    ap.result = result_payload
        await bus.publish(
            "approval.decided",
            approval_id=str(approval_id),
            approved=approve,
            status=final_status,
            tool=snapshot["tool"],
            summary=snapshot["summary"],
            result=result_payload,
            session_id=str(snapshot["session_id"]) if snapshot["session_id"] else None,
            task_step_id=str(snapshot["task_step_id"]) if snapshot["task_step_id"] else None,
        )
        return {"status": final_status, "result": result_payload}

    # ------------------------------------------------------------------ execution
    async def _run(self, spec: ToolSpec, args: dict[str, Any], risk: str, ctx: ToolContext) -> ToolResult:
        ledger_key: str | None = None
        ahash = args_hash(spec.name, args)
        if spec.writes:
            ledger_key = ctx.idempotency_key or f"{ahash}:{ctx.task_step_id or ctx.session_id or 'adhoc'}"
            dup = await self._claim_ledger(ledger_key, spec.name, ahash, ctx.principal.label)
            if dup is not None:
                return dup

        if RISK_ORDER[risk] >= RISK_ORDER["high"]:
            # intent is recorded BEFORE the action; without a working audit log such an action does not run
            try:
                await audit(
                    ctx.principal.label,
                    f"tool.{spec.name}",
                    outcome="started",
                    details={"risk": risk, "args": audit_args(spec.name, args)},
                    strict=True,
                )
            except AuditUnavailable:
                if ledger_key:
                    await self._finish_ledger(ledger_key, "error", None, "audit unavailable", 0)
                return ToolResult(
                    "error", spec.name, message="Audit-Protokoll nicht verfügbar – Aktion nicht ausgeführt."
                )
        call_id = uuid.uuid4().hex[:12]
        task_id = ctx.extra.get("task_id") or current_activity().task_id
        await bus.publish(
            "tool.started",
            tool=spec.name,
            connector=spec.connector,
            call_id=call_id,
            risk=risk,
            actor=ctx.principal.label,
            session_id=str(ctx.session_id) if ctx.session_id else None,
            task_id=task_id,
        )
        t0 = time.perf_counter()
        status: Status = "ok"
        message = ""
        data: Any = None
        try:
            with activity(
                session_id=str(ctx.session_id) if ctx.session_id else None,
                task_id=task_id,
                tool=spec.name,
            ):
                data = await asyncio.wait_for(spec.handler(ctx, args), timeout=spec.timeout_s)
        except TimeoutError:
            status, message = "error", f"Zeitüberschreitung nach {spec.timeout_s:.0f}s"
        except ToolUserError as e:
            status, message = "error", str(e)
        except Exception as e:
            logger.exception("Tool %s failed", spec.name)
            status, message = "error", f"{type(e).__name__}: {e}"[:500]
        duration_ms = int((time.perf_counter() - t0) * 1000)

        if ledger_key:
            await self._finish_ledger(ledger_key, status, data, message, duration_ms)
        if status == "ok" and spec.untrusted_output and ctx.session_id:
            await self._taint(ctx.session_id, spec.name)

        await audit(
            ctx.principal.label,
            f"tool.{spec.name}",
            outcome=status,
            details={
                "risk": risk,
                "args": audit_args(spec.name, args),
                "duration_ms": duration_ms,
                "approval_id": str(ctx.approval_id) if ctx.approval_id else None,
                "error": message if status != "ok" else None,
            },
        )
        await bus.publish(
            "tool.finished",
            tool=spec.name,
            connector=spec.connector,
            call_id=call_id,
            task_id=task_id,
            status=status,
            duration_ms=duration_ms,
            actor=ctx.principal.label,
            message=message,
            session_id=str(ctx.session_id) if ctx.session_id else None,
        )
        return ToolResult(status, spec.name, data=data, message=message, meta={"duration_ms": duration_ms})

    async def _claim_ledger(self, key: str, tool: str, ahash: str, actor: str) -> ToolResult | None:
        try:
            async with session_scope() as s:
                s.add(ToolExecution(idempotency_key=key, tool=tool, args_hash=ahash, status="running", actor=actor))
            return None
        except IntegrityError:
            pass
        async with session_scope() as s:
            row = await s.get(ToolExecution, key)
            if row is None:
                return None
            if row.status == "succeeded":
                return ToolResult(
                    "duplicate",
                    tool,
                    data=row.result.get("data"),
                    message="Diese Aktion wurde bereits ausgeführt; Ergebnis aus dem Ledger.",
                )
            if row.status == "running":
                if datetime.now(UTC) - row.created_at > LEDGER_STALE_AFTER:
                    # the process died mid-action: outcome unknown → release the key, but do not silently re-run it
                    row.status = "failed"
                    row.result = {"message": "unterbrochen"}
                    return ToolResult(
                        "error",
                        tool,
                        message="Die frühere Ausführung wurde unterbrochen (Ergebnis unklar). Bitte zuerst prüfen, "
                        "ob sie durchgekommen ist; ein neuer Versuch ist danach möglich.",
                    )
                return ToolResult("in_progress", tool, message="Diese Aktion läuft bereits.")
            # failed -> allow retry by re-claiming
            row.status = "running"
            row.created_at = datetime.now(UTC)
            return None

    async def _finish_ledger(self, key: str, status: str, data: Any, message: str, duration_ms: int) -> None:
        async with session_scope() as s:
            row = await s.get(ToolExecution, key)
            if row is not None:
                row.status = "succeeded" if status == "ok" else "failed"
                row.result = json.loads(json.dumps({"data": data, "message": message}, default=str))
                row.duration_ms = duration_ms
                row.finished_at = datetime.now(UTC)

    async def taint(self, session_id: uuid.UUID, source: str) -> None:
        """Marks a conversation as containing untrusted content (e.g. a document attachment with injection hits)."""
        await self._taint(session_id, source)

    async def _taint(self, session_id: uuid.UUID, source: str) -> None:
        async with session_scope() as s:
            sess = await s.get(ChatSession, session_id)
            if sess is not None:
                if not sess.tainted:
                    await bus.publish("session.tainted", session_id=str(session_id), source=source)
                sess.tainted = True
                sources = list(sess.taint_sources or [])
                if source not in sources:
                    sess.taint_sources = [*sources, source]


class ToolUserError(Exception):
    """A tool failure with a message that is safe and useful to show to the user/model."""


def _approval_message(status: str, summary: str) -> str:
    if status == "pending":
        return (
            f"FREIGABE ERFORDERLICH: '{summary}' wurde Sir zur Freigabe vorgelegt "
            "(in der Desktop-App; per Telegram nur, wenn Telegram gekoppelt ist – erwähne Telegram sonst nicht). "
            "Führe diese Aktion NICHT erneut aus; sie wird nach der "
            "Freigabe automatisch ausgeführt. Teile Sir kurz mit, dass die Freigabe aussteht."
        )
    if status == "executed":
        return f"'{summary}' wurde bereits freigegeben und ausgeführt."
    return f"'{summary}' wurde bereits freigegeben und wird ausgeführt."
