"""Application container: builds, starts and stops all core components."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import psutil
from sqlalchemy import func, select

from jarvis.activity import ActivityPipeline
from jarvis.agent.claude_agent import ClaudeCodeAgent, ClaudeOneShot
from jarvis.agent.hermes_setup import HermesSupervisor, sync_soul
from jarvis.agent.llm import HermesClient, OllamaClient
from jarvis.agent.model_router import ModelRouter
from jarvis.agent.orchestrator import Orchestrator
from jarvis.agent.personas import PersonaStore
from jarvis.agent.skills import SkillCatalog
from jarvis.agent.usage import UsageTracker
from jarvis.backup.service import BackupService
from jarvis.coding.runner import CodingRunner
from jarvis.config import Permissions, Personality, Settings, load_permissions, load_personalities
from jarvis.connectors.attachments import AttachmentsConnector
from jarvis.connectors.base import ConnectorManager
from jarvis.connectors.browser import BrowserConnector, BrowserService
from jarvis.connectors.coding import CodingConnector
from jarvis.connectors.fish_audio import FishAudioConnector
from jarvis.connectors.generic import HttpApiConnector, ShellConnector, WebhookConnector
from jarvis.connectors.github import GitHubConnector
from jarvis.connectors.google import GoogleConnector
from jarvis.connectors.kie_creative import KieCreativeConnector
from jarvis.connectors.lexware import LexwareConnector
from jarvis.connectors.mac import BridgeClient, MacConnector
from jarvis.connectors.memory_obsidian import ObsidianMemoryConnector
from jarvis.connectors.routing import RoutingConnector
from jarvis.connectors.system import SystemConnector
from jarvis.connectors.telegram import TelegramConnector
from jarvis.connectors.telephony import TelephonyConnector
from jarvis.connectors.vercel import VercelConnector
from jarvis.db import init_engine, session_scope
from jarvis.db.models import Approval, Task, UsageRecord
from jarvis.devices.connector import DevicesConnector
from jarvis.devices.hub import DeviceHub
from jarvis.events import Event, bus, spawn
from jarvis.graph import GraphBuilder
from jarvis.jobs.engine import TaskEngine
from jarvis.jobs.notify import Notifier
from jarvis.jobs.playbooks import register_playbooks
from jarvis.jobs.scheduler import Scheduler
from jarvis.mcp.client import MCPClientHub
from jarvis.memory.embeddings import LocalEmbedder
from jarvis.memory.service import MemoryService
from jarvis.memory.vault import Vault
from jarvis.mobile.alerts import AlertEngine
from jarvis.mobile.calls import CallService
from jarvis.mobile.gateway import GatewayServer
from jarvis.mobile.livekit_server import LiveKitServer
from jarvis.mobile.location import LocationStore
from jarvis.mobile.ota.service import AppUpdateService
from jarvis.mobile.pairing import PairingService
from jarvis.mobile.phone_control import PhoneControl
from jarvis.mobile.push import PushService
from jarvis.platform import create_adapter, set_adapter
from jarvis.secrets import secret_store
from jarvis.security.audit import audit
from jarvis.security.guards import KillSwitch
from jarvis.social.platforms import PLATFORM_CONNECTORS
from jarvis.social.service import SocialMediaService
from jarvis.social.service import playbook as social_playbook
from jarvis.social.tools import SocialMediaConnector
from jarvis.telephony.service import TelephonyService
from jarvis.tools.approval_mode import ApprovalPolicy
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry
from jarvis.uptik.service import UptikService
from jarvis.uptik.tools import UptikConnector
from jarvis.voice.speech import STTService, TTSService
from jarvis.voice.wakeup import WakeupStore
from jarvis.whatsapp.connector import WhatsAppConnector
from jarvis.whatsapp.service import WhatsAppService

logger = logging.getLogger(__name__)
VERSION = "0.2.0"
AGENT_MCP_SECRET = "agent-mcp-token"  # noqa: S105 - Keychain account name


class AppState:
    def __init__(
        self, settings: Settings, personality: Personality | None = None, permissions: Permissions | None = None
    ) -> None:
        self.settings = settings
        # Jarvis + further personas (config/personalities/); the one Sir chose is ``self.personality``
        self.personas = PersonaStore(settings.paths.data_dir / "persona.json", load_personalities(personality))
        self.permissions = permissions or load_permissions()
        self.started_at = time.time()
        self.degraded: str | None = None  # set when startup hit a problem the UI/health must show
        for p in (
            settings.paths.data_dir,
            settings.paths.log_dir,
            settings.paths.workspaces_dir,
            settings.paths.projects_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)
        settings.paths.data_dir.chmod(0o700)

        # The host's PlatformAdapter (MacAdapter on the Mac: the existing code paths). Tools that need a capability
        # this host lacks disappear from the agent's list and answer "nicht verfügbar – <Grund>" instead of crashing.
        self.platform = create_adapter(data_dir=settings.paths.data_dir)
        set_adapter(self.platform)
        self.kill_switch = KillSwitch(settings.paths.data_dir)
        self.registry = ToolRegistry(self.permissions)
        self.registry.set_capabilities(self.platform.capabilities())
        self.approval_policy = ApprovalPolicy(settings.paths.data_dir / "approval-mode.json")
        self.executor = ToolExecutor(self.registry, self.permissions, self.kill_switch, self.approval_policy)
        self.wakeup = WakeupStore(settings.paths.data_dir / "wakeup.json", settings.voice.wakeup)
        # Ollama is only used by the optional legacy Hermes provider; the default (Claude) never touches it.
        self.ollama: OllamaClient | None = (
            OllamaClient(settings.llm.ollama_url, settings.llm.keep_alive)
            if settings.agent.provider == "hermes"
            else None
        )
        self.embedder = LocalEmbedder(
            settings.paths.data_dir / "models" / "fastembed",
            settings.llm.embedding_model,
            settings.llm.embedding_dimensions,
        )
        self.hermes: HermesClient | None = None
        self.hermes_supervisor = HermesSupervisor(self)
        self.skills = SkillCatalog(settings.paths.data_dir)
        self.usage = UsageTracker(settings.paths.data_dir / "agent-usage.json")
        self.oneshot = ClaudeOneShot(self)
        self.model_router = ModelRouter(
            settings.agent.model, settings.agent.auto_downgrade, settings.paths.data_dir / "agent-model.json"
        )
        self.model_router.usage = self.usage
        self.agent: ClaudeCodeAgent | None = ClaudeCodeAgent(self) if settings.agent.provider == "claude_code" else None
        self.memory = MemoryService(Vault(settings.paths.vault_dir), self.embedder, self.structured_llm)
        self.bridge = BridgeClient(settings.paths.data_dir)
        self.browser = BrowserService(
            settings.browser.headless,
            settings.browser.default_timeout_ms,
            engine=settings.browser.engine,
            profile_dir=settings.paths.data_dir / "edge-profile",
            antibrow_profile=settings.browser.antibrow_profile,
        )
        self.stt = STTService(settings.voice.stt.server_url, settings.voice.language)
        self.tts = TTSService(settings.voice.tts, settings.paths.data_dir / "tts-cache")
        self.tts.set_voice(self.personality.voice, self.personality.system_voice)
        self.orchestrator = Orchestrator(self)
        self.tasks = TaskEngine(self)
        self.scheduler = Scheduler(self)
        self.notifier = Notifier(self)
        self.usage.notify = lambda level, title, body: self.notifier.notify("agent.usage", title, body, level=level)
        self.usage.on_change = lambda status: spawn(bus.publish("agent.usage", **status), name="usage-event")
        self.backup = BackupService(settings, self.notifier)
        self.coding = CodingRunner(self)
        self.telephony: TelephonyService | None = TelephonyService(self) if settings.telephony.enabled else None
        self.connectors = ConnectorManager(self)
        self.mcp_hub = MCPClientHub(self)
        self.voice_state: dict[str, Any] = {"connected": False}
        self.graph = GraphBuilder(self)
        self.activity = ActivityPipeline(bus)
        # Mobile companion: TLS gateway, pairing, push (FCM), alert engine, VoIP calls (LiveKit)
        self.pairing = PairingService(settings.mobile.pairing_ttl_s)
        self.gateway = GatewayServer(settings)
        self.livekit = LiveKitServer(settings.mobile.livekit, settings.paths.data_dir, settings.paths.log_dir)
        self.push = PushService(settings.mobile.firebase)
        self.alerts = AlertEngine(self)
        self.calls = CallService(self)
        self.location = LocationStore(self.push)
        self.phone = PhoneControl(self.push)
        # OTA updates of the Android app: signed APK when the app's code changes on main (jarvis.mobile.ota)
        self.app_update: AppUpdateService | None = (
            AppUpdateService(self) if settings.mobile.enabled and settings.mobile.app_update.enabled else None
        )
        # Jarvis Device Agents: remote computers (Windows, Linux, Mac) connected over Tailscale/LAN
        self.device_hub = DeviceHub(settings.devices.command_timeout_s, lambda: self.kill_switch.engaged)
        self.device_hub.on_paired_change = self._on_devices_paired_change
        self.device_pairing = PairingService(settings.devices.pairing_ttl_s)
        # WhatsApp: one central connection on this server (Node sidecar, supervised), shared by every client
        self.whatsapp = WhatsAppService(self)
        # Daily PJE Systems social video (jarvis.social): schedule → playbook → Remotion/FFmpeg → library → e-mail
        self.social = SocialMediaService(self)
        # UpTik: publishing over the platforms' web pages in a real browser (jarvis.uptik), off until Sir enables it
        self.uptik = UptikService(self)

        for conn_cls in (
            SystemConnector,
            ObsidianMemoryConnector,
            MacConnector,
            DevicesConnector,
            BrowserConnector,
            ShellConnector,
            CodingConnector,
            LexwareConnector,
            GoogleConnector,
            TelegramConnector,
            GitHubConnector,
            VercelConnector,
            HttpApiConnector,
            WebhookConnector,
            FishAudioConnector,
            KieCreativeConnector,
            TelephonyConnector,
            RoutingConnector,
            AttachmentsConnector,
            WhatsAppConnector,
            SocialMediaConnector,
            UptikConnector,
        ):
            self.connectors.add(conn_cls(self))
        for platform_cls in PLATFORM_CONNECTORS:  # Instagram, Facebook, TikTok, YouTube – off until Sir connects
            self.connectors.add(platform_cls(self))
        self._register_phone_tool()
        self._register_mobile_tools()
        register_playbooks(self)
        self.tasks.register(social_playbook(self.social))

    def _register_phone_tool(self) -> None:
        from jarvis.tools.registry import ToolContext, ToolSpec

        async def call_owner(ctx: ToolContext, a: dict[str, Any]) -> Any:
            if self.telephony is None:
                from jarvis.tools.executor import ToolUserError

                raise ToolUserError("Telefonie ist deaktiviert")
            try:
                return await self.telephony.call_owner(a["message"], reason="agent")
            except RuntimeError as e:
                from jarvis.tools.executor import ToolUserError

                raise ToolUserError(str(e)) from e

        self.registry.register(
            ToolSpec(
                "phone_call_owner",
                "Klassischer Anruf über das Telefonnetz (Twilio, kostenpflichtig) an Sirs Rufnummer. "
                "Für „ruf mich an“ / „ruf mich am Handy an“ IMMER zuerst mobile_call_owner "
                "(Anruf über die J.A.R.V.I.S.-App) verwenden; dieses Werkzeug nur, wenn Sir ausdrücklich "
                "einen normalen Telefonanruf verlangt.",
                {
                    "type": "object",
                    "properties": {"message": {"type": "string", "minLength": 3}},
                    "required": ["message"],
                    "additionalProperties": False,
                },
                call_owner,
                "high",
                "system",
                writes=True,
                summarize=lambda a: f"Sir anrufen: {a['message'][:120]}",
            )
        )

    def _register_mobile_tools(self) -> None:
        from jarvis.mobile.phone_tools import phone_tools
        from jarvis.mobile.tools import mobile_tools

        for spec in (*mobile_tools(self), *phone_tools(self)):
            self.registry.register(spec)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        init_engine(self.settings)
        if self.settings.devices.enabled:
            try:
                await self.device_hub.load_paired()
            except Exception:
                logger.exception("Loading paired device agents failed")
        await self.connectors.load()
        try:  # in-process embedding model for semantic memory search (loads in a worker thread)
            await self.embedder.embed(["warm-up"])
        except Exception:
            logger.warning("Embedding model failed to load", exc_info=True)
        try:
            await self.memory.start(self.settings.paths.data_dir / "memory-embedding-model")
        except Exception:
            logger.exception("Memory engine failed to start (embeddings?)")
        interrupted_jobs = await self.coding.resume_interrupted()
        await self.tasks.start()
        await self.scheduler.start()
        await self.social.start()
        await self.backup.start()
        self.notifier.start()
        if self.settings.mobile.enabled:
            await self.livekit.start()
            self.alerts.start()
            self.calls.start()
        if self.app_update is not None:
            self.app_update.start()
        await self.mcp_hub.start()
        if self.agent is not None:
            await self._start_claude_agent()
        elif self.settings.hermes.enabled:
            sync_soul(self)
            key = secret_store.get(self.settings.hermes.api_key_secret)
            if key:
                self.hermes = HermesClient(self.settings.hermes.api_url, key, self.settings.hermes.request_timeout_s)
                await self.hermes_supervisor.start()
            else:
                logger.warning("Hermes not provisioned yet – run `jarvisctl setup-hermes`")
        bus.subscribe("system.killswitch", self._on_killswitch)
        # images/videos generated after an approval still appear in the conversation that asked for them
        bus.subscribe("approval.decided", self.orchestrator.on_approval_decided)
        await audit("system", "core.start", details={"version": VERSION})
        if interrupted_jobs:
            spawn(self._report_interrupted_jobs(interrupted_jobs), name="coding-interrupted")
        # this start may be the restart after a job on Jarvis itself: record its outcome in that job
        spawn(self.coding.finish_self_restart(), name="coding-self-restart-result")
        if self.settings.mobile.enabled:
            from jarvis.mobile.alerts import Alert

            spawn(
                self.alerts.dispatch(
                    Alert(
                        "system",
                        "J.A.R.V.I.S. ist wieder online",
                        f"{self.personality.owner_title}, ich bin wieder erreichbar.",
                        event_key=f"system:online:{int(self.started_at)}",
                        deep_link="jarvis://home",
                        allow_call=False,
                    )
                ),
                name="mobile-online",
            )

    async def _report_interrupted_jobs(self, jobs: list[tuple[uuid.UUID, str]]) -> None:
        """A restart ends running coding jobs; without a notification they would just sit there as 'failed'."""
        for job_id, title in jobs:
            await self.notifier.notify(
                "coding.interrupted",
                f"Coding-Job unterbrochen: {title}",
                "Jarvis wurde neu gestartet. Mit „Wiederholen“ setzt Claude Code die Arbeit an derselben Stelle fort.",
                level="warning",
                data={"job_id": str(job_id), "status": "interrupted", "title": title},
            )

    async def _start_claude_agent(self) -> None:
        from jarvis.security.auth import create_device

        token = secret_store.get(AGENT_MCP_SECRET) or secret_store.get("hermes-mcp-token")
        if not token:
            _, token = await create_device("claude-code-agent", "agent")
            secret_store.set(AGENT_MCP_SECRET, token)
        assert self.agent is not None
        self.skills.build_plugin_dir()
        self.agent.write_mcp_config(token)
        await self.agent.start()

    async def structured_llm(self, system: str, prompt: str, schema: dict[str, Any]) -> Any:
        """Structured completion for utility tasks — Claude (subscription) or, with provider=hermes, Ollama."""
        if self.agent is not None:
            return await self.oneshot.run(prompt, schema=schema, system=system)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        assert self.ollama is not None
        return await self.ollama.chat_json(self.settings.llm.utility_model, messages, schema)

    async def stop(self) -> None:
        if self.agent is not None:
            await self.agent.stop()
        await self.hermes_supervisor.stop()
        await self.coding.shutdown()
        await self.tasks.stop()
        await self.scheduler.stop()
        await self.social.stop()
        await self.backup.stop()
        await self.memory.stop()
        await self.alerts.stop()
        if self.app_update is not None:
            await self.app_update.stop()
        await self.livekit.stop()
        await self.push.close()
        await self.location.stop()
        await self.connectors.shutdown()
        await self.mcp_hub.stop()
        await self.browser.close()
        if self.ollama is not None:
            await self.ollama.close()
        if self.hermes:
            await self.hermes.close()
        await self.bridge.close()

    @property
    def personality(self) -> Personality:
        """The active persona – everything that speaks (prompt, greetings, error phrases, voice) reads it here."""
        return self.personas.active

    async def set_persona(self, pid: str) -> Personality:
        """Switch the personality without a restart; raises ``KeyError`` for an unknown one.

        The voice applies to replies that start from now on (a reply being spoken keeps its voice), the system prompt
        from the next turn (``ClaudeCodeAgent.reload``: idle processes now, busy ones after their turn, history via
        --resume). Desktop and phone follow the ``persona.changed`` event."""
        p = self.personas.set(pid)
        self.tts.set_voice(p.voice, p.system_voice)
        if self.settings.hermes.enabled:
            sync_soul(self)
        if self.agent is not None:
            await self.agent.reload()
        await bus.publish("persona.changed", **self.personas.to_dict())
        return p

    async def _on_devices_paired_change(self) -> None:
        """The first device agent was paired (or the last one revoked): the device_* tools appear/disappear."""
        await self.connectors.check("devices")
        await self.hermes_supervisor.restart()
        if self.agent is not None:
            await self.agent.reload()

    async def _on_killswitch(self, ev: Event) -> None:
        engaged = bool(ev.data.get("engaged"))
        await self.bridge.set_killswitch(engaged)
        await self.device_hub.broadcast_killswitch(engaged)
        if engaged:
            await self.coding.cancel_all()
            await self.orchestrator.interrupt()
            await audit(str(ev.data.get("actor", "?")), "killswitch.engage", details={"reason": ev.data.get("reason")})
        else:
            await audit(str(ev.data.get("actor", "?")), "killswitch.release")

    # ------------------------------------------------------------------ status
    def voice_daemon_health(self) -> str:
        """``muted``: connected, but microphone and speaker are closed (mute in the app) – Jarvis neither hears nor
        speaks on the Mac. Reporting that as "ok" hid exactly this state (2026-10-28); "error": the daemon reported a
        problem such as an unavailable microphone."""
        v = self.voice_state
        if not v.get("connected"):
            return "offline"
        if v.get("muted"):
            return "muted"
        return "error" if v.get("state") == "error" else "ok"

    async def system_status(self, brief: bool = False) -> dict[str, Any]:
        comps: dict[str, str] = {}
        comps["embeddings"] = "ok" if self.embedder.loaded else "loading"
        if self.ollama is not None:  # legacy Hermes provider only
            try:
                await self.ollama.version()
                comps["ollama"] = "ok"
            except Exception:
                comps["ollama"] = "down"
        if self.agent is not None:
            comps["agent"] = (
                "ok"
                if self.agent.available() and not self.agent.last_error
                else ("degraded" if self.agent.available() else "down")
            )
        else:
            comps["hermes"] = (
                "ok"
                if self.hermes and await self.hermes.health()
                else ("not_configured" if self.hermes is None else "down")
            )
        try:
            async with session_scope() as s:
                await s.execute(select(1))
            comps["database"] = "ok"
        except Exception:
            comps["database"] = "down"
        comps["stt"] = "ok" if await self.stt.healthy() else "down"
        comps["voice_daemon"] = self.voice_daemon_health()
        for cid, h in self.connectors.health.items():
            comps[f"connector:{cid}"] = h.status
        if self.device_hub.has_paired:
            comps["devices"] = "ok" if self.device_hub.online() else "offline"
        async with session_scope() as s:
            pending = (
                await s.execute(select(func.count()).select_from(Approval).where(Approval.status == "pending"))
            ).scalar_one()
            running = (
                await s.execute(
                    select(func.count())
                    .select_from(Task)
                    .where(
                        Task.status.in_(
                            ["running", "queued", "paused", "waiting_approval", "waiting_input", "waiting_external"]
                        )
                    )
                )
            ).scalar_one()
        out: dict[str, Any] = {
            "version": VERSION,
            "uptime_s": int(time.time() - self.started_at),
            "kill_switch": {"engaged": self.kill_switch.engaged, "reason": self.kill_switch.reason()},
            "components": comps,
            "degraded": self.degraded,
            "pending_approvals": pending,
            "open_tasks": running,
        }
        if brief:
            return out
        vm = psutil.virtual_memory()
        disk = shutil.disk_usage(str(self.settings.paths.data_dir))
        loaded: list[Any] = []
        if self.ollama is not None:
            try:
                loaded = await self.ollama.loaded_models()
            except Exception:
                loaded = []
        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        async with session_scope() as s:
            usage = (
                await s.execute(
                    select(
                        UsageRecord.purpose,
                        func.count(),
                        func.sum(UsageRecord.prompt_tokens),
                        func.sum(UsageRecord.completion_tokens),
                        func.avg(UsageRecord.latency_ms),
                    )
                    .where(UsageRecord.ts >= since)
                    .group_by(UsageRecord.purpose)
                )
            ).all()
        proc = psutil.Process()
        out["resources"] = {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_total": vm.total,
            "memory_used": vm.used,
            "memory_percent": vm.percent,
            "disk_free": disk.free,
            "disk_total": disk.total,
            "core_rss": proc.memory_info().rss,
            "models_loaded": [
                {
                    "name": m.get("name"),
                    "size": m.get("size"),
                    "size_vram": m.get("size_vram"),
                    "expires_at": m.get("expires_at"),
                }
                for m in loaded
            ],
        }
        out["usage_today"] = [
            {
                "purpose": p,
                "requests": n,
                "prompt_tokens": int(pt or 0),
                "completion_tokens": int(ct or 0),
                "avg_latency_ms": round(float(lat or 0)),
            }
            for p, n, pt, ct, lat in usage
        ]
        out["tts"] = await self.tts.status()
        out["telephony"] = self.telephony.status() if self.telephony else {"enabled": False}
        out["mobile"] = {
            "gateway": self.gateway.status(),
            "livekit": await self.livekit.status(),
            "push_configured": self.push.fcm.configured(),
        }
        out["mcp_servers"] = self.mcp_hub.status()
        out["platform"] = self.platform.info()
        out["devices"] = self.device_hub.describe()
        out["voice"] = self.voice_state
        out["hermes_restarts"] = self.hermes_supervisor.restarts
        out["agent"] = (
            {**self.agent.status(), "routing": self.model_router.status()}
            if self.agent is not None
            else {"provider": "hermes"}
        )
        return out


async def wait_forever() -> None:
    await asyncio.Event().wait()
