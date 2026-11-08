"""Parse microdrama screenplay .txt files into structured Episodes / Scenes / Cues.

File format (per episode):
    EPISODE NUMBER: Episode 1
    EPISODE LOGLINE: ...
    CONTINUITY NOTES: ...
    --- SCRIPT START ---
    SCENE 01
    Scene heading: INT. CATHEDRAL – DAY
    Scene start: 00:00.00
    Scene end: 02:18.00
    **CAMERA:** / **ACTION:** / **SOUND FX:** / **SPEAKER (Emotion):**
    Start: (00:00.00) | End: (00:04.80)
    <text, may span lines>
    --- SCRIPT END ---
Header keys are matched case-insensitively (ep 6 uses upper case and has no "Scene end").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

TS = r"(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?"
RE_EP_NUM = re.compile(r"^EPISODE NUMBER:\s*Episode\s*(\d+)", re.I)
RE_LOGLINE = re.compile(r"^EPISODE LOGLINE:\s*(.*)", re.I)
RE_CONT = re.compile(r"^CONTINUITY NOTES:\s*(.*)", re.I)
RE_SCENE = re.compile(r"^SCENE\s+(\d+)\s*$", re.I)
RE_SC_HEAD = re.compile(r"^Scene heading:\s*(.*)", re.I)
RE_SC_START = re.compile(rf"^Scene start:\s*{TS}", re.I)
RE_SC_END = re.compile(rf"^Scene end:\s*{TS}", re.I)
RE_CUE = re.compile(r"^\*\*(.+?):\*\*\s*$")
RE_TIME = re.compile(rf"^Start:\s*\({TS}\)\s*\|\s*End:\s*\({TS}\)")
RE_FILE_EP = re.compile(r"episode-(\d+)", re.I)

# Speaker aliases observed in this show; anything else is normalised generically.
ALIASES = {
    "THE FORMER GROOM": "GERALD",
    "LADY IN GOLD GOWN": "CLARISSA",
    "MYSTERIOUS BEARDED MAN": "ALEXANDER",
    "THE BEARDED STRANGER": "ALEXANDER",
    "BEARDED STRANGER": "ALEXANDER",
}
TITLE_PREFIXES = ("LADY ", "LORD ", "THE ", "SIR ")
NON_DIALOGUE = {"CAMERA": "CAMERA", "ACTION": "ACTION", "SOUND FX": "SFX", "SFX": "SFX",
                "MUSIC": "SFX", "VO": "DIALOGUE"}


def ts_to_sec(m: tuple, offset: int = 0) -> float:
    mm, ss, frac = m[offset], m[offset + 1], m[offset + 2]
    f = float("0." + frac) if frac else 0.0
    return int(mm) * 60 + int(ss) + f


def fmt_ts(sec: float) -> str:
    mm, ss = divmod(max(sec, 0.0), 60)
    return f"{int(mm):02d}:{ss:05.2f}"


def normalize_speaker(raw: str) -> str:
    s = re.sub(r"\s+", " ", raw.strip().upper())
    if s in ALIASES:
        return ALIASES[s]
    for p in TITLE_PREFIXES:
        if s.startswith(p) and len(s) > len(p) + 2:
            rest = s[len(p):]
            # keep generic roles like "LORD" alone, strip honorific off names
            if rest not in ("IN GOLD GOWN",):
                s = rest
            break
    return ALIASES.get(s, s)


@dataclass
class Cue:
    cue_id: str
    episode: int
    idx: int
    kind: str           # CAMERA | ACTION | SFX | DIALOGUE
    speaker: str        # normalised speaker for DIALOGUE, else ""
    raw_label: str
    emotion: str
    t0: float
    t1: float
    text: str
    scene: int
    line: int

    def label(self) -> str:
        if self.kind == "DIALOGUE":
            emo = f" ({self.emotion})" if self.emotion else ""
            return f"{self.speaker}{emo}: {self.text}"
        return f"[{self.kind}] {self.text}"


@dataclass
class Scene:
    num: int
    heading: str = ""
    t0: float | None = None
    t1: float | None = None


@dataclass
class Episode:
    num: int
    path: Path
    logline: str = ""
    continuity: str = ""
    scenes: list[Scene] = field(default_factory=list)
    cues: list[Cue] = field(default_factory=list)

    @property
    def duration(self) -> float:
        ends = [c.t1 for c in self.cues]
        ends += [s.t1 for s in self.scenes if s.t1 is not None]
        return max(ends) if ends else 0.0

    def scene_of(self, t: float) -> Scene | None:
        for s in self.scenes:
            if s.t0 is not None and s.t0 <= t and (s.t1 is None or t < s.t1):
                return s
        return self.scenes[-1] if self.scenes else None

    def cues_between(self, t0: float, t1: float) -> list[Cue]:
        return [c for c in self.cues if c.t1 > t0 + 1e-6 and c.t0 < t1 - 1e-6]


def parse_episode(path: Path) -> Episode:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    m = RE_FILE_EP.search(path.name)
    ep = Episode(num=int(m.group(1)) if m else 0, path=path)
    scene: Scene | None = None
    pending: dict | None = None  # cue being built
    in_script = False

    def flush():
        nonlocal pending
        if pending and pending.get("t0") is not None:
            idx = len(ep.cues)
            ep.cues.append(Cue(
                cue_id=f"E{ep.num:02d}-C{idx:03d}", episode=ep.num, idx=idx,
                kind=pending["kind"], speaker=pending["speaker"], raw_label=pending["raw"],
                emotion=pending["emotion"], t0=pending["t0"], t1=pending["t1"],
                text=" ".join(pending["text"]).strip(), scene=scene.num if scene else 0,
                line=pending["line"]))
        pending = None

    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not in_script:
            if (mm := RE_EP_NUM.match(line)):
                ep.num = int(mm.group(1))
            elif (mm := RE_LOGLINE.match(line)):
                ep.logline = mm.group(1).strip()
            elif (mm := RE_CONT.match(line)):
                ep.continuity = mm.group(1).strip()
            elif line.startswith("--- SCRIPT START"):
                in_script = True
            continue
        if line.startswith("--- SCRIPT END"):
            flush()
            break
        if (mm := RE_SCENE.match(line)):
            flush()
            scene = Scene(num=int(mm.group(1)))
            ep.scenes.append(scene)
            continue
        if scene is not None and pending is None:
            if (mm := RE_SC_HEAD.match(line)):
                scene.heading = mm.group(1).strip(); continue
            if (mm := RE_SC_START.match(line)):
                scene.t0 = ts_to_sec(mm.groups()); continue
            if (mm := RE_SC_END.match(line)):
                scene.t1 = ts_to_sec(mm.groups()); continue
        if (mm := RE_CUE.match(line)):
            flush()
            label = mm.group(1).strip()
            emo_m = re.match(r"^(.*?)\s*\((.*)\)\s*$", label)
            name, emotion = (emo_m.group(1), emo_m.group(2)) if emo_m else (label, "")
            kind = NON_DIALOGUE.get(name.strip().upper(), "DIALOGUE")
            pending = {"kind": kind, "raw": label, "emotion": emotion.strip(),
                       "speaker": normalize_speaker(name) if kind == "DIALOGUE" else "",
                       "t0": None, "t1": None, "text": [], "line": i}
            continue
        if pending is not None:
            if pending["t0"] is None and (mm := RE_TIME.match(line)):
                g = mm.groups()
                pending["t0"], pending["t1"] = ts_to_sec(g, 0), ts_to_sec(g, 3)
            elif line and pending["t0"] is not None:
                pending["text"].append(line.strip('"').strip("“”") if pending["kind"] == "DIALOGUE" else line)
    flush()
    # repair missing scene bounds
    for k, s in enumerate(ep.scenes):
        sc_cues = [c for c in ep.cues if c.scene == s.num]
        if s.t0 is None:
            s.t0 = min((c.t0 for c in sc_cues), default=0.0)
        if s.t1 is None:
            nxt = ep.scenes[k + 1].t0 if k + 1 < len(ep.scenes) else None
            s.t1 = nxt if nxt is not None else max((c.t1 for c in sc_cues), default=s.t0)
    return ep


def load_show(show_dir: str | Path) -> list[Episode]:
    paths = sorted(Path(show_dir).glob("*.txt"))
    if not paths:
        raise FileNotFoundError(f"No .txt episodes in {show_dir}")
    eps = [parse_episode(p) for p in paths]
    if not any(e.cues for e in eps):
        raise ValueError("no timestamped cues found: expected '**CAMERA:**' / '**NAME (Emotion):**' blocks "
                         "followed by 'Start: (mm:ss.xx) | End: (mm:ss.xx)'")
    eps = [e for e in eps if e.cues]
    return sorted(eps, key=lambda e: e.num)
