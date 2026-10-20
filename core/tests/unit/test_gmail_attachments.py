"""Gmail MIME parsing, filename sanitising and safe destination handling."""

import base64
from pathlib import Path

import pytest

from jarvis.connectors.gmail_mime import (
    b64url_decode,
    collect_attachments,
    resolve_dest_dir,
    sanitize_filename,
    write_new,
)
from jarvis.tools.executor import ToolUserError


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


PAYLOAD = {
    "mimeType": "multipart/mixed",
    "parts": [
        {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(b"hi")}},
                {
                    "mimeType": "multipart/related",
                    "parts": [
                        {"mimeType": "text/html", "body": {"data": _b64(b"<img src='cid:abc'>")}},
                        {
                            "mimeType": "image/jpeg",
                            "filename": "",
                            "headers": [
                                {"name": "Content-Disposition", "value": "inline"},
                                {"name": "Content-ID", "value": "<abc>"},
                            ],
                            "body": {"attachmentId": "A1", "size": 10},
                        },
                    ],
                },
            ],
        },
        {
            "mimeType": "application/pdf",
            "filename": "Rechnung.pdf",
            "headers": [{"name": "Content-Disposition", "value": "attachment; filename=Rechnung.pdf"}],
            "body": {"attachmentId": "A2", "size": 99},
        },
    ],
}


def test_nested_parts_and_inline_image_without_name():
    atts = collect_attachments(PAYLOAD)
    assert [a["filename"] for a in atts] == ["image_1.jpg", "Rechnung.pdf"]
    assert atts[0]["inline"] is True and atts[0]["contentId"] == "abc" and atts[0]["attachmentId"] == "A1"
    assert atts[1]["inline"] is False and atts[1]["contentId"] is None


def test_text_parts_are_not_attachments():
    assert collect_attachments({"mimeType": "text/plain", "body": {"data": _b64(b"x")}}) == []


def test_b64url_decode_without_padding():
    raw = b"\xff\xfe\xfd?>"
    assert b64url_decode(_b64(raw)) == raw


def test_sanitize_filename():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("a/b\\c:d*.png") == "c_d_.png"
    assert sanitize_filename("..") == "datei"


def test_resolve_dest_dir(tmp_path: Path):
    root = tmp_path / "Downloads"
    root.mkdir()
    assert resolve_dest_dir(str(root / "x"), [root]) == (root / "x").resolve()
    with pytest.raises(ToolUserError):
        resolve_dest_dir(str(root / ".." / "evil"), [root])
    with pytest.raises(ToolUserError):
        resolve_dest_dir("relative/dir", [root])


def test_write_new_does_not_overwrite(tmp_path: Path):
    a = write_new(tmp_path, "f.png", b"1")
    b = write_new(tmp_path, "f.png", b"2")
    assert a.name == "f.png" and b.name == "f_1.png"
    assert a.read_bytes() == b"1"
