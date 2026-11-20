---
title: Results
---

# Results (our tasks only)

Generated 2026-11-20 by `tools/augment.py` from saved runs. Strict pass is the share of trials that passed every check; the harness tasks themselves are not published. Scores are not comparable across prompt versions (see Methodology). Raw third-party model data is not published here.

## Strict pass rate by category

| Category | Qwen3.6 27B Q4_K_M | Mistral Small 3.2 24B Q4_K_M |
|---|---|---|
| Summaries (S) | 83.3% | 61.1% |
| Tool calls (T) | 100.0% | 63.1% |
| Format (F) | 93.8% | 45.8% |
| Long context (L) | 100.0% | 83.3% |
| Coordination (C) | 6.4% | 2.6% |
| Coding goals (G) | 84.0% | 34.7% |
| RCA and planning (R) | 50.7% | 28.0% |

S, T, F, L, C come from the full non-voice run (Mistral T and C from the corrected rerun), G from the G/R run, R from the R v2 run (read-first rule stated in the prompt). Note: F and S were later rescored offline with fairer checks (Qwen F 100%, S 97%); the table shows the raw strict values recorded in the run files.

## Tokens per trial and speed

| Category | Qwen3.6 27B Q4_K_M (prompt / completion tokens, median tok/s) | Mistral Small 3.2 24B Q4_K_M (prompt / completion tokens, median tok/s) |
|---|---|---|
| S | 21864 / 126, 29.2 | 22130 / 116, 34.8 |
| T | 771 / 59, 35.7 | 333 / 201, 47.6 |
| F | 71 / 64, 35.0 | 61 / 109, 47.6 |
| L | 47226 / 8, 28.6 | 47220 / 18, 29.9 |
| C | 3005 / 112, 34.6 | 2114 / 225, 48.3 |
| G | 368 / 745, 36.4 | 361 / 346, 50.9 |
| R | 542 / 798, 36.2 | 532 / 467, 49.5 |

## Augmented with external data (insights only)

- **Qwen3.6 27B Q4_K_M**: an open-weights model with no Intelligence Index score in the external dataset; generates locally at about 0.8x the hosted speed; the whole run would cost roughly $2.21 at hosted per-token prices (API-equivalent; local runs cost GPU time, not dollars).
- **Mistral Small 3.2 24B Q4_K_M**: an open-weights model with no Intelligence Index score in the external dataset; generates locally at about 1.0x the hosted speed; the whole run would cost roughly $0.23 at hosted per-token prices (API-equivalent; local runs cost GPU time, not dollars).

The external index cannot place these two models, so our own scores are the only quality measure for them. Dollar cost per run becomes the x axis of our own Pareto line once Claude and Codex runs exist.
