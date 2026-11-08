"""CLI: python -m promo_finder "<show_dir>" --out out/ --mode auto|rules|hybrid --top 15"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .parse import load_show
from .candidates import all_candidates
from . import rank, quality, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="promo_finder", description="Find ranked promo clips in a microdrama screenplay.")
    ap.add_argument("show_dir", help="folder of episode .txt scripts")
    ap.add_argument("--out", default="out", help="output folder (default: out/)")
    ap.add_argument("--mode", choices=["auto", "rules", "hybrid"], default="auto",
                    help="rules = no LLM; hybrid = require Claude; auto = Claude if credentials are available")
    ap.add_argument("--top", type=int, default=15, help="number of promos to output")
    ap.add_argument("--per-episode", type=int, default=3, help="rule candidates kept per episode")
    ap.add_argument("--gold", help="optional editor-picked promos JSON for evaluation")
    ap.add_argument("--workers", type=int, default=6, help="parallel LLM calls")
    ap.add_argument("--episodes-per-call", type=int, default=None,
                    help="episodes judged per LLM call (default 1 for claude, 5 for gemini free tier)")
    ap.add_argument("--provider", choices=["claude", "gemini"], default="claude",
                    help="LLM for hybrid mode (gemini reads GEMINI_API_KEY from env or .env)")
    ap.add_argument("--model", default=None,
                    help="model id (default: claude-opus-5-5 / gemini-3.5-flash)")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    show_dir = Path(args.show_dir)
    show = show_dir.resolve().name
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    eps = load_show(show_dir)
    by_num = {e.num: e for e in eps}
    print(f"Parsed {len(eps)} episodes, {sum(len(e.cues) for e in eps)} cues.")

    cands = all_candidates(eps, args.per_episode)
    print(f"Rule candidates: {len(cands)}")

    mode = args.mode
    llm_log: list[str] = []
    strategy = ""
    if mode in ("auto", "hybrid"):
        from . import llm
        llm.load_dotenv()
        llm.configure(args.provider, args.model)
        if args.provider == "gemini" and args.workers > 2:
            args.workers = 2   # free tier: calls are rate-spaced anyway
        ok, why = llm.available()
        if not ok:
            if mode == "hybrid":
                print(f"--mode hybrid needs an LLM: {why}", file=sys.stderr)
                return 2
            print(f"LLM unavailable ({why}); running rules-only.")
            mode = "rules"
        else:
            mode = "hybrid"
            log = lambda m: (print(m), llm_log.append(m.strip()))
            print(f"LLM ({llm.MODEL}) pass 1/3: judging each episode…")
            per_call = args.episodes_per_call or (5 if args.provider == "gemini" else 1)
            judged = llm.judge_episodes(eps, cands, workers=args.workers, log=log, per_call=per_call)
            print(f"  {len(judged)} validated LLM promos")
            best_moments = sorted(judged, key=lambda p: -rank.final_score(p))[:20]
            print(f"LLM ({llm.MODEL}) pass 2/3: cross-episode stitching…")
            stitched = llm.stitch_pass(eps, best_moments, log=log)
            print(f"  {len(stitched)} validated stitched promos")
            # rule candidates stay in the pool, but LLM-validated ones carry blended scores
            cands = judged + stitched + cands

    chosen = rank.select(cands, top=args.top)
    if mode == "hybrid":
        from . import llm
        print(f"LLM ({llm.MODEL}) pass 3/3: posting order…")
        strategy = llm.apply_rank(chosen, llm.rank_pass(eps, chosen, log=lambda m: (print(m), llm_log.append(m))))
    ordered = rank.posting_order(chosen)
    for p in ordered:
        p.flags = quality.flag_promo(p, by_num)

    if mode == "hybrid":
        from . import llm
        cost = llm.usage_cost()
        llm_log.append(f"usage: {cost}")
        print(f"LLM usage: {cost}")
    meta = {"show": show, "mode": mode, "episodes": len(eps), "strategy": strategy,
            "diagnostics": quality.run_diagnostics(eps, cands, ordered), "llm_log": llm_log}
    if args.gold:
        meta["gold"] = quality.evaluate_gold(ordered, args.gold)

    report.write_json(out / "promos.json", ordered, meta)
    # every candidate considered, unfiltered, for auditing what the selector threw away
    for p in cands:
        p.scores.setdefault("final", rank.final_score(p))
    report.write_json(out / "all_candidates.json",
                      sorted(cands, key=lambda p: -p.scores["final"]), {"note": "all candidates before selection"})
    report.write_markdown(out / "report.md", show, ordered, by_num, meta)
    report.write_viewer(out / "viewer.html", show, ordered, by_num, meta)
    print(f"\nTop {len(ordered)} promos ({mode}):")
    for p in ordered:
        segs = " + ".join(f"E{s.episode} {s.to_dict()['start_ts']}-{s.to_dict()['end_ts']}" for s in p.segments)
        print(f" {p.rank:>2}. {segs:<40} {p.duration:5.1f}s  score {p.scores['final']:.2f}  {p.hook[:70]}")
    print(f"\nWrote {out/'promos.json'}, {out/'report.md'}, {out/'viewer.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
