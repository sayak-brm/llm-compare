# llm-compare

A benchmark for choosing which model should do which kind of agent work. It scores local models (llama.cpp) first, and Claude and Codex models later, on 150 synthetic tasks in eight categories: summaries, tool calls, output format, long context, voice, multi-step coordination, coding goals, and root-cause analysis with plan writing.

**The tasks and hidden tests are deliberately not in this repository.** Keeping them private stops them entering model training data and biasing results. This repo holds the harness that runs and scores them, the offline scorers, and the results.

- Results and method: [`docs/`](docs/index.md) (set up for GitHub Pages: Settings, Pages, branch `main`, folder `/docs`).
- Harness guide: [`harness/USAGE.md`](harness/USAGE.md).

## Layout
- `harness/`: runner (`run.py`), report (`report.py`), checks (`lib/`: facts and traps, schemas, commit messages, RCA and plan checker, sandboxed code runner), thresholds.
- `tools/`: offline scorers and measurement. They read saved results and run no model: `gr_weighted_score.py` (severity-weighted G and R), `c_rescore.py`, `toks_per_sec.py`, `pareto.py` (cost per intelligence frontier), `augment.py` (joins results with external model data and writes `docs/RESULTS.md`).
- `docs/`: the published site (index, methodology, results).

## Rules
1. Synthetic data only: no real names, hosts, keys or customer data.
2. A check enforces a rule only if the prompt states it. Breaches of stated rules cost score by severity; strict pass is kept too. Scores from different prompt versions are not compared.
3. Model-written code runs only in an isolated, time-limited subprocess (AST allowlist, empty environment, temporary directory, memory cap).
4. The harness never starts or stops a model server.
5. External model data (such as the Artificial Analysis Intelligence Index) is used locally for priors on cost and speed; raw third-party data is not republished.

## Quick start
You need your own task files in `harness/tasks/` (see `harness/USAGE.md`).

```powershell
python -I harness/run.py --label candidate-a --model candidate-a --base-url http://127.0.0.1:8080/v1 --repeats 3 --parallel 1 --thinking off
python -I harness/report.py harness/results/<run-a> harness/results/<run-b>
python -I tools/gr_weighted_score.py harness/results/<run>
```
