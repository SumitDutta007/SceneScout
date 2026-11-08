"""POST /api/stitch: one phase of the promo pipeline (see promo_finder/web.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from promo_finder import web  # noqa: E402

handler = web.make_handler(web.stitch)
