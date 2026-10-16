"""Configuration loading and validation.

Three YAML files in ``config/`` (main settings, personality, permissions) are parsed into
strict pydantic models. Environment variables of the form ``JARVIS__SECTION__KEY`` override
values in ``jarvis.yaml``. Secrets are never part of the config: only Keychain account names.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = Path(os.environ.get("JARVIS_CONFIG_DIR", REPO_ROOT / "config"))

RiskLevel = Literal["read", "low", "medium", "high", "critical"]
RISK_ORDER: dict[str, int] = {"read": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _expand(p: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(p))))


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


class TtsCfg(_Strict):
    provider: Literal["macos", "fish_audio"] = "macos"
    macos_voice: str = "Anna"
    rate: int = 185
    fish_audio: FishAudioCfg = Field(default_factory=FishAudioCfg)


class VoiceCfg(_Strict):
    enabled: bool = True
    language: str = "de"
    wake_word: str = "hey_jarvis"
    wake_threshold: float = 0.5
    stt: SttCfg = Field(default_factory=SttCfg)
    tts: TtsCfg = Field(default_factory=TtsCfg)
    end_of_turn_silence_ms: int = 1600  # longest pause before a turn ends (sentence clearly unfinished)
    min_end_of_turn_silence_ms: int = 650  # pause that ends a turn when the transcript is complete
    speculative_stt_ms: int = 280  # start transcribing after this much silence (overlaps STT with endpointing)
    vad_min_rms: float = 180
    max_utterance_s: int = 30
    followup_window_s: int = 6
    barge_in: Literal["auto", "voice", "wake_word"] = "auto"  # auto: by voice when echo cancellation is active
    barge_in_ms: int = 320  # speech needed to interrupt Jarvis (voice barge-in)
    native_audio: bool = True  # Swift helper: streaming playback without gaps (falls back to sounddevice/afplay)
    # macOS voice processing (AEC) also suppressed Sir's voice in the room in a measurement on his Mac
    # (room voice 30 vs. silence 20 RMS) → off by default; barge-in then works with "Hey Jarvis".
    echo_cancellation: bool = False
    conversation_window_s: int = 30  # after a reply Jarvis keeps listening this long – no wake word needed


class TwilioCfg(_Strict):
    account_sid_secret: str = "twilio-account-sid"
    auth_token_secret: str = "twilio-auth-token"
    from_number: str = ""


class TelephonyCfg(_Strict):
    enabled: bool = True
    provider: Literal["twilio", "local_test"] = "twilio"
    owner_numbers: list[str] = Field(default_factory=list)
    pin_secret: str = "phone-pin"
    max_calls_per_hour: int = 10
    twilio: TwilioCfg = Field(default_factory=TwilioCfg)


class NotificationRule(_Strict):
    event: str
    channels: list[Literal["desktop", "telegram", "phone", "voice"]]
    bypass_quiet_hours: bool = False


class QuietHours(_Strict):
    start: str = "22:30"
    end: str = "07:00"


class NotificationsCfg(_Strict):
    default_channels: list[Literal["desktop", "telegram", "phone", "voice"]] = Field(
        default_factory=lambda: ["desktop"]  # type: ignore[arg-type]
    )
    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    rules: list[NotificationRule] = Field(default_factory=list)


class CodingCfg(_Strict):
    claude_binary: str = "claude"
    max_parallel_jobs: int = 1
    job_timeout_minutes: int = 60
    allowed_tools: list[str] = Field(default_factory=list)
    sandbox: bool = True


class BrowserCfg(_Strict):
    headless: bool = True
    default_timeout_ms: int = 20000
    max_content_chars: int = 12000


class SchedulerCfg(_Strict):
    enabled: bool = True
    tick_seconds: int = 20


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


class Personality(_Strict):
    name: str = "J.A.R.V.I.S."
    owner_title: str = "Sir"
    default_language: str = "de"
    languages: list[str] = Field(default_factory=lambda: ["de", "en"])
    traits: list[str] = Field(default_factory=list)
    style_rules: list[str] = Field(default_factory=list)
    greetings: dict[str, str] = Field(default_factory=dict)
    error_phrases: list[str] = Field(default_factory=list)
    humor_examples: list[str] = Field(default_factory=list)


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


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    return Settings.model_validate(_apply_env_overrides(_load_yaml("jarvis.yaml"), environ))


def load_personality() -> Personality:
    return Personality.model_validate(_load_yaml("personality.yaml"))


def load_permissions() -> Permissions:
    return Permissions.model_validate(_load_yaml("permissions.yaml"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
