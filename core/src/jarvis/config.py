"""Configuration loading and validation.

Three YAML files in ``config/`` (main settings, personality, permissions) – plus further personalities in
``config/personalities/`` – are parsed into
strict pydantic models. Environment variables of the form ``JARVIS__SECTION__KEY`` override
values in ``jarvis.yaml``. Secrets are never part of the config: only Keychain account names.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = Path(os.environ.get("JARVIS_CONFIG_DIR", REPO_ROOT / "config"))

RiskLevel = Literal["read", "low", "medium", "high", "critical"]
RISK_ORDER: dict[str, int] = {"read": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _expand(p: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(p))))


# Customer sites live here (one folder per customer). Coding jobs for a folder below it work directly in that
# folder instead of a separate worktree under ``paths.workspaces_dir``.
CUSTOMER_SITES_DIR = Path("/Users/paul/Documents/Kunden-Seiten")


class ServerCfg(_Strict):
    host: str = "127.0.0.1"
    port: int = 8765
    public_base_url: str = ""
    cors_origins: list[str] = Field(default_factory=list)


class PathsCfg(_Strict):
    data_dir: Path
    log_dir: Path
    vault_dir: Path
    workspaces_dir: Path
    projects_dir: Path

    @field_validator("*", mode="before")
    @classmethod
    def _exp(cls, v: Any) -> Path:
        return _expand(v)


class DatabaseCfg(_Strict):
    host: str = "localhost"
    port: int = 5432
    name: str = "jarvis"
    user: str = "jarvis"
    password_secret: str = "postgres"


class LlmCfg(_Strict):
    ollama_url: str = "http://127.0.0.1:11434"
    agent_model: str = "jarvis-brain"
    utility_model: str = "jarvis-brain"
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B-Q"  # in-process ONNX (fastembed), no Ollama
    embedding_dimensions: int = 1024
    keep_alive: str = "30m"


class AgentCfg(_Strict):
    provider: Literal["claude_code", "hermes"] = "claude_code"
    claude_binary: str = "claude"
    model: Literal["sonnet", "opus", "haiku"] = "sonnet"
    utility_model: Literal["sonnet", "opus", "haiku"] = "haiku"
    auto_downgrade: bool = True
    idle_timeout_minutes: int = 15
    turn_timeout_s: int = Field(default=900, ge=30)  # a hung Claude turn would block every channel (global run lock)


class HermesCfg(_Strict):
    enabled: bool = True
    profile: str = "jarvis"
    api_url: str = "http://127.0.0.1:8642/p/jarvis"
    api_key_secret: str = "hermes-api-key"
    request_timeout_s: float = 300


class SessionCfg(_Strict):
    idle_timeout_minutes: int = 120
    max_history_messages: int = 30


class SttCfg(_Strict):
    provider: Literal["whisper_cpp"] = "whisper_cpp"
    server_url: str = "http://127.0.0.1:8178"
    model: str = "ggml-large-v3-turbo-q5_0.bin"


class FishAudioCfg(_Strict):
    api_key_secret: str = "fish-audio-api-key"
    reference_id: str = ""
    model: str = "s1"
    streaming: bool = True  # WebSocket live TTS (text in while the answer is still being written)
    latency: Literal["low", "balanced", "normal"] = "balanced"
    chunk_length: int = Field(default=100, ge=100, le=300)  # smaller = earlier first audio


class LocalTtsCfg(_Strict):
    """Jarvis voice on this Mac (scripts/local-tts/server.py, Qwen3-TTS cloning the reference clip)."""

    server_url: str = "http://127.0.0.1:8179"


class TtsCfg(_Strict):
    # "system": the OS voice of this host (macOS say, Windows SAPI, Linux espeak-ng); "macos" means the same.
    provider: Literal["macos", "fish_audio", "local", "system"] = "macos"
    macos_voice: str = "Anna"
    rate: int = 185
    fish_audio: FishAudioCfg = Field(default_factory=FishAudioCfg)
    local: LocalTtsCfg = Field(default_factory=LocalTtsCfg)
    cache_max_chars: int = Field(default=80, ge=0)  # short pieces ("Sehr wohl, Sir.") are synthesised once; 0 = off


class WakeupCfg(_Strict):
    """"Jarvis, wach auf": a staged start-up show – sound, optional line and apps brought to the front.

    Start values only: once Sir changes the routine in the desktop app, ``wakeup.json`` in the data dir
    holds enabled/sound/volume/apps (see ``jarvis.voice.wakeup``)."""

    enabled: bool = True
    sound: str = ""  # path to an MP3 file; empty = built-in power-up sound
    volume: float = Field(default=1.0, ge=0.0, le=1.0)
    say: str = "Zu Ihren Diensten, Sir. Alle Systeme sind online."  # spoken after the sound ("" = silent)
    apps: list[str] = Field(default_factory=list)  # app names / .app paths / bundle ids / https URLs, in this order
    app_delay_s: float = Field(default=0.8, ge=0.0, le=10.0)


class VoiceCfg(_Strict):
    enabled: bool = True
    language: str = "de"
    wake_word: str = "hey_jarvis"
    wake_threshold: float = 0.5
    stt: SttCfg = Field(default_factory=SttCfg)
    tts: TtsCfg = Field(default_factory=TtsCfg)
    end_of_turn_silence_ms: int = 1100  # longest pause before a turn ends (sentence clearly unfinished)
    min_end_of_turn_silence_ms: int = 400  # pause that ends a turn when the transcript is complete
    speculative_stt_ms: int = 280  # start transcribing after this much silence (overlaps STT with endpointing)
    vad_min_rms: float = 180
    max_utterance_s: int = 30
    followup_window_s: int = 6
    barge_in: Literal["auto", "voice", "wake_word"] = "auto"  # auto: by voice when echo cancellation is active
    barge_in_echo_ratio: float = 1.8  # without echo cancellation: how far above Jarvis' own echo peaks Sir must be
    barge_in_ms: int = 320  # speech needed to interrupt Jarvis (voice barge-in)
    native_audio: bool = True  # Swift helper: streaming playback without gaps (falls back to sounddevice/afplay)
    # macOS voice processing (AEC) also suppressed Sir's voice in the room in a measurement on his Mac
    # (room voice 30 vs. silence 20 RMS) → off by default; barge-in then works with "Hey Jarvis".
    echo_cancellation: bool = False
    conversation_window_s: int = 15  # after a reply Jarvis keeps listening this long – no wake word needed
    wakeup: WakeupCfg = Field(default_factory=WakeupCfg)


class TwilioCfg(_Strict):
    account_sid_secret: str = "twilio-account-sid"
    auth_token_secret: str = "twilio-auth-token"
    from_number: str = ""


class TelephonyCfg(_Strict):
    enabled: bool = True
    provider: Literal["twilio", "local_test"] = "twilio"
    owner_numbers: list[str] = Field(default_factory=list)
    pin_secret: str = "phone-pin"
    reject_unknown_callers: bool = False  # True: only numbers in owner_numbers are answered at all
    max_calls_per_hour: int = 10
    twilio: TwilioCfg = Field(default_factory=TwilioCfg)


Channel = Literal["desktop", "telegram", "phone", "voice", "mobile"]


class NotificationRule(_Strict):
    event: str
    channels: list[Channel]
    bypass_quiet_hours: bool = False


class QuietHours(_Strict):
    start: str = "22:30"
    end: str = "07:00"


class NotificationsCfg(_Strict):
    default_channels: list[Channel] = Field(
        default_factory=lambda: ["desktop"]  # type: ignore[arg-type]
    )
    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    rules: list[NotificationRule] = Field(default_factory=list)


class CodingCfg(_Strict):
    # Coding jobs always run through this command (no silent fallback to plain "claude").
    # Program name (looked up in PATH, ~/.local/bin, ~/bin, /opt/homebrew/bin, /usr/local/bin) or absolute path.
    claude_command: str = "claudeb"
    # Shell aliases cannot be started by subprocess: name → argv prefix that replaces it. A real program of that name
    # in PATH wins. `claudeb` is Sir's zsh alias for `claude --dangerously-skip-permissions`; the flag is NOT
    # included by default because `--restricted` (below) refuses it and it would void the allowlist.
    claude_aliases: dict[str, list[str]] = Field(default_factory=lambda: {"claudeb": ["claude"]})
    # False drops `--restricted` (needed for --dangerously-skip-permissions). Keep True unless intended.
    restricted: bool = True
    # Extra environment for the CLI (e.g. CLAUDE_CONFIG_DIR); "~" is expanded. API-key variables are refused.
    claude_env: dict[str, str] = Field(default_factory=dict)
    # Directories appended to PATH for the CLI and its shell tools (e.g. a Flutter SDK's bin/). The core runs under
    # launchd with a minimal PATH, so tools from the shell profile are missing otherwise. "~" is expanded.
    extra_path: list[str] = Field(default_factory=list)
    # haiku | sonnet | opus (or a full model id); empty = the model currently chosen in the chat.
    default_model: str = ""
    # Jobs running at the same time, each in its own worktree; further jobs wait as "queued". Delivery (merge into
    # the base branch, deploy, clean-up) happens one job at a time per repository.
    max_parallel_jobs: int = Field(default=3, ge=1)
    # Wall-clock limit per job (time spent paused does not count); exceeding it fails the job with CodingError.
    job_timeout_minutes: int = Field(default=180, ge=1)
    allowed_tools: list[str] = Field(default_factory=list)
    sandbox: bool = True
    # Exact absolute directories granted in addition to the job worktree (default: none = worktree only).
    # Read dirs are added with --add-dir and Edit/Write there are denied ("~" is expanded, missing dirs are skipped).
    extra_read_dirs: list[Path] = Field(default_factory=list)
    extra_write_dirs: list[Path] = Field(default_factory=list)
    # Standard-Ablauf nach einem grünen Job: Job-Branch pushen → PR (kein Draft) → PR mergen → Supabase deployen →
    # Job-Branch (origin und lokal) und Worktree löschen. Rot, Fehler oder Abbruch: WIP-Commit, Job-Branch wird
    # gepusht, aber nichts gemergt/deployt, der Worktree bleibt. False = nur committen.
    auto_ship: bool = True
    ship_base_branch: str = "main"
    # False (Standard): Job-Branch auf origin/<base> rebasen und direkt nach <base> pushen, ohne PR. Bei Konflikt
    # oder geschütztem Branch Fallback auf den PR-Weg. True = immer per PR.
    ship_via_pr: bool = False
    ship_merge_method: Literal["squash", "merge", "rebase"] = "squash"
    # Supabase CLI (Programmname oder absoluter Pfad); nur nötig, wenn das Projekt supabase/functions oder
    # supabase/migrations enthält.
    supabase_command: str = "supabase"
    supabase_timeout_minutes: int = 10
    # Hat ein Job Jarvis' eigenen Code ausgeliefert (Projekt = dieses Repository), startet Jarvis danach Core und –
    # falls sie läuft – Desktop-App neu, sobald kein anderer Coding-Job mehr läuft (jarvis.coding.self_restart).
    self_restart: bool = True


class BrowserCfg(_Strict):
    # "edge": Sir's installed Microsoft Edge, visible, with its own persistent Jarvis profile (cookies and
    # consents persist like for a normal user → far fewer CAPTCHAs than a fresh headless browser).
    # "chromium": Playwright's bundled browser, isolated context per call (previous behaviour).
    # "antibrow": antibrow anti-detect browser (free key: `jarvisctl secret set antibrow-api-key`, then
    # `uv sync --extra antibrow`); falls back to Edge when key/package are missing.
    engine: Literal["antibrow", "edge", "chromium"] = "antibrow"
    antibrow_profile: str = "jarvis"
    headless: bool = False  # only used for "chromium"; Edge always runs as a normal window
    search_engines: list[str] = Field(default_factory=lambda: ["google", "bing", "duckduckgo"])
    chrome_timeout_s: int = 300
    default_timeout_ms: int = 20000
    max_content_chars: int = 12000


class SchedulerCfg(_Strict):
    enabled: bool = True
    tick_seconds: int = 20


class MobileGatewayCfg(_Strict):
    """TLS listener for the companion app (LAN / Tailscale / VPS reverse proxy)."""

    enabled: bool = True
    host: str = "0.0.0.0"  # noqa: S104 - only mobile-safe paths are served on this listener (see api/exposure.py)
    port: int = 8767
    # Addresses the phone uses to reach the gateway (first reachable wins). Empty → LAN IPs detected at start.
    advertise_urls: list[str] = Field(default_factory=list)
    tls: Literal["self_signed", "external", "none"] = "self_signed"  # external: certificate from a proxy/Tailscale


class LiveKitCfg(_Strict):
    enabled: bool = True
    binary: str = "livekit-server"
    manage_process: bool = True  # the core starts and supervises a local livekit-server
    internal_url: str = "ws://127.0.0.1:7880"  # core + voice agent → LiveKit (loopback)
    public_url: str = ""  # what the phone uses; empty → proxied through the TLS gateway (wss://<gateway>/rtc)
    node_ip: str = ""  # IP announced for WebRTC media (LAN or Tailscale IP); empty → detected
    rtc_tcp_port: int = 7881
    rtc_udp_start: int = 50000
    rtc_udp_end: int = 50100
    turn_enabled: bool = False  # embedded TURN needs a public domain + certificate (VPS)
    api_key_secret: str = "livekit-api-key"
    api_secret_secret: str = "livekit-api-secret"


class FirebaseCfg(_Strict):
    """FCM HTTP v1. The service account JSON lives in the Keychain; the client options are not secret."""

    service_account_secret: str = "fcm-service-account"
    project_id: str = ""
    api_key: str = ""  # Android API key from google-services.json (public client identifier)
    app_id: str = ""  # mobilesdk_app_id
    sender_id: str = ""  # project_number


class CallPolicyCfg(_Strict):
    ring_timeout_s: int = 45
    max_calls_per_hour: int = 4
    min_minutes_between_calls: int = 3
    max_duration_minutes: int = 30


class AppUpdateCfg(_Strict):
    """OTA updates of the Android app (``jarvis.mobile.ota``): build a signed APK when the app's code changes on
    the base branch and hand it to the paired phones."""

    enabled: bool = True
    # False: no automatic builds (manual "build now" and existing releases keep working)
    auto_build: bool = True
    branch: str = "main"
    fetch: bool = True  # `git fetch origin <branch>` before each check (catches pushes from elsewhere)
    poll_interval_minutes: int = Field(default=10, ge=1)
    debounce_s: int = Field(default=60, ge=0)  # several changes in a row → one build
    keep_releases: int = Field(default=5, ge=3)  # older releases are removed (rollback needs a few)
    build_timeout_minutes: int = Field(default=40, ge=5)
    min_free_gb: float = Field(default=5.0, ge=0)  # a release build needs ~2-3 GB (checkout + build output)
    # Program name (looked up in PATH + coding.extra_path) or absolute path; must be "flutter" from the shell allowlist
    flutter_command: str = "flutter"
    android_sdk: str = ""  # empty: ANDROID_HOME / ANDROID_SDK_ROOT / ~/Library/Android/sdk
    java_home: str = ""  # empty: Android Studio's JDK / JAVA_HOME / /usr/libexec/java_home
    target_platform: Literal["android-arm64", "android-arm"] = "android-arm64"
    keystore_password_secret: str = "android-release-keystore"  # Keychain account (the keystore file is not here)
    install_link_ttl_hours: int = Field(default=24, ge=1, le=168)


class MobileCfg(_Strict):
    enabled: bool = True
    access_token_ttl_s: int = 600
    refresh_token_ttl_days: int = 30
    pairing_ttl_s: int = 600
    gateway: MobileGatewayCfg = Field(default_factory=MobileGatewayCfg)
    livekit: LiveKitCfg = Field(default_factory=LiveKitCfg)
    firebase: FirebaseCfg = Field(default_factory=FirebaseCfg)
    calls: CallPolicyCfg = Field(default_factory=CallPolicyCfg)
    app_update: AppUpdateCfg = Field(default_factory=AppUpdateCfg)


class BackupCfg(_Strict):
    enabled: bool = True
    directory: str = "~/Backups/Jarvis"
    at: str = "03:30"  # local time (Europe/Berlin); a missed run is caught up as soon as the core is running
    keep_recent: int = Field(default=14, ge=1)  # newest backups that are always kept
    keep_weekly: int = Field(default=8, ge=0)  # plus the newest backup of each of the last N weeks
    encrypt: bool = True  # AES-256-GCM, key in the Keychain (`jarvisctl backup key`)
    key_secret: str = "backup-key"
    pg_dump: str = "pg_dump"


class CreativeCfg(_Strict):
    """KIE.ai models for image_generate / video_generate (connector ``kie_creative``).

    One place for the model ids: override in ``jarvis.yaml`` (``creative:``) or per environment
    (``JARVIS__CREATIVE__IMAGE_MODEL`` …). A model set in the connector settings of the app wins over both.
    """

    image_model: str = "gpt-image-2-5-sunburst-text-to-image"  # text → image
    image_edit_model: str = "gpt-image-2-5-sunburst-image-to-image"  # with reference_image_path
    video_model: str = "wan/3-0-video"
    image_resolution: Literal["1K", "2K", "4K"] = "1K"
    video_resolution: Literal["480P", "720P", "1080P"] = "720P"
    # Polling limit for one video; with upload/download it must stay below agent.turn_timeout_s (900 s).
    video_timeout_s: int = Field(default=600, ge=60, le=3600)


class DevicesCfg(_Strict):
    """Jarvis Device Agents (Windows/Linux/Mac computers Jarvis controls remotely, see device_agent/)."""

    enabled: bool = True
    command_timeout_s: float = Field(default=60, ge=5, le=900)  # default wait for one device command
    pairing_ttl_s: int = Field(default=600, ge=60, le=3600)


class ClientsCfg(_Strict):
    """Clients that do not run next to the Core (desktop app on another PC, browser) – over Tailscale.

    ``remote_api``: the TLS gateway (:8767) also serves the full client API (not only the phone paths); every
    request still needs a device token. ``web_client``: the Core serves the browser client (``desktop/dist``)
    at ``/app`` – sign-in with a device token. Both are off by default: the Mac setup is unchanged."""

    remote_api: bool = False
    web_client: bool = False
    web_client_dir: Path | None = None  # default: <repo>/desktop/dist

    @field_validator("web_client_dir", mode="before")
    @classmethod
    def _exp(cls, v: Any) -> Path | None:
        return _expand(v) if v else None


class WhatsAppCfg(_Strict):
    """The WhatsApp sidecar (whatsapp-sidecar/, Node + Baileys). Connection options Sir changes in the app live in
    the connector settings; these are host-level options."""

    node_binary: str = "node"  # name in PATH or absolute path (Node.js ≥ 20)
    sidecar_dir: Path | None = None  # default: <repo>/whatsapp-sidecar (needs `npm ci && npm run build`)
    log_level: Literal["error", "warn", "info", "debug"] = "warn"  # sidecar log (always redacted)
    request_timeout_s: float = Field(default=60, ge=5, le=600)

    @field_validator("sidecar_dir", mode="before")
    @classmethod
    def _exp(cls, v: Any) -> Path | None:
        return _expand(v) if v else None


class SocialMediaCfg(_Strict):
    """Daily PJE Systems social video (jarvis.social): Remotion project, FFmpeg, library, e-mail.

    Host-level options. What Sir switches in the app (daily production, time, e-mail, autopost …) lives in
    ``<data_dir>/social-media.json``; the production time is the cron of the schedule "PJE Systems Daily Social
    Video" in the normal scheduler."""

    enabled: bool = True
    node_binary: str = "node"  # Node.js ≥ 20
    project_dir: Path | None = None  # default: <repo>/social (needs `npm ci && npm run browser`)
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    library_dir: Path | None = None  # default: <data_dir>/social-videos
    owner_email: str = "paulhoflich@gmail.com"  # mails to this address need no approval (Sir's own inbox)
    default_time: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str = "Europe/Berlin"
    concept_model: str = "opus"  # Claude CLI model alias (haiku | sonnet | opus): idea, script, storyboard, captions
    qa_model: str = "opus"  # frame analysis and spelling check
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = "medium"  # Claude CLI --effort for all of it
    render_timeout_s: int = Field(default=900, ge=60, le=3600)
    max_render_attempts: int = Field(default=3, ge=1, le=5)
    max_video_mb: float = Field(default=19.0, gt=1, le=200)
    email_attachment_max_mb: float = Field(default=18.0, gt=0, le=24)  # Gmail: 25 MB per message incl. encoding
    link_ttl_hours: int = Field(default=72, ge=1, le=24 * 30)
    public_base_url: str = ""  # address of this Core for download links in the e-mail (default server.public_base_url)

    @field_validator("project_dir", "library_dir", mode="before")
    @classmethod
    def _exp(cls, v: Any) -> Path | None:
        return _expand(v) if v else None


class Settings(_Strict):
    server: ServerCfg = Field(default_factory=ServerCfg)
    paths: PathsCfg
    database: DatabaseCfg = Field(default_factory=DatabaseCfg)
    llm: LlmCfg = Field(default_factory=LlmCfg)
    agent: AgentCfg = Field(default_factory=AgentCfg)
    hermes: HermesCfg = Field(default_factory=HermesCfg)
    session: SessionCfg = Field(default_factory=SessionCfg)
    voice: VoiceCfg = Field(default_factory=VoiceCfg)
    telephony: TelephonyCfg = Field(default_factory=TelephonyCfg)
    notifications: NotificationsCfg = Field(default_factory=NotificationsCfg)
    coding: CodingCfg = Field(default_factory=CodingCfg)
    browser: BrowserCfg = Field(default_factory=BrowserCfg)
    scheduler: SchedulerCfg = Field(default_factory=SchedulerCfg)
    mobile: MobileCfg = Field(default_factory=MobileCfg)
    backup: BackupCfg = Field(default_factory=BackupCfg)
    creative: CreativeCfg = Field(default_factory=CreativeCfg)
    devices: DevicesCfg = Field(default_factory=DevicesCfg)
    clients: ClientsCfg = Field(default_factory=ClientsCfg)
    whatsapp: WhatsAppCfg = Field(default_factory=WhatsAppCfg)
    social_media: SocialMediaCfg = Field(default_factory=SocialMediaCfg)


class Personality(_Strict):
    # Selectable persona (Einstellungen → Persönlichkeit). config/personality.yaml is "jarvis"; further personas live
    # in config/personalities/<id>.yaml. Everything except id/label/description/voice/sample/… is the system prompt.
    id: str = Field(default="jarvis", pattern=r"^[a-z][a-z0-9_]{1,31}$")
    label: str = ""  # shown in the settings ("" = name)
    description: str = ""  # one line for the settings
    name: str = "J.A.R.V.I.S."
    owner_title: str = "Sir"
    default_language: str = "de"
    languages: list[str] = Field(default_factory=lambda: ["de", "en"])
    identity: str = ""  # the "Du bist …" paragraph of the system prompt ("" = Jarvis' own, see persona.render_soul)
    traits: list[str] = Field(default_factory=list)
    style_rules: list[str] = Field(default_factory=list)
    greetings: dict[str, str] = Field(default_factory=dict)
    # call start (morning/afternoon/evening/night); empty = "Guten Abend, Sir." – start with Hallo/Guten …, so a
    # greeting the model repeats in its first answer is still recognised (persona.LeadingGreetingFilter)
    call_greetings: dict[str, str] = Field(default_factory=dict)
    error_phrases: list[str] = Field(default_factory=list)
    humor_examples: list[str] = Field(default_factory=list)
    voice: str = Field(default="jarvis", pattern=r"^[a-z][a-z0-9_]{1,31}$")  # voice of the local TTS server
    system_voice: str = ""  # macOS voice of the last fallback stage ("" = voice.tts.macos_voice)
    sample: str = ""  # Hörprobe in the settings
    ack_phrase: str = "Einen Moment, Sir."  # said when a tool runs before the first word
    confirm_example: str = "Sehr wohl, Sir. Ich kümmere mich darum."  # voice turns: how a started task is confirmed
    wakeup_say: str | None = None  # "Hey Jarvis, wake up" line (None = voice.wakeup.say)
    never_say: list[str] = Field(default_factory=list)  # words that are never spoken, even if the model writes them

    @property
    def display_name(self) -> str:
        return self.label or self.name


class RoleCfg(_Strict):
    description: str = ""
    max_risk: RiskLevel
    auto_approve: RiskLevel
    can_approve: bool = False


class ToolOverride(_Strict):
    risk: RiskLevel | None = None
    enabled: bool | None = None


class RateLimitsCfg(_Strict):
    tool_calls_per_minute: int = 60
    high_risk_per_hour: int = 30
    api_requests_per_minute: int = 600
    login_attempts_per_hour: int = 10


class Permissions(_Strict):
    roles: dict[str, RoleCfg]
    critical_approval_roles: list[str] = Field(default_factory=lambda: ["owner"])
    taint_requires_approval: bool = True
    approval_ttl_minutes: int = 1440
    tools: dict[str, ToolOverride] = Field(default_factory=dict)
    shell_allowlist: list[str] = Field(default_factory=list)
    # Own list per host OS ("windows", "linux"); without an entry for the host, shell_allowlist applies (the Mac).
    shell_allowlist_by_platform: dict[str, list[str]] = Field(default_factory=dict)
    rate_limits: RateLimitsCfg = Field(default_factory=RateLimitsCfg)

    @field_validator("roles")
    @classmethod
    def _owner_required(cls, v: dict[str, RoleCfg]) -> dict[str, RoleCfg]:
        if "owner" not in v:
            raise ValueError("permissions.yaml must define an 'owner' role")
        return v


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Config file missing: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


def _apply_env_overrides(data: dict[str, Any], environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Apply ``JARVIS__A__B=value`` overrides. Values are parsed as YAML scalars."""
    env = os.environ if environ is None else environ
    for key, raw in env.items():
        if not key.startswith("JARVIS__"):
            continue
        parts = [p.lower() for p in key.removeprefix("JARVIS__").split("__") if p]
        if not parts:
            continue
        node = data
        for part in parts[:-1]:
            nxt = node.setdefault(part, {})
            if not isinstance(nxt, dict):
                raise ValueError(f"Env override {key} targets a non-mapping value")
            node = nxt
        node[parts[-1]] = yaml.safe_load(raw)
    return data


def _deep_merge(base: dict[str, Any], top: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in top.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def platform_overlay(environ: dict[str, str] | None = None) -> Path | None:
    """``config/platform/<windows|linux>.yaml``: paths and defaults of that OS on top of ``jarvis.yaml``.

    The Mac never has an overlay – there ``jarvis.yaml`` is the whole configuration, as before."""
    from jarvis.platform import detect_platform_id

    pid = detect_platform_id(environ)
    if pid == "mac":
        return None
    path = CONFIG_DIR / "platform" / f"{pid}.yaml"
    return path if path.is_file() else None


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    data = _load_yaml("jarvis.yaml")
    overlay = platform_overlay(environ)
    if overlay is not None:
        data = _deep_merge(data, _load_yaml(str(overlay.relative_to(CONFIG_DIR))))
    return Settings.model_validate(_apply_env_overrides(data, environ))


def load_personality() -> Personality:
    return Personality.model_validate(_load_yaml("personality.yaml"))


PERSONALITIES_DIR = "personalities"


def load_personalities(default: Personality | None = None) -> list[Personality]:
    """Jarvis (config/personality.yaml) first, then every config/personalities/*.yaml in name order.

    A broken extra persona is skipped with a warning – it must never keep the core from starting."""
    first = default or load_personality()
    out = [first]
    for path in sorted((CONFIG_DIR / PERSONALITIES_DIR).glob("*.yaml")):
        try:
            p = Personality.model_validate(_load_yaml(f"{PERSONALITIES_DIR}/{path.name}"))
        except (ValueError, OSError, yaml.YAMLError) as e:
            logger.warning("Persönlichkeit %s ignoriert: %s", path.name, e)
            continue
        if any(o.id == p.id for o in out):
            logger.warning("Persönlichkeit %s ignoriert: id %r doppelt", path.name, p.id)
            continue
        out.append(p)
    return out


def load_permissions() -> Permissions:
    return Permissions.model_validate(_load_yaml("permissions.yaml"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
