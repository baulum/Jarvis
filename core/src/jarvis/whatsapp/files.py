"""Files that come from or go to WhatsApp: real type, safe names, allowed locations, integrity.

* **Type** comes from the content (magic bytes, the inside of ZIP containers), not from the name or the MIME type
  the sender declared. A mismatch is reported; executables (Windows/Mac/Linux programs, scripts, installers,
  Android/Java packages) are never stored, opened or processed.
* **Names** keep the original file name and extension where they are safe (no path parts, control or bidi
  characters, Windows-reserved names) and never overwrite anything: ``Rechnung.pdf``, ``Rechnung (1).pdf`` …
  created with ``O_EXCL`` so two downloads cannot race for the same name.
* **Locations**: a destination is resolved (``~``, relative = below Sir's home folder, symlinks followed) and must
  lie inside an allowed folder; key folders (``.ssh`` …) and Jarvis' own data are refused outright. A folder
  outside the allowed list is possible only with an explicit approval (the tool asks every time).
* **Integrity**: SHA-256 of every stored file; the same content stored twice is recognised by its hash.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import secrets
import shutil
import sys
import unicodedata
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from jarvis.agent.attachments import display_name

Category = Literal["image", "video", "audio", "document", "archive", "other", "executable"]


@dataclass(frozen=True, slots=True)
class FileKind:
    ext: str
    mime: str
    category: Category
    label: str


def _k(ext: str, mime: str, category: Category, label: str) -> FileKind:
    return FileKind(ext, mime, category, label)


KINDS: dict[str, FileKind] = {
    k.ext: k
    for k in (
        _k("jpg", "image/jpeg", "image", "JPG-Bild"),
        _k("png", "image/png", "image", "PNG-Bild"),
        _k("webp", "image/webp", "image", "WebP-Bild"),
        _k("gif", "image/gif", "image", "GIF-Bild"),
        _k("heic", "image/heic", "image", "HEIC-Bild"),
        _k("mp4", "video/mp4", "video", "MP4-Video"),
        _k("mov", "video/quicktime", "video", "MOV-Video"),
        _k("3gp", "video/3gpp", "video", "3GP-Video"),
        _k("webm", "video/webm", "video", "WebM-Video"),
        _k("mkv", "video/x-matroska", "video", "MKV-Video"),
        _k("ogg", "audio/ogg", "audio", "OGG-Audio (Opus)"),
        _k("mp3", "audio/mpeg", "audio", "MP3-Audio"),
        _k("m4a", "audio/mp4", "audio", "M4A-Audio"),
        _k("wav", "audio/wav", "audio", "WAV-Audio"),
        _k("amr", "audio/amr", "audio", "AMR-Audio"),
        _k("pdf", "application/pdf", "document", "PDF"),
        _k("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "document", "Word"),
        _k("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "document", "Excel"),
        _k(
            "pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "document",
            "PowerPoint",
        ),
        _k("odt", "application/vnd.oasis.opendocument.text", "document", "OpenDocument-Text"),
        _k("ods", "application/vnd.oasis.opendocument.spreadsheet", "document", "OpenDocument-Tabelle"),
        _k("doc", "application/msword", "document", "Word (alt)"),
        _k("xls", "application/vnd.ms-excel", "document", "Excel (alt)"),
        _k("ppt", "application/vnd.ms-powerpoint", "document", "PowerPoint (alt)"),
        _k("rtf", "application/rtf", "document", "RTF"),
        _k("txt", "text/plain", "document", "Text"),
        _k("md", "text/markdown", "document", "Markdown"),
        _k("csv", "text/csv", "document", "CSV"),
        _k("json", "application/json", "document", "JSON"),
        _k("vcf", "text/vcard", "document", "Kontaktkarte"),
        _k("zip", "application/zip", "archive", "ZIP-Archiv"),
        _k("rar", "application/vnd.rar", "archive", "RAR-Archiv"),
        _k("7z", "application/x-7z-compressed", "archive", "7z-Archiv"),
        _k("gz", "application/gzip", "archive", "GZip-Archiv"),
    )
}
TEXT_EXTS = frozenset({"txt", "md", "csv", "json", "vcf"})
# Program files and installers: never stored or processed, whatever their content looks like.
EXECUTABLE_EXTS = frozenset(
    {
        "exe",
        "msi",
        "msp",
        "dll",
        "sys",
        "scr",
        "com",
        "bat",
        "cmd",
        "ps1",
        "psm1",
        "vbs",
        "vbe",
        "js",
        "jse",
        "wsf",
        "wsh",
        "hta",
        "cpl",
        "lnk",
        "reg",
        "jar",
        "apk",
        "aab",
        "xapk",
        "ipa",
        "app",
        "dmg",
        "pkg",
        "mpkg",
        "command",
        "tool",
        "sh",
        "bash",
        "zsh",
        "csh",
        "ksh",
        "fish",
        "py",
        "pyw",
        "pl",
        "rb",
        "php",
        "deb",
        "rpm",
        "appimage",
        "run",
        "elf",
        "so",
        "dylib",
        "workflow",
        "scpt",
        "applescript",
        "action",
        "iso",
        "img",
        "vhd",
        "vhdx",
        "docm",
        "xlsm",
        "pptm",
        "dotm",
        "xltm",
    }
)
OUTGOING_IMAGE_EXTS = frozenset({"jpg", "png", "webp"})
OUTGOING_VIDEO_EXTS = frozenset({"mp4", "3gp", "mov"})
OUTGOING_AUDIO_EXTS = frozenset({"ogg", "mp3", "m4a", "amr", "wav"})
SENSITIVE_PARTS = frozenset(
    {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".password-store", ".config/gcloud", "keychains"}
)
_WIN_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.IGNORECASE)
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class FileRejected(ValueError):
    """A file or destination was refused; the message is meant for Sir."""


@dataclass(frozen=True, slots=True)
class Detected:
    kind: FileKind | None  # None = unknown binary format
    executable: bool = False
    mismatch: bool = False  # the declared name/MIME says something else than the content
    reason: str = ""

    @property
    def category(self) -> Category:
        if self.executable:
            return "executable"
        return self.kind.category if self.kind else "other"

    @property
    def mime(self) -> str:
        return self.kind.mime if self.kind else "application/octet-stream"

    @property
    def label(self) -> str:
        if self.executable:
            return "Programmdatei"
        return self.kind.label if self.kind else "unbekanntes Format"


# --------------------------------------------------------------------------------------------- detection
def extension(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    return ext.lower() if dot and stem else ""


def _ftyp(head: bytes) -> FileKind | None:
    if head[4:8] != b"ftyp":
        return None
    brand = head[8:12]
    if brand in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"):
        return KINDS["heic"]
    if brand in (b"M4A ", b"M4B ", b"M4P "):
        return KINDS["m4a"]
    if brand == b"qt  ":
        return KINDS["mov"]
    if brand.startswith(b"3g"):
        return KINDS["3gp"]
    return KINDS["mp4"]


def _zip_kind(data: bytes | None, path: Path | None) -> tuple[FileKind | None, bool]:
    """(kind, executable) of a ZIP container: Office/OpenDocument files, Java/Android packages or a plain ZIP."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data) if data is not None else str(path))
    except (zipfile.BadZipFile, OSError):
        return None, False
    with zf:
        names = set(zf.namelist()[:5000])
        if "AndroidManifest.xml" in names or any(n.endswith(".dex") for n in names):
            return None, True
        if "META-INF/MANIFEST.MF" in names and any(n.endswith(".class") for n in names):
            return None, True
        if "[Content_Types].xml" in names:
            try:
                ctypes = zf.read("[Content_Types].xml")[:200_000].decode("utf-8", "replace").lower()
            except (KeyError, zipfile.BadZipFile):
                ctypes = ""
            if "macroenabled" in ctypes or "vbaproject" in ctypes:
                return None, True  # documents with macros count as programs
            if any(n.startswith("word/") for n in names):
                return KINDS["docx"], False
            if any(n.startswith("xl/") for n in names):
                return KINDS["xlsx"], False
            if any(n.startswith("ppt/") for n in names):
                return KINDS["pptx"], False
        if "mimetype" in names:
            try:
                mt = zf.read("mimetype")[:100].decode("ascii", "replace").strip()
            except (KeyError, zipfile.BadZipFile):
                mt = ""
            if mt == KINDS["odt"].mime:
                return KINDS["odt"], False
            if mt == KINDS["ods"].mime:
                return KINDS["ods"], False
        return KINDS["zip"], False


def _looks_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError as e:
        if e.start > len(head) - 4:  # a multi-byte character cut off at the end of the sample
            return True
    try:
        head.decode("cp1252")
        return True
    except UnicodeDecodeError:
        return False


def sniff(
    head: bytes,
    declared_name: str = "",
    declared_mime: str = "",
    *,
    data: bytes | None = None,
    path: Path | None = None,
) -> Detected:
    """Real type of a file from its first bytes (and, for ZIP containers, its table of contents)."""
    declared_ext = extension(declared_name)
    executable_name = declared_ext in EXECUTABLE_EXTS
    kind: FileKind | None = None
    executable = False
    if head.startswith(b"MZ") or head.startswith(b"\x7fELF"):
        executable = True
    elif head[:4] in (
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
    ):
        executable = True  # Mach-O (also Java class files share CAFEBABE)
    elif head.startswith(b"#!AMR"):
        kind = KINDS["amr"]  # voice recordings (must be checked before the script rule below)
    elif head.startswith(b"#!"):
        executable = True  # script with an interpreter line
    elif head.startswith(b"\x89PNG\r\n\x1a\n"):
        kind = KINDS["png"]
    elif head.startswith(b"\xff\xd8\xff"):
        kind = KINDS["jpg"]
    elif head.startswith((b"GIF87a", b"GIF89a")):
        kind = KINDS["gif"]
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        kind = KINDS["webp"]
    elif head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        kind = KINDS["wav"]
    elif (ft := _ftyp(head)) is not None:
        kind = ft
    elif head.startswith(b"\x1a\x45\xdf\xa3"):
        kind = KINDS["mkv"] if b"matroska" in head[:64] else KINDS["webm"]
    elif head.startswith(b"OggS"):
        kind = KINDS["ogg"]
    elif head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        kind = KINDS["mp3"]
    elif b"%PDF-" in head[:1024]:
        kind = KINDS["pdf"]
    elif head.startswith(b"PK\x03\x04"):
        kind, executable = _zip_kind(data, path)
    elif head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):  # OLE: legacy Office – or an MSI installer
        if declared_ext in ("doc", "xls", "ppt"):
            kind = KINDS[declared_ext]
        elif declared_ext in ("msi", "msp") or "msi" in declared_mime:
            executable = True
        else:
            kind = KINDS["doc"]
    elif head.startswith(b"Rar!\x1a\x07"):
        kind = KINDS["rar"]
    elif head.startswith(b"7z\xbc\xaf\x27\x1c"):
        kind = KINDS["7z"]
    elif head.startswith(b"\x1f\x8b"):
        kind = KINDS["gz"]
    elif head.startswith(b"{\\rtf"):
        kind = KINDS["rtf"]
    elif _looks_text(head):
        kind = KINDS[declared_ext] if declared_ext in TEXT_EXTS else KINDS["txt"]
    if executable or executable_name:
        return Detected(
            None,
            executable=True,
            reason="Programmdateien und Installer werden aus Sicherheitsgründen nicht gespeichert oder geöffnet.",
        )
    mismatch = False
    if kind is not None and declared_ext and declared_ext != kind.ext:
        same = {kind.ext, declared_ext} <= {"jpg", "jpeg"} or (declared_ext in TEXT_EXTS and kind.ext in TEXT_EXTS)
        mismatch = not same and not (declared_ext == "jpeg" and kind.ext == "jpg")
    if kind is not None and declared_mime and not mismatch:
        base = declared_mime.split(";")[0].strip().lower()
        major = base.split("/")[0]
        if major in ("image", "video", "audio") and major != kind.mime.split("/")[0]:
            mismatch = True
    return Detected(kind, mismatch=mismatch)


def sniff_path(path: Path, declared_name: str = "", declared_mime: str = "") -> Detected:
    with path.open("rb") as f:
        head = f.read(4096)
        trailer = b""
        if path.stat().st_size >= 1024:
            f.seek(-512, os.SEEK_END)
            trailer = f.read(4)
    if trailer == b"koly":  # Apple disk image (installer container)
        return Detected(None, executable=True, reason="Disk-Images und Installer werden nicht verarbeitet.")
    return sniff(head, declared_name, declared_mime, path=path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------------------------- names
def safe_file_name(raw: str, ext: str = "", default_stem: str = "WhatsApp-Datei") -> str:
    """Original name where possible: no path parts, no control/bidi characters, no Windows-reserved names.

    ``ext`` is the extension of the real type: a contradicting known extension is replaced (``foto.pdf`` that is
    a JPEG becomes ``foto.jpg``), a missing one is added."""
    name = display_name(raw or "")
    name = _BAD_CHARS.sub("_", name).strip().rstrip(". ")
    if not name or name == "Anhang":
        name = default_stem
    stem, dot, current = name.rpartition(".")
    if not dot or not stem:
        stem, current = name, ""
    if ext:
        low = current.lower()
        if low == ext or (low == "jpeg" and ext == "jpg"):
            pass
        elif low and (low in KINDS or low in EXECUTABLE_EXTS or low == "jpeg"):
            current = ext
        else:
            stem, current = (f"{stem}.{current}" if current else stem), ext
    if _WIN_RESERVED.match(stem):
        stem = f"_{stem}"
    stem = stem[: max(1, 120 - len(current) - 1)] if current else stem[:120]
    return f"{stem}.{current}" if current else stem


def _candidates(name: str) -> Iterable[str]:
    yield name
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    for i in range(1, 1000):
        yield f"{stem} ({i}).{ext}" if ext else f"{stem} ({i})"
    yield f"{stem}-{secrets.token_hex(4)}.{ext}" if ext else f"{stem}-{secrets.token_hex(4)}"


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)


def create_exclusive(directory: Path, name: str) -> tuple[Path, int]:
    """Opens a new file under the first free name (``x.pdf``, ``x (1).pdf`` …) – never replaces anything."""
    directory.mkdir(parents=True, exist_ok=True)
    for candidate in _candidates(name):
        path = directory / candidate
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY, 0o600)
        except FileExistsError:
            continue
        return path, fd
    raise FileRejected("Kein freier Dateiname gefunden.")


def copy_exclusive(src: Path, directory: Path, name: str) -> Path:
    path, fd = create_exclusive(directory, name)
    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
            shutil.copyfileobj(inp, out, 1024 * 1024)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def move_exclusive(src: Path, directory: Path, name: str) -> Path:
    """Moves a finished temp file to its final name (same file system: rename; otherwise copy + delete)."""
    path, fd = create_exclusive(directory, name)
    os.close(fd)
    try:
        os.replace(src, path)
    except OSError:
        try:
            shutil.copyfile(src, path)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        src.unlink(missing_ok=True)
    path.chmod(0o600)  # the renamed inode keeps the mode of its source
    return path


# --------------------------------------------------------------------------------------------- locations
def default_user_dirs(home: Path) -> list[Path]:
    names = [
        "Desktop",
        "Documents",
        "Downloads",
        "Pictures",
        "Music",
        "Movies" if sys.platform == "darwin" else "Videos",
    ]
    return [home / n for n in names]


@dataclass(slots=True)
class PathPolicy:
    """Where Jarvis may put received files and from where it may take files to send."""

    allowed: list[Path]
    protected: list[Path] = field(default_factory=list)  # always refused (Jarvis data, secrets …)
    exceptions: list[Path] = field(default_factory=list)  # inside ``protected`` but fine (downloads, staging)
    home: Path = field(default_factory=Path.home)

    def __post_init__(self) -> None:
        self.allowed = [_resolve(p) for p in self.allowed]
        self.protected = [_resolve(p) for p in self.protected]
        self.exceptions = [_resolve(p) for p in self.exceptions]

    def resolve(self, raw: str) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise FileRejected("Pfad fehlt.")
        if "\x00" in raw:
            raise FileRejected("Ungültiger Pfad.")
        text = os.path.expandvars(raw.strip())
        if text == "~" or text.startswith(("~/", "~\\")):
            path = self.home / text[2:]  # "~" is the home this policy was built for
        else:
            path = Path(text).expanduser()
        if not path.is_absolute():
            path = self.home / path
        resolved = _resolve(path)  # symlinks are followed: a link out of an allowed folder is not inside it
        self._refuse_sensitive(resolved)
        return resolved

    def _refuse_sensitive(self, p: Path) -> None:
        parts = [x.lower() for x in p.parts]
        joined = "/".join(parts)
        if any(s in parts or f"/{s}" in joined for s in SENSITIVE_PARTS):
            raise FileRejected("Schlüssel- und Zugangsdaten-Ordner (.ssh, .gnupg, Schlüsselbund …) sind gesperrt.")
        if any(_within(p, e) for e in self.exceptions):
            return
        if any(_within(p, x) for x in self.protected):
            raise FileRejected("Jarvis' eigene Daten und Zugangsdaten sind kein erlaubter Speicher- oder Versandort.")

    def is_allowed(self, p: Path) -> bool:
        return any(_within(p, r) for r in [*self.allowed, *self.exceptions])

    def describe(self) -> list[str]:
        return [str(p) for p in self.allowed]


def _resolve(p: Path) -> Path:
    try:
        return p.resolve(strict=False)
    except (OSError, RuntimeError):
        return Path(os.path.abspath(p))


def _within(p: Path, root: Path) -> bool:
    return p == root or p.is_relative_to(root)


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{round(n / 1024)} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB".replace(".", ",")
    return f"{n / (1024 * 1024 * 1024):.2f} GB".replace(".", ",")


def unicode_fold(text: str) -> str:
    """Case- and accent-insensitive form for matching names (``Müller`` ~ ``mueller`` ~ ``muller``)."""
    t = text.casefold().replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c))
