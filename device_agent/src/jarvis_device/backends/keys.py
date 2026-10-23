"""Key names → Windows virtual-key codes, X11 keysyms and JarvisBridge (macOS) names.

The Core speaks one vocabulary on every device: ``key`` is a single character or a name from ``NAMED``
(``enter``, ``tab``, ``esc``, ``f5`` …), ``modifiers`` are ``ctrl``, ``shift``, ``alt`` and ``win`` (``cmd`` and
``super`` are synonyms of ``win``). ``delete`` is the forward delete key, ``backspace`` deletes to the left.
"""

from __future__ import annotations

ALIASES = {
    "return": "enter",
    "escape": "esc",
    "del": "delete",
    "forwarddelete": "delete",
    "ins": "insert",
    "pgup": "pageup",
    "page_up": "pageup",
    "pgdn": "pagedown",
    "page_down": "pagedown",
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
    "print": "printscreen",
    "prtsc": "printscreen",
    "apps": "menu",
    "contextmenu": "menu",
    "mute": "volumemute",
    "spacebar": "space",
}

MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "strg": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "option": "alt",
    "opt": "alt",
    "win": "win",
    "windows": "win",
    "super": "win",
    "meta": "win",
    "cmd": "win",
    "command": "win",
}

# --------------------------------------------------------------------------- Windows
VK: dict[str, int] = {
    "enter": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "capslock": 0x14,
    "printscreen": 0x2C,
    "menu": 0x5D,
    "volumemute": 0xAD,
    "volumedown": 0xAE,
    "volumeup": 0xAF,
    "nexttrack": 0xB0,
    "prevtrack": 0xB1,
    "playpause": 0xB3,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
}
VK_MODIFIERS = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B}
# keys that need KEYEVENTF_EXTENDEDKEY (the navigation block, the Windows and menu keys)
VK_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5D, 0x2C}

# --------------------------------------------------------------------------- X11 (xdotool)
KEYSYM: dict[str, str] = {
    "enter": "Return",
    "tab": "Tab",
    "esc": "Escape",
    "space": "space",
    "backspace": "BackSpace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "Prior",
    "pagedown": "Next",
    "left": "Left",
    "up": "Up",
    "right": "Right",
    "down": "Down",
    "capslock": "Caps_Lock",
    "printscreen": "Print",
    "menu": "Menu",
    "volumemute": "XF86AudioMute",
    "volumedown": "XF86AudioLowerVolume",
    "volumeup": "XF86AudioRaiseVolume",
    "nexttrack": "XF86AudioNext",
    "prevtrack": "XF86AudioPrev",
    "playpause": "XF86AudioPlay",
    **{f"f{i}": f"F{i}" for i in range(1, 25)},
}
KEYSYM_CHARS = {
    "+": "plus",
    "-": "minus",
    ",": "comma",
    ".": "period",
    "/": "slash",
    ";": "semicolon",
    "=": "equal",
    "'": "apostrophe",
    "[": "bracketleft",
    "]": "bracketright",
    "\\": "backslash",
    "`": "grave",
    "#": "numbersign",
    "*": "asterisk",
    "<": "less",
    ">": "greater",
    "ä": "adiaeresis",
    "ö": "odiaeresis",
    "ü": "udiaeresis",
    "ß": "ssharp",
}
KEYSYM_MODIFIERS = {"ctrl": "ctrl", "shift": "shift", "alt": "alt", "win": "super"}

# --------------------------------------------------------------------------- macOS (JarvisBridge /input/key)
BRIDGE: dict[str, str] = {
    "enter": "return",
    "delete": "forwarddelete",
    "backspace": "backspace",
    "esc": "escape",
}
BRIDGE_MODIFIERS = {"ctrl": "ctrl", "shift": "shift", "alt": "alt", "win": "cmd"}


class KeyError_(ValueError):
    pass


def canonical_key(key: str) -> str:
    """Lower-case canonical name, or the single character itself (case kept: 'A' means shift+a)."""
    if len(key) == 1:
        return key
    k = key.strip().lower().replace(" ", "")
    k = ALIASES.get(k, k)
    if k in VK or k in KEYSYM:
        return k
    raise KeyError_(f"Unbekannte Taste „{key}“")


def canonical_modifiers(mods: list[str] | None) -> list[str]:
    out: list[str] = []
    for m in mods or []:
        c = MODIFIER_ALIASES.get(str(m).strip().lower())
        if c is None:
            raise KeyError_(f"Unbekannte Zusatztaste „{m}“")
        if c not in out:
            out.append(c)
    return out


def xdotool_combo(key: str, modifiers: list[str]) -> str:
    k = canonical_key(key)
    sym = KEYSYM.get(k) or KEYSYM_CHARS.get(k) or k
    return "+".join([*(KEYSYM_MODIFIERS[m] for m in canonical_modifiers(modifiers)), sym])


def bridge_key(key: str, modifiers: list[str]) -> dict[str, object]:
    k = canonical_key(key)
    return {"key": BRIDGE.get(k, k), "modifiers": [BRIDGE_MODIFIERS[m] for m in canonical_modifiers(modifiers)]}
