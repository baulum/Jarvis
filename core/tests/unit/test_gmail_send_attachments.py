"""gmail_send attachments: path allow-list, size limit and MIME structure."""

import base64
import email
from email import policy
from pathlib import Path
from typing import Any

import pytest

from jarvis.connectors.google import GoogleConnector, load_attachments
from jarvis.tools.executor import ToolUserError


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "allowed"
    r.mkdir()
    return r


def test_loads_file_with_mime(root: Path) -> None:
    f = root / "Angebot.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    assert load_attachments([str(f)], (root,)) == [("Angebot.pdf", "application/pdf", b"%PDF-1.4 test")]


def test_rejects_outside_dotdot_and_symlink(root: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    link = root / "link.txt"
    link.symlink_to(secret)
    for bad in (str(secret), str(root / ".." / "secret.txt"), str(link)):
        with pytest.raises(ToolUserError, match="nicht erlaubt"):
            load_attachments([bad], (root,))


def test_rejects_missing_dir_and_sensitive(root: Path) -> None:
    with pytest.raises(ToolUserError, match="nicht gefunden"):
        load_attachments([str(root / "nope.pdf")], (root,))
    with pytest.raises(ToolUserError, match="keine reguläre Datei"):
        load_attachments([str(root)], (root,))
    env = root / ".env"
    env.write_text("K=V")
    with pytest.raises(ToolUserError, match="sensible"):
        load_attachments([str(env)], (root,))


def test_total_size_limit(root: Path) -> None:
    for n in ("a.bin", "b.bin"):
        (root / n).write_bytes(b"0" * 600)
    with pytest.raises(ToolUserError, match="zu groß"):
        load_attachments([str(root / "a.bin"), str(root / "b.bin")], (root,), max_bytes=1000)


@pytest.mark.asyncio
async def test_mime_structure_and_backward_compat() -> None:
    sent: list[dict[str, Any]] = []
    g = GoogleConnector.__new__(GoogleConnector)

    async def fake_api(method: str, url: str, **kw: Any) -> Any:
        sent.append(kw["json"])
        return {"id": "m1"}

    g._api = fake_api  # type: ignore[method-assign]
    await g.send_mail("a@b.de", "Hallo", "Text")
    plain = email.message_from_bytes(base64.urlsafe_b64decode(sent[0]["raw"]), policy=policy.default)
    assert not plain.is_multipart()

    await g.send_mail("a@b.de", "Hallo", "Text", attachments=[("Angebot.pdf", "application/pdf", b"%PDF-1")])
    msg = email.message_from_bytes(base64.urlsafe_b64decode(sent[1]["raw"]), policy=policy.default)
    assert msg.get_content_type() == "multipart/mixed"
    att = list(msg.iter_attachments())
    assert len(att) == 1
    assert att[0].get_content_type() == "application/pdf"
    assert att[0].get_filename() == "Angebot.pdf"
    assert att[0].get_content() == b"%PDF-1"
    assert msg.get_body(("plain",)).get_content().strip() == "Text"  # type: ignore[union-attr]
