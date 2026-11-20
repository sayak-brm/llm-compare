---
title: llm-compare
---

# llm-compare

A benchmark for choosing which model should do which kind of agent work: summaries, tool calls, output format, long context, multi-step coordination, small coding goals and root-cause analysis with plan writing. It compares local models (llama.cpp) first, and Claude and Codex models later.

The task prompts and hidden tests are **not published**, so models and prompts can't be tuned to them.

- [Results](RESULTS.md): strict pass rates, token use and speed per category.
- [Methodology](methodology.md): categories, scoring rules and caveats.

## Latest (20 Nov 2026)

On our tasks Qwen3.6 27B leads Mistral Small 3.2 24B in every category. Both are weak at multi-step coordination (C): the best strict pass rate is 6.4%, and even counting only whether the right actions were taken it stays below 40%. A check only counts a rule the prompt states, so the RCA prompt now says to start with a read-only step. Runs before and after that change are not compared with each other.
