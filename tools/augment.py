"""Join our benchmark results with the slopalytics / Artificial Analysis records.

Reads saved results and the local AA dataset; runs no model. Writes:
  data/augmented-results-<date>.json   LOCAL ONLY (git-ignored): our scores + AA fields
  docs/RESULTS.md                      publishable: our own scores and derived insights only

Run:  python -I tools/augment.py
Raw AA numbers never go in docs/RESULTS.md; only our scores and rounded, derived values.
"""
import json
import pathlib
import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
RES = ROOT / "harness" / "results"
AA = ROOT / "data" / "slopalytics-models-2026-11-20.json"

# model key -> (display name, AA record id, {category: run folder})
MODELS = {
    "qwen3.6-27b-q4km": ("Qwen3.6 27B Q4_K_M", "qwen3-6-27b-non-reasoning", {
        "S": "qwen36-27b-q4km-full-20261120-043309", "T": "qwen36-27b-q4km-full-20261120-043309",
        "F": "qwen36-27b-q4km-full-20261120-043309", "L": "qwen36-27b-q4km-full-20261120-043309",
        "C": "qwen36-27b-q4km-full-20261120-043309", "G": "qwen36-27b-q4km-GR-20261120-170101",
        "R": "qwen36-27b-q4km-Rv2-20261120-180357"}),
    "mistral3.2-24b-q4km": ("Mistral Small 3.2 24B Q4_K_M", "mistral-small-3-2", {
        "S": "mistral32-24b-q4km-full-20261120-053625", "F": "mistral32-24b-q4km-full-20261120-053625",
        "L": "mistral32-24b-q4km-full-20261120-053625", "T": "mistral32-24b-q4km-TC-rerun-20261120-060328",
        "C": "mistral32-24b-q4km-TC-rerun-20261120-060328", "G": "mistral32-24b-q4km-GR-20261120-152908",
        "R": "mistral32-24b-q4km-Rv2-20261120-184042"}),
}
CATS = ["S", "T", "F", "L", "C", "G", "R"]
NAMES = {"S": "Summaries", "T": "Tool calls", "F": "Format", "L": "Long context",
         "C": "Coordination", "G": "Coding goals", "R": "RCA and planning"}


def load(run):
    return [json.loads(l) for l in open(RES / run / "requests.jsonl", encoding="utf-8")]


def stats(rows, cat):
    rows = [r for r in rows if r["category"] == cat and not r.get("skipped_context")]
    if not rows:
        return None
    n = len(rows)
    ptok = sum(r.get("prompt_tokens") or 0 for r in rows)
    ctok = sum(r.get("completion_tokens") or 0 for r in rows)
    speeds = sorted(r["generated_tokens_s"] for r in rows if r.get("generated_tokens_s"))
    return {
        "trials": n,
        "strict_pass": round(sum(1 for r in rows if r.get("pass")) / n, 3),
        "mean_score": round(sum(r.get("score") or 0 for r in rows) / n, 3),
        "prompt_tokens_per_trial": round(ptok / n),
        "completion_tokens_per_trial": round(ctok / n),
        "median_gen_tok_s": round(speeds[len(speeds) // 2], 1) if speeds else None,
        "_ptok": ptok, "_ctok": ctok,
    }


def main():
    aa = {r["id"]: r for r in json.load(open(AA, encoding="utf-8"))}
    out = {"generated": datetime.date.today().isoformat(), "models": {}}
    for key, (disp, aid, runs) in MODELS.items():
        rec = aa.get(aid)
        m = {"name": disp, "aa_id": aid, "categories": {}, "runs": runs}
        cache = {}
        pt = ct = 0
        for cat in CATS:
            run = runs.get(cat)
            if not run:
                continue
            rows = cache.setdefault(run, load(run))
            s = stats(rows, cat)
            if s:
                pt += s.pop("_ptok")
                ct += s.pop("_ctok")
                m["categories"][cat] = s
        m["total_prompt_tokens"], m["total_completion_tokens"] = pt, ct
        if rec:
            m["aa"] = {k: rec.get(k) for k in ("name", "lab", "open", "released", "intelligence",
                                               "speed", "inputPrice", "outputPrice", "context")}
            if rec.get("inputPrice") is not None and rec.get("outputPrice") is not None:
                m["api_equivalent_usd_all_categories"] = round(
                    (pt * rec["inputPrice"] + ct * rec["outputPrice"]) / 1e6, 4)
            if rec.get("speed"):
                sp = [c["median_gen_tok_s"] for c in m["categories"].values() if c["median_gen_tok_s"]]
                if sp:
                    m["local_vs_hosted_speed"] = round(sorted(sp)[len(sp) // 2] / rec["speed"], 2)
        out["models"][key] = m
    p = ROOT / "data" / f"augmented-results-{out['generated']}.json"
    p.write_text(json.dumps(out, indent=1), encoding="utf-8")
    write_docs(out)
    print("wrote", p)


def pct(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def write_docs(out):
    ms = list(out["models"].values())
    L = ["---", "title: Results", "---", "", "# Results (our tasks only)", "",
         f"Generated {out['generated']} by `tools/augment.py` from saved runs. Strict pass is the share of trials that "
         "passed every check; the harness tasks themselves are not published. Scores are not comparable "
         "across prompt versions (see Methodology). Raw third-party model data is not published here.", "",
         "## Strict pass rate by category", "",
         "| Category | " + " | ".join(m["name"] for m in ms) + " |",
         "|---|" + "---|" * len(ms)]
    for c in CATS:
        L.append(f"| {NAMES[c]} ({c}) | " + " | ".join(pct(m["categories"].get(c, {}).get("strict_pass")) for m in ms) + " |")
    L += ["", "S, T, F, L, C come from the full non-voice run (Mistral T and C from the corrected rerun), G from "
          "the G/R run, R from the R v2 run (read-first rule stated in the prompt). Note: F and S were "
          "later rescored offline with fairer checks (Qwen F 100%, S 97%); the table shows the raw strict values "
          "recorded in the run files.", "",
          "## Tokens per trial and speed", "",
          "| Category | " + " | ".join(f"{m['name']} (prompt / completion tokens, median tok/s)" for m in ms) + " |",
          "|---|" + "---|" * len(ms)]
    for c in CATS:
        cells = []
        for m in ms:
            s = m["categories"].get(c)
            cells.append("n/a" if not s else f"{s['prompt_tokens_per_trial']} / {s['completion_tokens_per_trial']}, {s['median_gen_tok_s']}")
        L.append(f"| {c} | " + " | ".join(cells) + " |")
    L += ["", "## Augmented with external data (insights only)", ""]
    for m in ms:
        a = m.get("aa")
        bits = []
        if a:
            bits.append("an open-weights model with no Intelligence Index score in the external dataset"
                        if a.get("intelligence") is None else "has an external Intelligence Index score")
            if m.get("local_vs_hosted_speed"):
                bits.append(f"generates locally at about {m['local_vs_hosted_speed']:.1f}x the hosted speed")
            if m.get("api_equivalent_usd_all_categories") is not None:
                bits.append("the whole run would cost "
                            f"roughly ${m['api_equivalent_usd_all_categories']:.2f} at hosted per-token prices "
                            "(API-equivalent; local runs cost GPU time, not dollars)")
        L.append(f"- **{m['name']}**: " + "; ".join(bits) + "." if bits else f"- **{m['name']}**: no external record.")
    L += ["", "The external index cannot place these two models, so our own scores are the only quality measure for them. "
          "Dollar cost per run becomes the x axis of our own Pareto line once Claude and Codex runs exist.", ""]
    (ROOT / "docs" / "RESULTS.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
