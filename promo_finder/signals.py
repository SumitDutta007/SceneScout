"""Rule-based signals: how hooky is an opening, how cliffy is an ending,
how well does a stretch stand on its own, does it state the premise.

All scores are in [0, 1]. Each function also returns the list of reasons that fired,
so the report can explain *why* a window scored the way it did.
"""
from __future__ import annotations

import re

from .parse import Cue, Episode

HOOK_SECONDS = 3.0
CLIFF_SECONDS = 4.0


def _rx(words: list[str]) -> re.Pattern:
    """Whole-word alternation; each entry may itself be a small regex (e.g. 'slap(s|ped)?')."""
    return re.compile(r"\b(" + "|".join(words) + r")\b", re.I)


# Physical / visual shock in ACTION, CAMERA or SFX text - strong tier
SHOCK_ACTION = _rx([
    r"slap(s|ped|ping)?", r"strik(e|es|ing)", "struck", r"whip(s|ped|ping)?", r"lash(es|ed)?",
    r"punch(es|ed)?", r"shov(e|es|ed)", r"hurl(s|ed)?", r"throw(s|n)?", "threw",
    r"seiz(e|es|ed|ing)", r"drag(s|ged|ging)?", r"yank(s|ed)?", r"chok(e|es|ed|ing)",
    r"stab(s|bed|bing)?", r"blood(y|ied|-soaked)?",
    r"bleed(s|ing)?", r"flames?", "ablaze", "blaze", "inferno", r"on fire", r"set fire",
    r"explosions?", r"explod(e|es|ed|ing)", r"shatter(s|ed|ing)?", r"smash(es|ed|ing)?",
    r"slam(s|med|ming)?", r"collaps(e|es|ed|ing)", r"faint(s|ed)?", r"scream(s|ed|ing)?",
    r"shriek(s|ed|ing)?", r"poison(s|ed|ing)?", "arsenic", r"(drops?|sinks?) to one knee",
    r"arrest(s|ed)?", r"spit(s)?", r"rips?", r"stomp(s|ed|ing)?", r"trampl(e|es|ed|ing)",
    r"wrench(es|ed)?", r"grips? .{0,20}throat", r"(pins?|pinned) .{0,20}wall",
    r"(brandish|thrust|press|rais)(es|ed|ing)? (the |a |her |his )?(glowing|red-hot|hot|iron|blade|sword|dagger|knife|tong)",
])
# menace props: a threat is present but nothing has happened yet
PROPS = _rx(["riding crop", "dagger", r"swords?", "sabre", r"knife", r"blades?", r"chain(s|ed)",
             r"shackle(s|d)?", r"(hot|red-hot|branding|glowing) (iron|tong)", r"tongs?", "noose", "gallows",
             r"pistols?", r"cuff(s|ed)"])
# mild tier - emotional but not a thumb-stopper on its own
MILD_ACTION = _rx([r"gasp(s|ed|ing)?", r"kneel(s|ing)?", r"kiss(es|ed|ing)?", r"grab(s|bed|bing)?",
                   r"sob(s|bing)?", r"tears? (stream|roll|spill)", r"recoil(s|ed)?", r"glar(e|es|ing)",
                   r"sneer(s|ing)?", r"burst(s)? (through|open|in)", r"storm(s)? in", "mud"])
SHOCK_SFX = _rx([r"cracks?", r"smacks?", r"thuds?", r"booms?", r"explosions?", r"shatter(s|ing)?",
                 r"screams?", r"gasps?", r"slams?", r"clangs?", r"stings?", r"impacts?", r"roars?",
                 r"crash(es)?", r"bangs?", r"whoosh"])
AGGRO_CAMERA = _rx(["crash zoom", "snap zoom", "whip pan", "whip-pan", r"extreme close-up",
                    "slow motion", "slow-motion", "dutch angle", "handheld", "smash cut"])
ESTABLISHING = _rx(["establishing", "aerial", "drone", "exterior", "landscape", "sky",
                    "skyline", "sunrise", "sunset", r"fades? in"])

# Dialogue that lands as a hook / threat / reveal
SHOCK_LINE = _rx([
    "how dare", r"liars?", r"witch(craft)?", r"murder(er|ed)?", r"poison(ed|er)?", r"hang(ed)?",
    r"die", "dead", r"kill(ed)?", r"traitors?", r"beggars?", "worthless", r"disown(ed)?",
    r"betray(ed|al)?", r"thie(f|ves)", "guards", "arrest", "get out", "on your knees", "kneel",
    r"marry(ing)?", "married", "duke", "duchess", "alive", "the truth", "who are you",
    "what have you done", r"burn(ed)?", r"execut(e|ed|ion)", "invalid", "nobody", "plain",
    "gallows", "you will pay", "never", "stolen", r"steal", "framed", "guilty",
])
HOT_EMOTION = _rx([
    "cold(ly)?", "furious(ly)?", "fury", "enraged", "rage", r"mock(ing|ingly)?", r"sneer(ing)?",
    "cruel(ly)?", r"shock(ed)?", r"venom(ous)?", r"menac(ing|ingly)", r"scream(ing)?",
    r"desperate(ly)?", r"command(ing)?", r"threat(ening)?", r"contempt(uous)?", "vicious",
    "sadistic", "seething", "icy", "defiant", r"horrif(ied)?", r"terrified", r"panic(ked)?",
    r"outrag(e|ed)", "savage", r"taunt(ing)?", r"gloat(ing)?", "smug", "dismissive", "harsh",
    "booming", "lethal", "fierce", r"hiss(ing)?", "explosive", "blunt", "unforgiving", "arrogant",
])

# Ending signals
CLIFF_SFX = _rx(["cliffhanger", "ominous", r"swell(s|ing)?", r"stings?", "drone", "crescendo",
                 r"booms?", "thunder", r"heartbeats?", "cut to black", "abrupt(ly)?", "dramatic"])
CLIFF_CAMERA = _rx(["cut to black", "smash cut", r"freez(e|es)", "hard cut", r"push-in",
                    "push in", "slow push", "snap zoom", "crash zoom", "fade to black"])
THREAT_LINE = _rx([
    "you will", r"will (pay|hang|burn|die|regret|never|be)", "never", "unless", "or else",
    r"kill(s|ed)?", "until", "who are you", "what have you done", "regret", "gallows",
    r"execut(e|ed|ion)", "at dawn", "tomorrow", "destroy", "ruin", "the truth", "duke", "duchess",
    "alive", "invalid", r"witch(craft)?", "guilty", "dare", "dead", "coming", r"burn(s)?",
    "hang", "die", "mine", "end", "finished", "now", "wait",
])
# strong release: the story visibly comes to rest (always penalised, even at an episode end)
RESOLVE_STRONG = _rx(["sunlight", "hand-in-hand", r"walk(s|ing)? (away|off|out) together", "into the light",
                      r"leav(e|es|ing) together", "safe at last", "peacefully", r"falls? asleep"])
RESOLVE_WORDS = _rx([r"smil(e|es|ing)", "relief", r"reliev(ed)?", r"embrac(e|es|ing)",
                     r"laugh(s|ing)?", "peace(ful)?", "tender(ly)?", "warm(ly)?", r"soft(ly)?",
                     r"gentl(e|y)", "content(ed)?", "calm(ly)?", "comfort(s|ing)?", r"hug(s)?",
                     "sleep(s|ing)?", r"exhal(e|es)", "grateful", r"thank(s)?"])

# Premise lexicon: lines that tell a cold viewer what the show is about
PREMISE = _rx([
    "altar", r"wedding", r"brides?", "groom", r"marr(y|ied|iage|ying)", r"husband", "wife",
    r"beggars?", r"soldiers?", "duke", "duchess", r"disown(ed)?", "sister", r"fianc(e|ee|é)",
    r"jilt(ed)?", r"peasants?", "worthless", "inheritance", "plain", "nobody", r"scar(s|red)?",
    r"strangers?", "title", r"heirs?", "estate",
])
BACKREF = _rx(["remember", "earlier", "as i told", "last night", "before that",
               "like i said", "you promised", "as we planned", "the plan", "that ring",
               "that letter", "what happened"])
LEAD_PRONOUN = re.compile(r"^\s*(he|she|they|it|that|this|those|then|so|and|but|him|her)\b", re.I)


def _words(t: str) -> int:
    return len(re.findall(r"\w+", t))


def window_cues(ep: Episode, t0: float, t1: float) -> list[Cue]:
    return ep.cues_between(t0, t1)


def hook_score(ep: Episode, t0: float) -> tuple[float, list[str]]:
    cues = [c for c in ep.cues if t0 - 0.5 <= c.t0 < t0 + HOOK_SECONDS - 1e-6]
    s, why = 0.0, []
    act = " ".join(c.text for c in cues if c.kind in ("ACTION", "CAMERA"))
    sfx = " ".join(c.text for c in cues if c.kind == "SFX")
    lines = [c for c in cues if c.kind == "DIALOGUE" and c.t0 >= t0 - 0.2]
    if (m := SHOCK_ACTION.search(act)):
        s += 0.40; why.append(f"on-screen shock ('{m.group(0).lower()}')")
    elif (m := PROPS.search(act)):
        s += 0.20; why.append(f"menace in frame ('{m.group(0).lower()}')")
    elif (m := MILD_ACTION.search(act)):
        s += 0.15; why.append(f"emotional beat ('{m.group(0).lower()}')")
    if (m := SHOCK_SFX.search(sfx)):
        s += 0.10; why.append(f"impact sound ('{m.group(0).lower()}')")
    if AGGRO_CAMERA.search(act):
        s += 0.05; why.append("aggressive camera")
    if lines:
        s += 0.12
        first = lines[0]
        if _words(first.text) <= 8:
            s += 0.08; why.append("short punchy opening line")
        if re.search(r"[!?]", first.text):
            s += 0.05
        if (m := SHOCK_LINE.search(" ".join(c.text for c in lines))):
            s += 0.22; why.append(f"charged line ('{m.group(0).lower()}')")
        if HOT_EMOTION.search(" ".join(c.emotion for c in lines)):
            s += 0.08; why.append("hot delivery (" + lines[0].emotion.split(",")[0] + ")")
    if not lines and not SHOCK_ACTION.search(act) and ESTABLISHING.search(act):
        s -= 0.30; why.append("opens on establishing/atmosphere shot")
    if not cues:
        s -= 0.5
    return max(0.0, min(1.0, s)), why


def cliff_score(ep: Episode, t1: float) -> tuple[float, list[str]]:
    tail = [c for c in ep.cues if c.t1 > t1 - CLIFF_SECONDS + 1e-6 and c.t0 < t1 - 1e-6]
    s, why = 0.0, []
    sfx = " ".join(c.text for c in tail if c.kind == "SFX")
    cam = " ".join(c.text for c in tail if c.kind in ("CAMERA", "ACTION"))
    if (m := CLIFF_SFX.search(sfx)) or (m := re.search(r"\b(cliffhanger|swelling score|ominous|score swells)\b", cam, re.I)):
        s += 0.30; why.append(f"tension sting ('{m.group(0).lower()}')")
    if (m := CLIFF_CAMERA.search(cam)):
        s += 0.12; why.append(f"ending camera beat ('{m.group(0).lower()}')")
    is_ep_end = t1 >= ep.duration - 1.5
    if is_ep_end:
        s += 0.30; why.append("episode's own cliffhanger ending")
    # last line within the final 6s
    last_lines = [c for c in ep.cues if c.kind == "DIALOGUE" and t1 - 6.0 <= c.t1 <= t1 + 0.05]
    if last_lines:
        ll = last_lines[-1]
        if ll.text.rstrip().endswith("?"):
            s += 0.12; why.append("ends on an unanswered question")
        if (m := THREAT_LINE.search(ll.text)):
            s += 0.25; why.append(f"ends on a threat/reveal line ('{m.group(0).lower()}')")
        if ll.text.rstrip().endswith(("...", "—", "-")):
            s += 0.08; why.append("line cut off mid-thought")
        if HOT_EMOTION.search(ll.emotion):
            s += 0.05
    # interruption: a shock event (doors burst, explosion, strike) lands in the last seconds,
    # so the viewer sees the disruption but not its consequence
    late = [c for c in ep.cues if t1 - 6.0 <= c.t0 < t1 and c.kind in ("ACTION", "SFX")]
    if (m := SHOCK_ACTION.search(" ".join(c.text for c in late))) or \
       (m := SHOCK_SFX.search(" ".join(c.text for c in late if c.kind == "SFX"))):
        s += 0.20; why.append(f"cut lands on an interruption ('{m.group(0).lower()}')")
        if last_lines and last_lines[-1].t0 < late[0].t0 and last_lines[-1].text.rstrip().endswith("?"):
            s += 0.10; why.append("question interrupted before it's answered")
    # comfort at the cut usually releases tension; an authored episode ending is exempt
    # (a tearful smile at an unanswered proposal is still a cliffhanger)
    if (m := RESOLVE_STRONG.search(cam + " " + sfx)):
        s -= 0.30; why.append(f"story comes to rest at the cut ('{m.group(0).lower()}')")
    elif RESOLVE_WORDS.search(cam + " " + sfx) and not is_ep_end and s < 0.3:
        s -= 0.15; why.append("tension resolves at the cut (calm/relief)")
    # did tension resolve *right after* the cut? (if the scene continues calmly, the cut is
    # still unresolved for the viewer, but an ending that the scene immediately answers is
    # usually a mid-beat rather than a peak)
    if not is_ep_end:
        nxt = [c for c in ep.cues if t1 <= c.t0 < t1 + 3.0 and c.kind == "DIALOGUE"]
        if nxt and _words(nxt[0].text) > 0 and not THREAT_LINE.search(nxt[0].text):
            s -= 0.05
    return max(0.0, min(1.0, s)), why


def standalone_score(ep: Episode, t0: float, t1: float) -> tuple[float, list[str]]:
    cues = window_cues(ep, t0, t1)
    lines = [c for c in cues if c.kind == "DIALOGUE"]
    s, why = 1.0, []
    if lines and LEAD_PRONOUN.match(lines[0].text):
        s -= 0.15; why.append("first line leans on earlier context ('" + lines[0].text.split()[0] + "')")
    speakers = {c.speaker for c in lines}
    if len(speakers) > 4:
        s -= 0.08 * (len(speakers) - 4); why.append(f"{len(speakers)} speaking characters")
    br = sum(1 for c in lines if BACKREF.search(c.text))
    if br:
        s -= min(0.3, 0.1 * br); why.append("references earlier events")
    # later episodes depend on more accumulated plot (mild prior)
    s -= 0.20 * (ep.num - 1) / 49.0
    if not lines:
        s -= 0.25; why.append("no dialogue: meaning must come from visuals alone")
    return max(0.0, min(1.0, s)), why


def premise_score(ep: Episode, t0: float, t1: float) -> tuple[float, list[str]]:
    text = " ".join(c.text for c in window_cues(ep, t0, t1) if c.kind == "DIALOGUE")
    hits = sorted({m.group(0).lower() for m in PREMISE.finditer(text)})
    s = min(1.0, 0.25 * len(hits))
    return s, ([f"states the premise ({', '.join(hits[:4])})"] if hits else [])


def intensity(c: Cue) -> float:
    """Per-cue intensity used for picking stitch pieces and the viewer's heat strip."""
    t = c.text + " " + c.emotion
    v = 0.0
    if SHOCK_ACTION.search(t): v += 0.5
    elif PROPS.search(t): v += 0.25
    elif MILD_ACTION.search(t): v += 0.2
    if c.kind == "SFX" and (SHOCK_SFX.search(t) or CLIFF_SFX.search(t)): v += 0.3
    if c.kind == "DIALOGUE":
        if SHOCK_LINE.search(c.text): v += 0.3
        if HOT_EMOTION.search(c.emotion): v += 0.2
    return min(1.0, v)
