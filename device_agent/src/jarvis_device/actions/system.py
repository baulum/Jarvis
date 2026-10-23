"""System information of the device (psutil – identical on Windows, Linux and macOS)."""

from __future__ import annotations

import platform
import socket
import time
from pathlib import Path
from typing import Any

import psutil

from jarvis_device import __version__


def system_info(roots: list[Path]) -> dict[str, Any]:
    vm = psutil.virtual_memory()
    disks = []
    seen: set[str] = set()
    for part in psutil.disk_partitions(all=False):
        if part.mountpoint in seen or "loop" in part.device or part.fstype in ("squashfs", "tmpfs"):
            continue
        seen.add(part.mountpoint)
        try:
            du = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append({"mount": part.mountpoint, "total": du.total, "free": du.free, "percent": du.percent})
    battery = None
    try:
        b = psutil.sensors_battery()
    except (AttributeError, OSError):
        b = None
    if b is not None:
        battery = {"percent": round(b.percent), "plugged": bool(b.power_plugged)}
    top = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        mem = p.info.get("memory_info")
        if mem is not None:
            top.append({"pid": p.info["pid"], "name": p.info.get("name"), "rss": mem.rss})
    top.sort(key=lambda x: x["rss"], reverse=True)
    return {
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "os_version": platform.version(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "agent_version": __version__,
        "uptime_s": int(time.time() - psutil.boot_time()),
        "cpu": {"count": psutil.cpu_count(), "percent": psutil.cpu_percent(interval=0.2)},
        "memory": {"total": vm.total, "available": vm.available, "percent": vm.percent},
        "disks": disks,
        "battery": battery,
        "top_processes": top[:10],
        "allowed_roots": [str(r) for r in roots],
    }
