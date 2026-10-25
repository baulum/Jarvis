"""Binary file transfer for the Core (WhatsApp attachments to/from this computer): stat, read_chunk, write_chunk."""

from __future__ import annotations

import base64
import hashlib

import pytest

from jarvis_device.dispatcher import CommandError
from jarvis_device.protocol import READ_ONLY_ACTIONS


async def test_stat_and_chunked_read_with_hash(make_dispatcher, root):
    data = bytes(range(256)) * 40_000  # ~10 MB, three chunks
    (root / "Präsentation.pdf").write_bytes(data)
    d = make_dispatcher()
    st = await d.run("files.stat", {"path": str(root / "Präsentation.pdf"), "hash": True})
    assert st["is_file"] and st["size"] == len(data) and st["sha256"] == hashlib.sha256(data).hexdigest()
    got = b""
    while len(got) < st["size"]:
        part = await d.run("files.read_chunk", {"path": str(root / "Präsentation.pdf"), "offset": len(got), "length": 4 * 1024 * 1024})
        got += base64.b64decode(part["data"])
    assert got == data and part["eof"]
    assert {"files.stat", "files.read_chunk"} <= READ_ONLY_ACTIONS


async def test_chunked_write_verifies_hash_and_never_overwrites(make_dispatcher, root):
    d = make_dispatcher()
    data = b"\x89PNG" + b"x" * (5 * 1024 * 1024)
    digest = hashlib.sha256(data).hexdigest()

    async def upload(uid: str, target: str, payload: bytes, sha: str) -> dict:
        res: dict = {}
        offset = 0
        step = 4 * 1024 * 1024
        while True:
            chunk = payload[offset : offset + step]
            final = offset + len(chunk) >= len(payload)
            args = {"upload_id": uid, "path": target, "offset": offset, "data": base64.b64encode(chunk).decode(), "final": final}
            if final:
                args |= {"sha256": sha, "size": len(payload)}
            res = await d.run("files.write_chunk", args)
            offset += len(chunk)
            if final:
                return res

    (root / "Desktop").mkdir()
    first = await upload("a1b2c3d4", str(root / "Desktop" / "Bild.png"), data, digest)
    assert first["sha256"] == digest and (root / "Desktop" / "Bild.png").read_bytes() == data
    second = await upload("a1b2c3d5", str(root / "Desktop" / "Bild.png"), data, digest)
    assert second["path"].endswith("Bild (1).png")  # existing file kept
    with pytest.raises(CommandError) as bad:
        await upload("a1b2c3d6", str(root / "Desktop" / "Kaputt.png"), data, "0" * 64)
    assert bad.value.code == "invalid" and not (root / "Desktop" / "Kaputt.png").exists()
    assert not list((root / "Desktop").glob(".*jarvis-part*"))  # no leftovers


async def test_write_chunk_checks_order_ids_and_folders(make_dispatcher, root, tmp_path):
    d = make_dispatcher()
    chunk = base64.b64encode(b"abc").decode()
    with pytest.raises(CommandError) as order:
        await d.run("files.write_chunk", {"upload_id": "deadbeef", "path": str(root / "x.txt"), "offset": 5, "data": chunk, "final": False})
    assert order.value.code == "invalid"
    with pytest.raises(CommandError):
        await d.run("files.write_chunk", {"upload_id": "../../x", "path": str(root / "x.txt"), "offset": 0, "data": chunk, "final": False})
    with pytest.raises(CommandError) as outside:
        await d.run("files.write_chunk", {"upload_id": "deadbeef", "path": str(tmp_path / "outside.txt"), "offset": 0,
                                          "data": chunk, "final": True, "sha256": hashlib.sha256(b"abc").hexdigest()})  # fmt: skip
    assert outside.value.code == "denied"
    d.kill.remote = True
    with pytest.raises(CommandError) as killed:
        await d.run("files.write_chunk", {"upload_id": "deadbeef", "path": str(root / "x.txt"), "offset": 0, "data": chunk, "final": False})
    assert killed.value.code == "kill_switch"
