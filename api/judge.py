"""POST /api/judge: one phase of the promo pipeline (see promo_finder/web.py)."""
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from promo_finder import web  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        web.serve_post(self, web.judge)

    def do_GET(self):
        web.serve_get(self)
