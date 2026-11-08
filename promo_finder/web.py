"""Web pipeline, split into short phases so each fits a serverless time limit.

The browser drives it:
  1. POST /api/judge     {files, group}          -> {items, groups}  (once per group of 5 episodes, in parallel)
  2. POST /api/stitch    {files, items}          -> {items}          (cross-episode promos)
  3. POST /api/finalize  {files, items, stitched, top} -> {viewer_html, promos}

The server keeps no state between calls. Every phase re-parses the uploaded scripts, and model output
travels between phases only as cue-ID items that are re-validated against the script each time, so the
browser can never inject timestamps. If the model is unavailable (no key, quota, timeout), each phase
degrades quietly to the rule engine and the user still gets a result. Details go to the server log only.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from http.server import BaseHTTPRequestHandler

from .parse import load_texts
from . import candidates as C
from . import llm, quality, rank, report

PER_GROUP = 5
TIME_BUDGET = float(os.environ.get("PROMO_TIME_BUDGET", "55"))   # seconds per phase (function limit is 60)
MAX_BODY = 4_000_000
MAX_FILES = 300


def _setup() -> bool:
    """Configure the model layer for a web request. Returns True if a model is available."""
    llm.load_dotenv()
    provider = os.environ.get("PROMO_PROVIDER", "gemini")
    llm.configure(provider, os.environ.get("PROMO_MODEL") or
                  ("gemini-3-flash-preview" if provider == "gemini" else None))
    if not llm.FALLBACK_MODELS and provider == "gemini":
        llm.FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]
    llm.CACHE_DIR = llm.Path(os.environ.get("PROMO_CACHE_DIR", "/tmp/promo_cache" if os.environ.get("VERCEL")
                                            else ".cache"))
    llm.DEADLINE = time.time() + TIME_BUDGET
    llm.REQUEST_TIMEOUT = TIME_BUDGET
    llm.MAX_RETRY_DELAY = 20
    llm.GEMINI_MIN_GAP = 0.0          # one call per invocation; the browser paces parallel calls
    ok, _ = llm.available()
    return ok


def _episodes(payload: dict):
    files = payload.get("files") or []
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise ValueError(f"Add between 1 and {MAX_FILES} script files.")
    pairs = [(os.path.basename(str(f.get("name") or f"script-{i + 1}.txt")), str(f.get("text") or ""))
             for i, f in enumerate(files)]
    try:
        eps = load_texts(pairs)
    except ValueError:
        raise ValueError("These files don't look like timestamped screenplays. Each script needs cue blocks "
                         "such as **CAMERA:** or **NAME (Emotion):** followed by "
                         "'Start: (mm:ss.xx) | End: (mm:ss.xx)'.") from None
    C.configure_cast(eps)
    return eps


def _log(msg: str) -> None:
    print(msg, flush=True)   # server log only


# ---------------------------------------------------------------- phases

def judge(payload: dict) -> dict:
    eps = _episodes(payload)
    groups = math.ceil(len(eps) / PER_GROUP)
    g = int(payload.get("group") or 0)
    if not 0 <= g < groups:
        raise ValueError("bad group index")
    group = eps[g * PER_GROUP:(g + 1) * PER_GROUP]
    items: list[dict] = []
    if _setup():
        rule_cands = [p for ep in group for p in C.single_candidates(ep)]
        items = llm.judge_raw(eps, group, rule_cands, log=_log)
    return {"items": items, "groups": groups, "episodes": len(eps)}


def stitch(payload: dict) -> dict:
    eps = _episodes(payload)
    if not _setup():
        return {"items": []}
    judged = llm.validate_items(payload.get("items") or [], eps, "judge", _log)
    if not judged:
        return {"items": []}
    moments = sorted(judged, key=lambda p: -rank.final_score(p))[:20]
    return {"items": llm.stitch_raw(eps, moments, log=_log)}


def finalize(payload: dict) -> dict:
    eps = _episodes(payload)
    by_num = {e.num: e for e in eps}
    top = max(1, min(30, int(payload.get("top") or 15)))
    ready = _setup()
    judged = llm.validate_items(payload.get("items") or [], eps, "judge", _log)
    judged += llm.validate_items(payload.get("stitched") or [], eps, "stitch", _log)
    cands = judged + C.all_candidates(eps)
    chosen = rank.select(cands, top=top)
    strategy = ""
    if ready and judged:
        strategy = llm.apply_rank(chosen, llm.rank_pass(eps, chosen, log=_log))
    ordered = rank.posting_order(chosen)
    for p in ordered:
        p.flags = quality.flag_promo(p, by_num)
    show = re.sub(r"[^\w .,'()-]", "", str(payload.get("show") or "")).strip()[:80] or "Your show"
    meta = {"mode": "", "episodes": len(eps), "strategy": strategy, "public": True}
    html = report.render_viewer(show, ordered, by_num, meta, public=True)
    return {"viewer_html": html, "promos": report.public_promos(ordered, eps), "strategy": strategy}


# ---------------------------------------------------------------- HTTP
# Vercel discovers Python functions by a literal top-level `class handler(BaseHTTPRequestHandler)` in each
# api/*.py file, so those files define the class themselves and delegate to these helpers.

def send_json(req: BaseHTTPRequestHandler, code: int, obj: dict) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req.send_response(code)
    req.send_header("Content-Type", "application/json; charset=utf-8")
    req.send_header("Cache-Control", "no-store")
    req.send_header("Content-Length", str(len(body)))
    req.end_headers()
    req.wfile.write(body)


def serve_get(req: BaseHTTPRequestHandler) -> None:
    send_json(req, 405, {"ok": False, "error": "POST only"})


def serve_post(req: BaseHTTPRequestHandler, phase) -> None:
    n = int(req.headers.get("content-length") or 0)
    if n <= 0 or n > MAX_BODY:
        return send_json(req, 413, {"ok": False, "error": "Upload is too large (max ~4 MB of scripts per show)."})
    try:
        payload = json.loads(req.rfile.read(n))
        return send_json(req, 200, {"ok": True, **phase(payload)})
    except ValueError as e:
        return send_json(req, 400, {"ok": False, "error": str(e)})
    except Exception as e:  # noqa: BLE001
        _log(f"{phase.__name__} failed: {type(e).__name__}: {e}")
        return send_json(req, 500, {"ok": False, "error": "Something went wrong while analysing the scripts."})


def make_handler(phase):
    """Handler class for local servers/tests (Vercel uses the literal classes in api/*.py)."""
    class handler(BaseHTTPRequestHandler):
        def do_GET(self):
            serve_get(self)

        def do_POST(self):
            serve_post(self, phase)
    return handler
