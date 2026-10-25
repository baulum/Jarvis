from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest

from jarvis.whatsapp.files import (
    FileRejected,
    PathPolicy,
    copy_exclusive,
    create_exclusive,
    safe_file_name,
    sha256_file,
    sniff,
    sniff_path,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PDF = b"%PDF-1.7\n%..."


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.mark.parametrize(
    ("head", "ext", "category"),
    [
        (PNG, "png", "image"),
        (JPG, "jpg", "image"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp", "image"),
        (PDF, "pdf", "document"),
        (b"OggS\x00\x02" + b"\x00" * 20, "ogg", "audio"),
        (b"ID3\x04\x00", "mp3", "audio"),
        (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8, "mp4", "video"),
        (b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 8, "m4a", "audio"),
        (b"\x00\x00\x00\x18ftypqt  " + b"\x00" * 8, "mov", "video"),
        (b"#!AMR\n", "amr", "audio"),
        (b"Rar!\x1a\x07\x00", "rar", "archive"),
    ],
)
def test_type_comes_from_content(head: bytes, ext: str, category: str) -> None:
    det = sniff(head, "irgendwas.dat")
    assert det.kind is not None and det.kind.ext == ext
    assert det.category == category


def test_office_containers_are_recognised_from_the_inside() -> None:
    docx = _zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>"})
    xlsx = _zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<w/>"})
    plain = _zip({"a.txt": b"hi"})
    assert sniff(docx[:4096], "Brief.docx", data=docx).kind.ext == "docx"  # type: ignore[union-attr]
    assert sniff(xlsx[:4096], "Tabelle.xlsx", data=xlsx).kind.ext == "xlsx"  # type: ignore[union-attr]
    assert sniff(plain[:4096], "x.zip", data=plain).kind.ext == "zip"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("head", "name"),
    [
        (b"MZ\x90\x00", "rechnung.pdf"),  # Windows program disguised as PDF
        (b"\x7fELF\x02\x01", "foto.jpg"),
        (b"\xcf\xfa\xed\xfe", "tool"),
        (b"#!/bin/sh\nrm -rf ~", "notes.txt"),
        (PDF, "invoice.pdf.exe"),  # executable extension wins even with harmless content
        (b"plain text", "setup.bat"),
    ],
)
def test_executables_are_refused(head: bytes, name: str) -> None:
    det = sniff(head, name)
    assert det.executable
    assert det.category == "executable"
    assert "Programm" in det.reason


def test_android_and_macro_documents_count_as_programs() -> None:
    apk = _zip({"AndroidManifest.xml": b"x", "classes.dex": b"x"})
    macro = _zip({"[Content_Types].xml": b"application/vnd.ms-word.document.macroEnabled.main+xml", "word/x.xml": b"x"})
    assert sniff(apk[:4096], "app.zip", data=apk).executable
    assert sniff(macro[:4096], "brief.docx", data=macro).executable


def test_mismatch_between_name_and_content_is_reported(tmp_path: Path) -> None:
    det = sniff(PNG, "angebot.pdf")
    assert det.kind is not None and det.kind.ext == "png" and det.mismatch
    assert not sniff(JPG, "bild.jpeg").mismatch
    assert sniff(PNG, "x.png", "video/mp4").mismatch  # declared MIME family contradicts the content
    dmg = tmp_path / "image.dmg"
    dmg.write_bytes(b"\x00" * 2048 + b"koly" + b"\x00" * 508)
    assert sniff_path(dmg, "image.dmg").executable


def test_safe_names_keep_the_original_but_never_path_parts() -> None:
    assert safe_file_name("Rechnung März 2026.pdf", "pdf") == "Rechnung März 2026.pdf"
    assert safe_file_name("../../etc/passwd", "") == "passwd"
    assert safe_file_name("..\\..\\Windows\\win.ini", "txt") == "win.ini.txt" or safe_file_name("..\\..\\Windows\\win.ini", "txt").endswith(".txt")
    assert safe_file_name("foto.pdf", "jpg") == "foto.jpg"  # content wins over a wrong known extension
    assert safe_file_name("Vertrag v1.2", "pdf") == "Vertrag v1.2.pdf"
    assert safe_file_name("CON.txt", "txt") == "_CON.txt"
    assert safe_file_name("a‮gnp.exe", "") == "agnp.exe"  # bidi override removed
    assert safe_file_name("", "jpg") == "WhatsApp-Datei.jpg"
    assert len(safe_file_name("x" * 400 + ".pdf", "pdf")) <= 120


def test_collision_safe_names(tmp_path: Path) -> None:
    src = tmp_path / "src.pdf"
    src.write_bytes(PDF)
    a = copy_exclusive(src, tmp_path / "out", "Angebot.pdf")
    b = copy_exclusive(src, tmp_path / "out", "Angebot.pdf")
    c = copy_exclusive(src, tmp_path / "out", "Angebot.pdf")
    assert [a.name, b.name, c.name] == ["Angebot.pdf", "Angebot (1).pdf", "Angebot (2).pdf"]
    assert sha256_file(a) == sha256_file(c)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_exclusive_create_never_follows_a_planted_symlink(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("geheim")
    out = tmp_path / "out"
    out.mkdir()
    (out / "x.pdf").symlink_to(secret)
    path, fd = create_exclusive(out, "x.pdf")
    os.close(fd)
    assert path.name == "x (1).pdf"
    assert secret.read_text() == "geheim"


def test_path_policy_traversal_symlinks_and_sensitive_folders(tmp_path: Path) -> None:
    home = tmp_path / "home"
    allowed = home / "Documents"
    allowed.mkdir(parents=True)
    (home / ".ssh").mkdir()
    data = tmp_path / "jarvis-data"
    (data / "whatsapp" / "downloads").mkdir(parents=True)
    pol = PathPolicy(allowed=[allowed, data / "whatsapp" / "downloads"], protected=[data],
                     exceptions=[data / "whatsapp" / "downloads"], home=home)  # fmt: skip
    assert pol.is_allowed(pol.resolve("Documents/Projekt/rechnung.pdf"))  # relative = below home
    assert pol.is_allowed(pol.resolve("~/Documents"))
    assert not pol.is_allowed(pol.resolve("Documents/../../outside.txt"))  # traversal resolves outside
    with pytest.raises(FileRejected):
        pol.resolve("~/.ssh/id_ed25519")
    with pytest.raises(FileRejected):
        pol.resolve(str(data / "bridge.token"))
    assert pol.is_allowed(pol.resolve(str(data / "whatsapp" / "downloads" / "a.pdf")))
    with pytest.raises(FileRejected):
        pol.resolve("bad\x00name")
    if os.name != "nt":
        (allowed / "escape").symlink_to(tmp_path)
        assert not pol.is_allowed(pol.resolve("Documents/escape/x.txt"))  # a link out does not count as inside
