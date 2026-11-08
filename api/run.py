"""Vercel Python serverless function: POST /api/run

Body (JSON): {"show": "<name>", "top": 15, "files": [{"name": "episode-001.txt", "text": "..."}]}
Returns (JSON): {"ok": true, "viewer_html": "...", "promos": {...}, "log": "..."}

Runs the rule engine only. The LLM pass takes minutes and needs an API key with a daily quota,
which a public endpoint must not expose. LLM results are pre-computed under /raw_outputs/hybrid_gemini/.
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from promo_finder.__main__ import main  # noqa: E402

MAX_BODY = 4_000_000      # Vercel's request body limit is 4.5 MB
MAX_FILES = 300


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, {"ok": True, "usage": "POST JSON {show, top, files:[{name,text}]}"})

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._send(413, {"ok": False, "error": f"body must be 1..{MAX_BODY} bytes"})
        try:
            payload = json.loads(self.rfile.read(n))
            files = payload.get("files") or []
            if not isinstance(files, list) or not files or len(files) > MAX_FILES:
                return self._send(400, {"ok": False, "error": f"send 1..{MAX_FILES} episode .txt files"})
            top = max(1, min(30, int(payload.get("top") or 15)))
            show = re.sub(r"[^\w .,'()-]", "", str(payload.get("show") or "Uploaded show"))[:80].strip() or "show"
        except (ValueError, TypeError, AttributeError) as e:
            return self._send(400, {"ok": False, "error": f"bad request: {e}"})

        with tempfile.TemporaryDirectory() as td:
            show_dir = Path(td) / show
            show_dir.mkdir()
            written = 0
            for f in files:
                name = os.path.basename(str(f.get("name", "")))
                if not name.lower().endswith(".txt"):
                    continue
                (show_dir / name).write_text(str(f.get("text", "")), encoding="utf-8")
                written += 1
            if not written:
                return self._send(400, {"ok": False, "error": "no .txt files in upload"})
            out = Path(td) / "out"
            log = io.StringIO()
            try:
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    rc = main([str(show_dir), "--out", str(out), "--mode", "rules", "--top", str(top)])
            except Exception as e:  # noqa: BLE001  (malformed scripts etc.)
                return self._send(422, {"ok": False, "error": f"{type(e).__name__}: {e}", "log": log.getvalue()[-4000:]})
            if rc != 0 or not (out / "viewer.html").exists():
                return self._send(422, {"ok": False, "error": "run failed", "log": log.getvalue()[-4000:]})
            return self._send(200, {
                "ok": True,
                "viewer_html": (out / "viewer.html").read_text(encoding="utf-8"),
                "promos": json.loads((out / "promos.json").read_text(encoding="utf-8")),
                "log": log.getvalue()[-4000:],
            })
