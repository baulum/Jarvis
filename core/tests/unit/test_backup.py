import os
from pathlib import Path

import pytest

from jarvis.backup.service import NAME_PREFIX, decrypt_file, encrypt_file, restore_archive, select_keep


def test_encrypt_roundtrip_multi_chunk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jarvis.backup.service.CHUNK", 1000)
    key = os.urandom(32)
    src = tmp_path / "a.bin"
    src.write_bytes(os.urandom(4500))
    encrypt_file(src, tmp_path / "a.enc", key)
    decrypt_file(tmp_path / "a.enc", tmp_path / "b.bin", key)
    assert (tmp_path / "b.bin").read_bytes() == src.read_bytes()
    assert (tmp_path / "a.enc").read_bytes() != src.read_bytes()


def test_wrong_key_tamper_and_truncation_are_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jarvis.backup.service.CHUNK", 1000)
    key = os.urandom(32)
    src = tmp_path / "a.bin"
    src.write_bytes(os.urandom(3500))
    enc = tmp_path / "a.enc"
    encrypt_file(src, enc, key)
    with pytest.raises(ValueError):
        decrypt_file(enc, tmp_path / "x", os.urandom(32))
    raw = enc.read_bytes()
    flipped = bytearray(raw)
    flipped[40] ^= 1
    (tmp_path / "t.enc").write_bytes(bytes(flipped))
    with pytest.raises(ValueError):
        decrypt_file(tmp_path / "t.enc", tmp_path / "x", key)
    # cutting whole trailing chunks must not yield a "valid" shorter backup
    import struct

    pos, offsets = 12, []
    while pos < len(raw):
        offsets.append(pos)
        pos += 4 + struct.unpack(">I", raw[pos : pos + 4])[0]
    (tmp_path / "c.enc").write_bytes(raw[: offsets[-1]])
    with pytest.raises(ValueError):
        decrypt_file(tmp_path / "c.enc", tmp_path / "x", key)


def test_empty_file_roundtrip(tmp_path: Path) -> None:
    key = os.urandom(32)
    (tmp_path / "e").write_bytes(b"")
    encrypt_file(tmp_path / "e", tmp_path / "e.enc", key)
    decrypt_file(tmp_path / "e.enc", tmp_path / "e2", key)
    assert (tmp_path / "e2").read_bytes() == b""


def test_retention_keeps_recent_and_one_per_week() -> None:
    names = [f"{NAME_PREFIX}2026{m:02d}{d:02d}-030000.tar.gz.enc" for m in (8, 9, 10) for d in range(1, 29)]
    names.append("something-else.txt")
    keep = select_keep(names, keep_recent=7, keep_weekly=4)
    assert "something-else.txt" not in keep
    assert f"{NAME_PREFIX}20261028-030000.tar.gz.enc" in keep
    assert f"{NAME_PREFIX}20261025-030000.tar.gz.enc" in keep  # newest of the previous week
    assert f"{NAME_PREFIX}20261018-030000.tar.gz.enc" in keep
    assert f"{NAME_PREFIX}20260801-030000.tar.gz.enc" not in keep
    assert 7 <= len(keep) <= 11


def test_restore_unpacks_into_target(tmp_path: Path) -> None:
    import tarfile

    (tmp_path / "database.dump").write_text("dump")
    arc = tmp_path / f"{NAME_PREFIX}20261019-030000.tar.gz"
    with tarfile.open(arc, "w:gz") as t:
        t.add(tmp_path / "database.dump", arcname="database.dump")
    out = restore_archive(arc, tmp_path / "out", None)
    assert (out / "database.dump").read_text() == "dump"
