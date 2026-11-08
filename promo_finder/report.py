"""Write promos.json, report.md and viewer.html."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .candidates import Promo
from .parse import Episode, fmt_ts
from . import signals as sg


def _excerpt(p: Promo, eps: dict[int, Episode]) -> list[dict]:
    out = []
    for si, s in enumerate(p.segments):
        ep = eps[s.episode]
        rows = []
        for c in ep.cues_between(s.start, s.end):
            zone = ""
            if si == 0 and c.t0 < s.start + sg.HOOK_SECONDS:
                zone = "hook"
            if si == len(p.segments) - 1 and c.t1 > s.end - sg.CLIFF_SECONDS:
                zone = "cliff" if not zone else zone
            rows.append({"id": c.cue_id, "t0": fmt_ts(c.t0), "t1": fmt_ts(c.t1), "kind": c.kind,
                         "speaker": c.speaker, "emotion": c.emotion, "text": c.text, "zone": zone,
                         "heat": round(sg.intensity(c), 2)})
        out.append({"episode": s.episode, "start": fmt_ts(s.start), "end": fmt_ts(s.end),
                    "heading": s.scene_heading, "file": ep.path.name, "cues": rows})
    return out


def write_json(path: Path, promos: list[Promo], meta: dict) -> None:
    path.write_text(json.dumps({"meta": meta, "promos": [p.to_dict() for p in promos]},
                               indent=2, ensure_ascii=False), encoding="utf-8")


def _seg_str(p: Promo) -> str:
    return "<br>".join(f"Ep {s.episode} {fmt_ts(s.start)}–{fmt_ts(s.end)}" for s in p.segments)


def write_markdown(path: Path, show: str, promos: list[Promo], eps: dict[int, Episode], meta: dict) -> None:
    L = [f"# Promo picks: *{show}*", "",
         f"Mode: **{meta['mode']}** · {meta['episodes']} episodes · {meta['diagnostics']['candidates']} candidates "
         f"→ {len(promos)} promos. Times are `mm:ss.xx` from each episode's script.", ""]
    if meta.get("strategy"):
        L += ["**Posting strategy (Claude):** " + meta["strategy"], ""]
    L += ["## Ranked promos (posting order)", "",
          "| # | Segments | Len | Hook (first 3s) | Cliffhanger (cut) | Why it works | Score |",
          "|---|---|---|---|---|---|---|"]
    for p in promos:
        L.append(f"| {p.rank} | {_seg_str(p)} | {p.duration:.0f}s | {p.hook.replace('|', '/')} | "
                 f"{p.cliffhanger.replace('|', '/')} | {p.why.replace('|', '/')} | {p.scores['final']:.2f} |")
    L += ["", "## Post these first, and why", ""]
    for p in promos[:5]:
        L.append(f"{p.rank}. **{p.id}** ({_seg_str(p).replace('<br>', ' + ')}): {p.post_reason}")
    L += ["", "## Promo detail", ""]
    for p in promos:
        L += [f"### {p.id}: rank {p.rank} · {p.kind} · {p.duration:.1f}s · source: {p.source}", ""]
        for s in p.segments:
            L.append(f"- **Ep {s.episode}** `{fmt_ts(s.start)} → {fmt_ts(s.end)}` ({s.duration:.1f}s), "
                     f"{s.scene_heading} · cues {s.cue_ids[0]}…{s.cue_ids[-1]}")
        L += ["", f"- **Hook:** {p.hook}", f"- **Cliffhanger:** {p.cliffhanger}", f"- **Why:** {p.why}",
              f"- **Post order:** {p.post_reason}",
              "- **Scores:** " + ", ".join(f"{k} {v:.2f}" for k, v in p.scores.items())]
        if p.llm.get("scores"):
            L.append("- **Claude scores (1–5):** " + ", ".join(f"{k} {v}" for k, v in p.llm["scores"].items()))
            if p.llm.get("cold_viewer_test"):
                L.append(f"- **Cold-viewer test:** {p.llm['cold_viewer_test']}")
        if p.flags:
            L.append("- **Flags:** " + "; ".join(p.flags))
        L.append("")
        L.append("<details><summary>Script excerpt</summary>\n")
        for seg in _excerpt(p, eps):
            L.append(f"**Ep {seg['episode']} {seg['start']}–{seg['end']}**\n")
            for c in seg["cues"]:
                mark = {"hook": "🟧 ", "cliff": "🟪 "}.get(c["zone"], "")
                who = f"{c['speaker']} ({c['emotion']})" if c["kind"] == "DIALOGUE" else c["kind"]
                L.append(f"- {mark}`{c['t0']}` **{who}:** {c['text']}")
            L.append("")
        L.append("</details>\n")
    d = meta["diagnostics"]
    L += ["## Quality check (automatic)", "",
          f"- Episodes used in the selection: {len(d['episodes_used_in_selection'])}/{d['episodes']}; never used: "
          f"{d['episodes_never_used']}",
          f"- Episodes whose best candidate is weak (< 0.55): {d['weak_episodes'] or 'none'}",
          f"- Hook trigger words behind the selection: {d['hook_trigger_words']}",
          f"- Visual-only hooks (no line in the first 3s): {d['visual_only_hooks']}",
          f"- Stitched promos selected: {d['stitched_selected']} · late-season (ep ≥ 39): {d['late_season_selected']}",
          ""]
    if meta.get("gold"):
        L += ["### Against editor picks", "", "```", json.dumps(meta["gold"], indent=2), "```", ""]
    if meta.get("llm_log"):
        L += ["### LLM log", ""] + [f"- {x}" for x in meta["llm_log"]] + [""]
    flagged = [(p.id, f) for p in promos for f in p.flags]
    if flagged:
        L += ["### Per-promo flags", ""] + [f"- **{i}**: {f}" for i, f in flagged] + [""]
    L += ["See `ANALYSIS.md` for the hand review of where the tool picks badly.", ""]
    path.write_text("\n".join(L), encoding="utf-8")


VIEWER = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Promo picks · __SHOW__</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--line:#262a33;--fg:#e7e9ee;--mut:#9aa1ad;--hook:#ff9f43;--cliff:#a78bfa;--ok:#34d399;--warn:#fbbf24}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
header{padding:20px 24px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:2}
h1{margin:0 0 4px;font-size:20px}.mut{color:var(--mut)}
.controls{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
input,select{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 8px}
main{padding:20px 24px;display:grid;gap:14px;max-width:1100px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.top{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
.rank{font-size:22px;font-weight:700;min-width:36px}.pill{font-size:11px;padding:2px 8px;border-radius:99px;border:1px solid var(--line);color:var(--mut)}
.segs{font-family:ui-monospace,Consolas,monospace;font-size:13px}
.row{display:grid;grid-template-columns:110px 1fr;gap:6px;margin-top:6px}.row b{color:var(--mut);font-weight:500}
.hook{color:var(--hook)}.cliff{color:var(--cliff)}
.bars{display:flex;gap:14px;margin-top:10px;flex-wrap:wrap}.bar{width:120px}.bar span{display:block;height:6px;background:var(--line);border-radius:3px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--ok)}
.flags{margin-top:8px;color:var(--warn);font-size:12px}
details{margin-top:10px}summary{cursor:pointer;color:var(--mut)}
table{width:100%;border-collapse:collapse;margin-top:6px;font-size:12.5px}td{padding:3px 6px;border-top:1px solid var(--line);vertical-align:top}
td.t{font-family:ui-monospace,Consolas,monospace;color:var(--mut);white-space:nowrap}td.k{color:var(--mut);white-space:nowrap}
tr.z-hook td:first-child{border-left:3px solid var(--hook)}tr.z-cliff td:first-child{border-left:3px solid var(--cliff)}
.seghead{margin-top:10px;font-weight:600}
</style></head><body>
<header><h1>Promo picks · __SHOW__</h1><div class="mut" id="meta"></div>
<div class="controls">
<input id="q" placeholder="Search hook / line / character…" size="30">
<select id="kind"><option value="">All types</option><option value="single">Single stretch</option><option value="stitched">Stitched</option></select>
<select id="sort"><option value="rank">Sort: posting order</option><option value="final">Sort: score</option><option value="ep">Sort: episode</option></select>
<label class="mut">Max episode <input id="maxep" type="number" min="1" value="999" style="width:70px"></label>
<label class="mut"><input id="noflag" type="checkbox"> Hide flagged</label>
</div></header>
<main id="list"></main>
<script>
const DATA = __DATA__;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
$('#meta').textContent = `${DATA.meta.mode} mode · ${DATA.meta.episodes} episodes · ${DATA.promos.length} promos` + (DATA.meta.strategy ? ' · ' + DATA.meta.strategy : '');
function bar(label, v){return `<div class="bar mut">${label} ${v.toFixed(2)}<span><i style="width:${Math.round(Math.min(1,v)*100)}%"></i></span></div>`}
function render(){
  const q = $('#q').value.toLowerCase(), kind = $('#kind').value, maxep = +$('#maxep').value || 999, nf = $('#noflag').checked;
  let ps = DATA.promos.filter(p => (!kind || p.kind === kind) && Math.max(...p.segments.map(s => s.episode)) <= maxep && (!nf || !p.flags.length));
  if (q) ps = ps.filter(p => JSON.stringify([p.hook, p.cliffhanger, p.why, DATA.excerpts[p.id]]).toLowerCase().includes(q));
  const s = $('#sort').value;
  ps.sort((a,b) => s === 'final' ? b.scores.final - a.scores.final : s === 'ep' ? a.segments[0].episode - b.segments[0].episode || a.segments[0].start - b.segments[0].start : a.rank - b.rank);
  $('#list').innerHTML = ps.map(p => `
  <div class="card">
    <div class="top"><div class="rank">#${p.rank}</div><div class="segs">${p.segments.map(s => `Ep ${s.episode} ${s.start_ts} → ${s.end_ts}`).join(' &nbsp;+&nbsp; ')}</div>
      <span class="pill">${p.kind}</span><span class="pill">${p.duration.toFixed(1)}s</span><span class="pill">${p.source}</span><span class="pill">score ${p.scores.final.toFixed(2)}</span></div>
    <div class="row"><b>Hook</b><div class="hook">${esc(p.hook)}</div></div>
    <div class="row"><b>Cliffhanger</b><div class="cliff">${esc(p.cliffhanger)}</div></div>
    <div class="row"><b>Why</b><div>${esc(p.why)}</div></div>
    <div class="row"><b>Post order</b><div class="mut">${esc(p.post_reason)}</div></div>
    ${p.llm && p.llm.cold_viewer_test ? `<div class="row"><b>Cold viewer</b><div class="mut">${esc(p.llm.cold_viewer_test)}</div></div>` : ''}
    <div class="bars">${bar('hook', p.scores.hook)}${bar('cliff', p.scores.cliff)}${bar('standalone', p.scores.standalone)}${bar('premise', p.scores.premise)}</div>
    ${p.flags.length ? `<div class="flags">⚠ ${p.flags.map(esc).join('<br>⚠ ')}</div>` : ''}
    <details><summary>Script excerpt (<span class="hook">■ hook window</span> · <span class="cliff">■ cliffhanger window</span>)</summary>
      ${DATA.excerpts[p.id].map(seg => `<div class="seghead">Ep ${seg.episode} · ${seg.start}–${seg.end} · ${esc(seg.heading)} <span class="mut">(${esc(seg.file)})</span></div>
      <table>${seg.cues.map(c => `<tr class="z-${c.zone}"><td class="t">${c.t0}</td><td class="k">${c.kind === 'DIALOGUE' ? esc(c.speaker) + (c.emotion ? ' <span class="mut">(' + esc(c.emotion) + ')</span>' : '') : c.kind}</td><td>${esc(c.text)}</td></tr>`).join('')}</table>`).join('')}
    </details>
  </div>`).join('') || '<div class="mut">No promos match.</div>';
}
['#q','#kind','#sort','#maxep','#noflag'].forEach(s => $(s).addEventListener('input', render));
render();
</script></body></html>"""


def write_viewer(path: Path, show: str, promos: list[Promo], eps: dict[int, Episode], meta: dict) -> None:
    data = {"meta": {k: v for k, v in meta.items() if k in ("mode", "episodes", "strategy")},
            "promos": [p.to_dict() for p in promos],
            "excerpts": {p.id: _excerpt(p, eps) for p in promos}}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(VIEWER.replace("__SHOW__", html.escape(show)).replace("__DATA__", blob), encoding="utf-8")
