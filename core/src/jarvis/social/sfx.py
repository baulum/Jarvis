"""Sound design without third-party samples: every effect and music bed is synthesized by FFmpeg (``aevalsrc`` /
``anoisesrc``) from the formulas below – no licence questions, identical on macOS, Linux and Windows.

The storyboard places cues per scene (``sfx: [{sound, at, gain}]``); every scene cut also gets a soft transition
sound unless the scene brings its own at 0 s. ``mix_command`` builds one FFmpeg call that mixes cues, the music bed
and an optional voice-over, limits peaks and normalizes loudness for social feeds (−14 LUFS, true peak −1.5 dBTP).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.social.storyboard import FPS

SR = 48000
TARGET_LUFS = -14.0
TRUE_PEAK = -1.5


def _eval(expr: str, d: float) -> str:
    return f"aevalsrc=exprs='{expr}':d={d}:s={SR}"


def _noise(d: float, color: str, chain: str) -> str:
    return f"anoisesrc=d={d}:c={color}:r={SR}:a=0.8:seed=7,{chain}"


# name → (lavfi source, duration in s). Formulas: t = seconds since the cue starts.
SOUNDS: dict[str, tuple[str, float]] = {
    "whoosh": (
        _noise(0.55, "pink", "highpass=f=300,lowpass=f=4200,afade=t=in:d=0.3:curve=exp,afade=t=out:st=0.3:d=0.25"),
        0.55,
    ),
    "swipe": (
        _noise(0.3, "white", "highpass=f=900,lowpass=f=7000,afade=t=in:d=0.12,afade=t=out:st=0.12:d=0.18,volume=0.6"),
        0.3,
    ),
    "swoosh_down": (_eval("0.5*sin(2*PI*(900*t-650*t*t))*exp(-3*t)", 0.6), 0.6),
    "click": (_eval("0.8*sin(2*PI*2200*t)*exp(-120*t)", 0.06), 0.06),
    "tick": (_eval("0.6*sin(2*PI*4200*t)*exp(-260*t)", 0.03), 0.03),
    "pop": (_eval("0.8*sin(2*PI*(620*t-1200*t*t))*exp(-32*t)", 0.14), 0.14),
    "ding": (_eval("0.38*sin(2*PI*1318.5*t)*exp(-4*t)+0.16*sin(2*PI*2637*t)*exp(-6*t)", 1.2), 1.2),
    "notify": (
        _eval("0.42*(sin(2*PI*1046.5*t)*exp(-22*t)+gte(t,0.11)*sin(2*PI*1568*(t-0.11))*exp(-10*(t-0.11)))", 0.7),
        0.7,
    ),
    "error_buzz": (_eval("0.32*sgn(sin(2*PI*110*t))*lt(mod(t,0.18),0.13)*exp(-1.5*t)", 0.45) + ",lowpass=f=2400", 0.45),
    "glitch": (_eval("0.45*(2*random(0)-1)*lt(mod(t*33,1),0.45)*exp(-4*t)", 0.32) + ",acrusher=bits=6:mode=lin", 0.32),
    "bass_hit": (_eval("0.95*sin(2*PI*(45*t+9.375*(1-exp(-8*t))))*exp(-3.2*t)", 1.0), 1.0),
    "riser": (
        _eval("0.4*(t/1.2)*sin(2*PI*(200*t+583*t*t))+0.12*(t/1.2)*(2*random(0)-1)", 1.2) + ",highpass=f=150",
        1.2,
    ),
    "typing": (_eval("0.55*sin(2*PI*3100*t)*exp(-220*mod(t,0.083))*lt(mod(t,0.083),0.03)", 0.85), 0.85),
    "shutter": (
        _noise(
            0.2, "white", "volume='if(lt(t,0.05),1,if(lt(t,0.09),0,if(lt(t,0.15),0.8,0)))':eval=frame,highpass=f=1200"
        ),
        0.2,
    ),
    "success": (
        _eval(
            "0.32*(sin(2*PI*523.25*t)*exp(-9*t)+gte(t,0.08)*sin(2*PI*659.25*(t-0.08))*exp(-9*(t-0.08))"
            "+gte(t,0.16)*sin(2*PI*783.99*(t-0.16))*exp(-5*(t-0.16)))",
            0.9,
        ),
        0.9,
    ),
}

# Music beds: generated for the full length. 120 BPM pulse, low tension drone, a soft major-seventh pad.
MUSIC: dict[str, str] = {
    "pulse": "0.55*sin(2*PI*(48*mod(t,0.5)+7.5*(1-exp(-16*mod(t,0.5)))))*exp(-9*mod(t,0.5))"
    "+0.06*(2*random(1)-1)*exp(-70*mod(t+0.25,0.5))+0.08*sin(2*PI*55*t)",
    "tension": "0.22*sin(2*PI*55*t)*(0.6+0.4*sin(2*PI*0.5*t))+0.14*sin(2*PI*82.41*t)"
    "+0.10*sin(2*PI*1760*t)*exp(-90*mod(t,0.5))",
    "glow": "0.09*(sin(2*PI*130.81*t)+sin(2*PI*164.81*t)+sin(2*PI*196*t)+sin(2*PI*246.94*t))"
    "*(0.75+0.25*sin(2*PI*0.25*t))",
}
MUSIC_GAIN = {"pulse": 0.5, "tension": 0.55, "glow": 0.6}

# Default sound on a cut, by transition (a scene can override with its own cue at 0 s).
TRANSITION_SOUND = {
    "fade": None, "cut": None, "slide_up": "swipe", "slide_left": "swipe", "push": "whoosh", "wipe": "swipe",
    "zoom": "whoosh", "glitch": "glitch", "flash": "bass_hit", "blur": "whoosh", "whip": "whoosh", "iris": "riser",
}  # fmt: skip


@dataclass(slots=True)
class Cue:
    sound: str
    at: float  # seconds from the start of the video
    gain: float = 0.8


def cues_for(board: dict[str, Any]) -> list[Cue]:
    """All cues on the video timeline: scene sounds plus transition sounds on the cuts (synchronous to frames)."""
    out: list[Cue] = []
    start = 0.0
    for i, scene in enumerate(board.get("scenes") or []):
        own = scene.get("sfx") or []
        if i > 0 and not any(float(c.get("at", 0)) < 0.05 for c in own):
            snd = TRANSITION_SOUND.get(scene.get("transition") or "cut")
            if snd:
                out.append(Cue(snd, max(0.0, start - (0.25 if snd in ("whoosh", "riser") else 0.0)), 0.55))
        for c in own:
            if c.get("sound") in SOUNDS:
                out.append(Cue(c["sound"], start + float(c.get("at", 0)), float(c.get("gain", 0.8))))
        start += round(float(scene.get("duration", 0)) * FPS) / FPS
    out.sort(key=lambda c: c.at)
    return out[:40]


def mix_command(
    ffmpeg: str,
    video: Path,
    out: Path,
    cues: list[Cue],
    music: str,
    duration: float,
    voiceover: Path | None = None,
    loud_gain_db: float = 0.0,
) -> list[str]:
    """One FFmpeg call: silent video + synthesized audio → MP4 (H.264 copied, AAC 48 kHz stereo, faststart)."""
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video)]
    labels: list[str] = []
    graph: list[str] = []
    idx = 1
    # a silent base track makes the mix exactly as long as the video even without any cue
    argv += ["-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=mono:d={duration:.3f}"]
    labels.append(f"[{idx}:a]")
    idx += 1
    for n, cue in enumerate(cues):
        src, _ = SOUNDS[cue.sound]
        argv += ["-f", "lavfi", "-i", src]
        delay = round(cue.at * 1000)
        graph.append(f"[{idx}:a]volume={cue.gain:.2f},adelay={delay}:all=1[c{n}]")
        labels.append(f"[c{n}]")
        idx += 1
    if music in MUSIC:
        argv += ["-f", "lavfi", "-i", _eval(MUSIC[music], round(duration + 0.1, 3))]
        gain = MUSIC_GAIN[music] * (0.45 if voiceover else 1.0)
        fade_at = max(0.0, duration - 0.6)
        graph.append(
            f"[{idx}:a]lowpass=f=9000,volume={gain:.2f},afade=t=in:d=0.25,afade=t=out:st={fade_at:.2f}:d=0.6[music]"
        )
        labels.append("[music]")
        idx += 1
    if voiceover is not None:
        argv += ["-i", str(voiceover)]
        graph.append(f"[{idx}:a]aresample={SR},pan=mono|c0=c0,volume=1.6[vo]")
        labels.append("[vo]")
        idx += 1
    graph.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,"
        f"aformat=sample_rates={SR}:channel_layouts=stereo,"
        f"loudnorm=I={TARGET_LUFS + loud_gain_db}:TP={TRUE_PEAK}:LRA=11,"
        "alimiter=limit=0.79:attack=2:release=40:level=0,"
        f"aresample={SR}[aout]"
    )
    argv += [
        "-filter_complex", ";".join(graph),
        "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-ac", "2",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out),
    ]  # fmt: skip
    return argv
