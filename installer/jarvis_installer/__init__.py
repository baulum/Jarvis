"""J.A.R.V.I.S. installer for Windows and Linux (the Mac keeps scripts/setup.sh).

Standard library only, so it runs before anything else is installed. It checks and reports missing dependencies
(Obsidian, Tailscale, Claude Code, PostgreSQL, …) with an install hint per OS, downloads and verifies the models
(Piper voice, Whisper, wake word) and guides through the first setup of the three roles:

- Core Server   – agents, memory, tools, API (usually stays on the Mac; on Windows/Linux possible)
- Client        – desktop app or browser, connected to a Core over Tailscale
- Device Agent  – lets the Core control this computer (programs, mouse, keyboard, screenshots, files …)
"""

__version__ = "0.1.0"
