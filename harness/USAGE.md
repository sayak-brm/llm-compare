# Harness usage

Python 3.11, standard library only. The harness sends tasks to a local OpenAI-compatible server (llama.cpp `llama-server`) and scores the answers with deterministic checks; no model grades outputs. It never starts or stops a server.

**The tasks and hidden tests are not in this repository.** They are kept private so they cannot enter model training data and bias results. To use the harness, supply your own task files in `tasks/` (one JSON file per task: id, system and user messages, optional tools, check, token estimates; see `lib/tasks.py` and `lib/checks.py` for the fields each check kind reads).

## Run
Start the server yourself, then:

```powershell
python run.py --label candidate-a --model candidate-a --base-url http://127.0.0.1:8080/v1 --repeats 3 --parallel 1 --thinking off
python run.py --label candidate-a-quick --model candidate-a --profile quick --category T,F
python report.py results/<run-a> results/<run-b>
```

`-c` on the server is the context shared across slots; leave room for tool schemas and completions. Only loopback `--base-url` values are accepted.

## Results
`results/<label>-<timestamp>/` holds `requests.jsonl` (one row per trial, with TTFT, token usage, speed, finish reason), `summary.json`, `run.md` and `review/`. `report.py` compares runs: pass rate per category, median TTFT and generation speed, peak VRAM, tool-call validity, reasoning leaks, parallel slowdown. Readiness thresholds are in `thresholds.json`.

## Checks (`lib/`)
`checks.py` (facts and traps, schemas, exact commands, commit messages, headings and tables), `rca.py` (root-cause and plan checker), `coderun.py` (model-written code runs in an isolated subprocess: AST allowlist, `python -I -S`, empty environment, temp directory, time limit, Windows Job Object memory cap), `context.py` (token measurement through `/tokenize`), `client.py` (streaming client).

`python -I selftest_gr.py` exercises the code runner against adversarial programs when task files with gold answers are present.

## Rule
A check enforces a rule only if the task prompt states it. Breaches of stated rules cost score by severity; strict pass is kept too.
