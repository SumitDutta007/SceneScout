# promo_finder

Reads a microdrama screenplay (one timestamped `.txt` per episode) and returns ranked promo clips. Each promo is 20–60s long, made of one or more segments, opens on a hook in the first 3 seconds, ends on a cliffhanger and should make sense to a viewer who has never seen the show. For every promo you get the episode, start and end per segment, the hook, the cliffhanger, a one-line reason, a posting order and quality flags.

**No time to run it?** The unedited outputs of two full runs on all 50 episodes are committed. Open `viewer.html` in either folder in a browser.
- [`raw_outputs/hybrid_gemini/`](raw_outputs/hybrid_gemini/): rules + Gemini 3 Flash (12 calls, 2.5 min, free tier)
- [`raw_outputs/rules_only/`](raw_outputs/rules_only/): rules only (4s, no LLM)
- [`raw_outputs/eval_vs_my_picks.json`](raw_outputs/eval_vs_my_picks.json): both runs scored against 12 moments picked in advance. Results are in [ANALYSIS.md](ANALYSIS.md) §4.

## Run it

Needs Python 3.10+. The rules mode has no dependencies.

1. Put the input pack in `data/`, one folder per show, containing the episode `.txt` files. For example: `data/I Married a Broken Soldier. He was a Duke/episode-001-....txt`. The scripts are not in this repo.
2. Run:

```bash
python -m promo_finder "data/I Married a Broken Soldier. He was a Duke" --out out
#   --mode rules|hybrid|auto   (auto = use Claude if credentials exist, else rules only)
#   --provider claude|gemini   LLM for hybrid mode (default claude)
#   --model <id>               default claude-opus-5-5 / gemini-3.5-flash; token usage is printed
#   --top 15                   number of promos
#   --gold editor_picks.json   score against an editor's picks (precision@k, temporal IoU)
pip install pytest && python -m pytest tests -q      # 9 tests
```

Output goes to `out/` (`all_candidates.json` and the console log are included in the committed run):
- `promos.json`: machine-readable list
- `report.md`: ranked table, top-5 posting rationale, per-promo script excerpts, auto quality check
- `viewer.html`: open in a browser to filter and sort promos and read each one's script with the hook and cliffhanger windows highlighted

## How it works

| Stage | File | What it does |
|---|---|---|
| Parse | `parse.py` | Header, scenes and cues (CAMERA / ACTION / SFX / dialogue with emotion tag) become seconds. Speaker aliases are normalised (e.g. `THE FORMER GROOM` → `GERALD`). Missing scene ends are repaired (ep 6). |
| Cut points | `candidates.py` | A segment may only start at a shot start and end at a shot end, and never in the middle of a line. |
| Signals | `signals.py` | **Hook** = what starts in the first 3s (violence/shock verbs, menace props, charged short lines, hot delivery tags; penalty for establishing shots). **Cliffhanger** = the last 4–6s (written music stings, cut to black, episode end, threat or question line, interruption by a shock event; penalty if the story comes to rest). **Standalone** = penalties for leading pronouns, back-references, too many speakers, late episodes. **Premise** = lines that state the setup (altar, married, beggar, Duke…). |
| Candidates | `candidates.py` | Single stretches: every strong hook start × every legal end 20–60s later, then per-episode NMS. Stitched: short hook piece + cliffhanger piece, in story order, sharing an on-screen main character. |
| Claude (optional) | `llm.py` | Uses `claude-opus-5-5` with structured JSON output. (1) A per-episode judge reads the full transcript plus the rule candidates and proposes or refines up to 3 promos. (2) A cross-episode stitch pass. (3) A posting-order pass. Claude answers with **cue IDs, never timestamps**, and each one is mapped back to the script and snapped to a legal cut. Invalid items are dropped and logged, the series bible is prompt-cached, and responses are disk-cached in `.cache/`. |
| Rank | `rank.py` | Blends Claude and rule scores (0.6 / 0.4) when Claude ran. Diversity rules: ≤2 promos per episode, no overlapping or same-opening promos, stitched ≤ ⅓ of the list. Posting order = score + early-episode entry bonus + premise bonus − late-season spoiler cost, then reordered so consecutive posts use different hook types. |
| Quality | `quality.py` | Per-promo flags (visual-only hook, weak cliffhanger, off-screen names, location jump, newcomer in a stitch, cut inside a music sting, late-season spoiler, rules vs Claude disagreement). Run diagnostics. `--gold` evaluation. |

**LLM providers.** The AI layer is one function (`llm._call`), so it is provider-agnostic.
- **Gemini (free tier, used for the committed hybrid run):** put `GEMINI_API_KEY=...` in a `.env` file in the project root (git-ignored), then run `--mode hybrid --provider gemini`. It uses the standard library only, with no extra install. Calls are spaced about 6.5s apart for the free tier's rate limit, and 429s are retried. Note that Google may use free-tier inputs to improve its products; production should use a paid tier.
- **Claude:** `pip install -r requirements.txt`, set `ANTHROPIC_API_KEY` (or `ant auth login`), and use `--mode hybrid`. It makes about 52 calls per show. The cost is not measured yet; the estimate is a few dollars per 50-episode show.

See **ANALYSIS.md** for the results on *I Married a Broken Soldier. He was a Duke*, the posting order, and where the tool picks badly.
