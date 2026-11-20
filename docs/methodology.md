---
title: Methodology
---

# Methodology

## Categories (150 synthetic tasks)
S summaries (12), T tool calls (28), F output format (16), L long context up to about 47k real tokens (12), V voice, kept local (6), C multi-step coordination (26), G coding goals (25), R root-cause analysis and planning (25).

## Rules
1. Synthetic data only: no real names, hosts, keys or customer data.
2. **A check enforces a rule only if the prompt states it.** Breaches of stated rules cost score by severity; strict pass/fail is also kept. Scores from different prompt versions are not compared.
3. Tools are mocks: calls are recorded, never executed. Model-written code (G) runs only in an isolated, time-limited subprocess with no network and an empty environment.
4. The harness never starts or stops a model server. The operator does, and logs it.
5. Local runs use llama.cpp with a 65,536-token context, one slot, thinking off, three repeats per task.
6. For R, two numbers are reported beside strict pass: diagnosis (cause, red herring, injection) and plan shape (read first).

## Caveats
- External indexes (such as the Artificial Analysis Intelligence Index) are aggregates over other tasks. Here they give priors for cost, speed and placement; this benchmark gives the fit to our work. Raw external data is not republished.
- Open-weights models run here have no external index, so our scores are their only quality measure.
- Dollar cost per task is an API list price. Subscriptions bill by usage, not per task.
