from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from jarvis.config import CreativeCfg, Settings, _apply_env_overrides
from jarvis.connectors import kie_creative as kc
from jarvis.connectors.kie_creative import (
    KieCreativeConnector,
    generated_dir,
    media_dir,
    resolve_image_path,
    resolve_media_file,
)
from jarvis.mobile.gateway import mobile_path_allowed
from jarvis.tools.artifacts import artifacts
from jarvis.tools.executor import ToolUserError
from jarvis.voice.speech import speakable


def _state(tmp_path: Path, creative: CreativeCfg | None = None) -> SimpleNamespace:
    paths = SimpleNamespace(
        data_dir=tmp_path / "data", workspaces_dir=tmp_path / "ws", projects_dir=tmp_path / "projects"
    )
    for p in (paths.data_dir, paths.workspaces_dir, paths.projects_dir):
        p.mkdir(parents=True)
    return SimpleNamespace(settings=SimpleNamespace(paths=paths, creative=creative or CreativeCfg()))


def test_accepts_generated_image(tmp_path: Path) -> None:
    state = _state(tmp_path)
    gen = generated_dir(state)
    gen.mkdir()
    img = gen / "a.png"
    img.write_bytes(b"x")
    assert resolve_image_path(state, str(img)) == img.resolve()


def test_rejects_path_outside_roots(tmp_path: Path) -> None:
    state = _state(tmp_path)
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"x")
    with pytest.raises(ToolUserError):
        resolve_image_path(state, str(outside))


def test_rejects_non_image_and_traversal(tmp_path: Path) -> None:
    state = _state(tmp_path)
    gen = generated_dir(state)
    gen.mkdir()
    (gen / "notes.txt").write_text("x")
    with pytest.raises(ToolUserError):
        resolve_image_path(state, str(gen / "notes.txt"))
    with pytest.raises(ToolUserError):
        resolve_image_path(state, str(gen / ".." / ".." / "secret.png"))


def test_send_roots_can_be_restricted_to_generated_only(tmp_path: Path) -> None:
    state = _state(tmp_path)
    ws_img = state.settings.paths.workspaces_dir / "b.png"
    ws_img.write_bytes(b"x")
    assert resolve_image_path(state, str(ws_img)) == ws_img.resolve()
    with pytest.raises(ToolUserError):
        resolve_image_path(state, str(ws_img), roots=[generated_dir(state).resolve()])


# ---------------------------------------------------------------------------------------------- KIE mock
class FakeKie:
    """Mock of createTask → recordInfo (first "generating", then "success") → upload → result download."""

    def __init__(self, result_type: str = "image/png") -> None:
        self.created: list[dict[str, Any]] = []
        self.uploads: list[dict[str, Any]] = []
        self.polls = 0
        self.result_type = result_type
        self.auth_on_download: str | None = "unset"

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/api/v1/jobs/createTask":
            self.created.append(json.loads(req.content))
            return httpx.Response(200, json={"code": 200, "msg": "success", "data": {"taskId": "task_1"}})
        if path == "/api/v1/jobs/recordInfo":
            self.polls += 1
            if self.polls == 1:
                return httpx.Response(200, json={"code": 200, "data": {"state": "generating"}})
            result = json.dumps({"resultUrls": ["https://tempfile.example/result.bin"]})
            return httpx.Response(200, json={"code": 200, "data": {"state": "success", "resultJson": result}})
        if path == "/api/file-base64-upload":
            self.uploads.append(json.loads(req.content))
            return httpx.Response(200, json={"code": 200, "data": {"downloadUrl": "https://kie.example/ref.png"}})
        if path == "/result.bin":
            self.auth_on_download = req.headers.get("Authorization")
            return httpx.Response(200, content=b"MEDIA", headers={"content-type": self.result_type})
        return httpx.Response(404, json={"code": 404, "msg": "unknown"})


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kc, "POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(kc, "VIDEO_POLL_INTERVAL_S", 0.0)


def _connector(state: SimpleNamespace, fake: FakeKie, **settings: Any) -> KieCreativeConnector:
    conn = KieCreativeConnector(state)  # type: ignore[arg-type]
    conn.settings = dict(settings)
    conn.transport = httpx.MockTransport(fake)
    conn.secret = lambda name: "test-key" if name == "kie-api-key" else None  # type: ignore[method-assign]
    return conn


async def test_text_to_image_uses_gpt_image_sunburst_and_shows_the_image(tmp_path: Path, no_wait: None) -> None:
    state, fake = _state(tmp_path), FakeKie()
    out = await _connector(state, fake).generate_image({"prompt": "Ein Leuchtturm bei Nacht", "aspect_ratio": "16:9"})

    body = fake.created[0]
    assert body["model"] == "gpt-image-2-5-sunburst-text-to-image"
    assert body["input"] == {"prompt": "Ein Leuchtturm bei Nacht", "aspect_ratio": "16:9", "resolution": "1K"}
    assert fake.polls == 2 and not fake.uploads
    assert fake.auth_on_download is None  # the key never goes to the result host
    path = Path(out["image_path"])
    assert path.parent == generated_dir(state) and path.read_bytes() == b"MEDIA" and path.suffix == ".png"
    # chat attachment: the artifact the timeline puts on the tool item
    art = artifacts.take(out["artifact_id"])
    assert art is not None and art["kind"] == "image" and art["media"] == "images" and art["file"] == path.name
    assert "im Chat" in out["note"] and "vorlesen" in out["note"]  # the model must not repeat the path


async def test_reference_image_switches_to_image_to_image(tmp_path: Path, no_wait: None) -> None:
    state, fake = _state(tmp_path), FakeKie(result_type="image/webp")
    ref = state.settings.paths.workspaces_dir / "ref.png"
    ref.write_bytes(b"\x89PNG")
    out = await _connector(state, fake).generate_image({"prompt": "Gleiches Motiv im Schnee", "reference_image_path": str(ref)})

    body = fake.created[0]
    assert body["model"] == "gpt-image-2-5-sunburst-image-to-image"
    assert body["input"]["input_urls"] == ["https://kie.example/ref.png"]
    assert "image_urls" not in body["input"] and body["input"]["aspect_ratio"] == "1:1"
    assert fake.uploads[0]["base64Data"].startswith("data:image/png;base64,")
    assert out["image_path"].endswith(".webp")
    artifacts.take(out["artifact_id"])


async def test_video_uses_wan_3_with_first_frame_and_lands_in_generated_videos(tmp_path: Path, no_wait: None) -> None:
    state, fake = _state(tmp_path), FakeKie(result_type="video/mp4")
    ref = generated_dir(state)
    ref.mkdir()
    (ref / "still.png").write_bytes(b"\x89PNG")
    out = await _connector(state, fake).generate_video(
        {"prompt": "Die Kamera fährt langsam heran", "aspect_ratio": "9:16", "duration_s": 8, "reference_image_path": str(ref / "still.png")}
    )

    body = fake.created[0]
    assert body["model"] == "wan/3-0-video"
    assert body["input"] == {
        "prompt": "Die Kamera fährt langsam heran",
        "aspect_ratio": "9:16",
        "duration": 8,
        "resolution": "720P",
        "audio": True,
        "first_frame_url": "https://kie.example/ref.png",
    }
    path = Path(out["video_path"])
    assert path.parent == media_dir(state, "videos") and path.suffix == ".mp4"
    art = artifacts.take(out["artifact_id"])
    assert art is not None and art["kind"] == "video" and art["file"] == path.name and art["duration_s"] == 8


async def test_video_duration_is_validated_before_any_request(tmp_path: Path, no_wait: None) -> None:
    fake = FakeKie()
    with pytest.raises(ToolUserError):
        await _connector(_state(tmp_path), fake).generate_video({"prompt": "zu lang", "duration_s": 31})
    assert not fake.created


async def test_rejected_model_reports_kie_message(tmp_path: Path, no_wait: None) -> None:
    def reject(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 422, "msg": "model not supported"})

    conn = _connector(_state(tmp_path), FakeKie())
    conn.transport = httpx.MockTransport(reject)
    with pytest.raises(ToolUserError, match="422: model not supported"):
        await conn.generate_image({"prompt": "Test"})


async def test_models_come_from_config_and_connector_settings_win(tmp_path: Path, no_wait: None) -> None:
    custom = CreativeCfg(image_model="cfg-image", image_edit_model="cfg-edit", video_model="cfg-video")
    conn = _connector(_state(tmp_path, custom), FakeKie(), video_model="setting-video")
    assert (conn.model("image_model"), conn.model("image_edit_model"), conn.model("video_model")) == (
        "cfg-image",
        "cfg-edit",
        "setting-video",
    )
    # old keys of the nano-banana version are ignored instead of silently pinning the old model
    legacy = _connector(_state(tmp_path / "legacy"), FakeKie(), model="google/nano-banana")
    assert legacy.model("image_model") == "gpt-image-2-5-sunburst-text-to-image"


def test_default_models_and_environment_override() -> None:
    d = CreativeCfg()
    assert (d.image_model, d.image_edit_model, d.video_model) == (
        "gpt-image-2-5-sunburst-text-to-image",
        "gpt-image-2-5-sunburst-image-to-image",
        "wan/3-0-video",
    )
    paths = {k: "/tmp/j" for k in ("data_dir", "log_dir", "vault_dir", "workspaces_dir", "projects_dir")}
    s = Settings.model_validate(_apply_env_overrides({"paths": paths}, {"JARVIS__CREATIVE__VIDEO_MODEL": "wan/3-0-video-prime"}))
    assert s.creative.video_model == "wan/3-0-video-prime" and s.creative.image_model == d.image_model


def test_legacy_nano_banana_keeps_its_parameter_names() -> None:
    inputs = kc.image_inputs("google/nano-banana-edit", "p", "auto", "1K", ["https://r"])
    assert inputs == {"prompt": "p", "image_size": "1:1", "output_format": "png", "image_urls": ["https://r"]}


def test_tools_registered_with_medium_risk() -> None:
    conn = KieCreativeConnector(SimpleNamespace(settings=SimpleNamespace(creative=CreativeCfg())))  # type: ignore[arg-type]
    specs = {s.name: s for s in conn.tools()}
    assert set(specs) == {"image_generate", "video_generate"}
    video = specs["video_generate"]
    assert video.risk == "medium" and video.connector == "kie_creative" and video.writes
    assert video.timeout_s < 900  # stays below the chat turn timeout
    assert set(video.parameters["properties"]) >= {"prompt", "reference_image_path", "aspect_ratio", "duration_s"}


# ---------------------------------------------------------------------------------------------- path guard
def test_media_file_only_by_bare_name_inside_its_folder(tmp_path: Path) -> None:
    state = _state(tmp_path)
    images, videos = media_dir(state, "images"), media_dir(state, "videos")
    images.mkdir()
    videos.mkdir()
    (images / "20261022-102738-53c75e52.png").write_bytes(b"x")
    (videos / "clip.mp4").write_bytes(b"x")
    (tmp_path / "data" / "secret.png").write_bytes(b"x")
    (images / "link.png").symlink_to(tmp_path / "data" / "secret.png")

    assert resolve_media_file(state, "images", "20261022-102738-53c75e52.png") == (images / "20261022-102738-53c75e52.png").resolve()
    assert resolve_media_file(state, "videos", "clip.mp4") is not None
    for kind, name in [
        ("images", "../secret.png"),
        ("images", "..%2Fsecret.png"),
        ("images", "sub/a.png"),
        ("images", str(images / "20261022-102738-53c75e52.png")),
        ("images", ".hidden.png"),
        ("images", "missing.png"),
        ("images", "clip.mp4"),  # wrong type for the folder
        ("videos", "20261022-102738-53c75e52.png"),
        ("images", "link.png"),  # symlink pointing out of the folder
        ("other", "a.png"),
        ("images", ""),
    ]:
        assert resolve_media_file(state, kind, name) is None, (kind, name)


def test_mobile_gateway_allows_media_names_only() -> None:
    assert mobile_path_allowed("GET", "/api/media/images/20261022-102738-53c75e52.png")
    assert mobile_path_allowed("GET", "/api/media/videos/clip.mp4")
    assert not mobile_path_allowed("GET", "/api/media/images/../secret.png")
    assert not mobile_path_allowed("GET", "/api/media/other/a.png")
    assert not mobile_path_allowed("POST", "/api/media/images/a.png")


def test_speech_never_reads_file_paths() -> None:
    text = (
        "Das Bild ist fertig, Sir. Gespeichert unter "
        "/Users/paul/Library/Application Support/Jarvis/generated-images/20261022-102738-53c75e52.png und bereit."
    )
    spoken = speakable(text)
    assert "/" not in spoken and "png" not in spoken and "Support" not in spoken
    assert spoken.startswith("Das Bild ist fertig, Sir.") and spoken.endswith("und bereit.")
    assert speakable("Liegt in `~/Downloads/a.mp4`.") == "Liegt in ."
    assert speakable("Tempo 50 km/h und 3/4 Takt") == "Tempo 50 km/h und 3/4 Takt"
