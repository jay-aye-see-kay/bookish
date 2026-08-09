# Experiments

One-off investigations, spikes, and evals live under `experiments/`. Each
experiment is a self-contained directory so the messy history is preserved
without polluting the main docs or source tree.

## Directory convention

```
experiments/
  AGENTS.md                       # this file
  001-<short-slug>/
    goal.md                       # what we set out to learn or decide
    iteration-01-<short-slug>.md  # raw results from the first run
    iteration-02-<short-slug>.md  # further iterations as needed
    outcome.md                    # final conclusions and decisions
    scripts/                      # code used to run the experiment
    data/                         # optional: generated artifacts, caches, logs
```

- Use three-digit sequential prefixes so experiments stay ordered.
- Keep the slug short and descriptive.
- `goal.md` and `outcome.md` are required. Iteration files are added as needed.
- `data/` is optional and should usually be gitignored. It holds Parquet files,
  query vectors, logs, plots, or anything else produced by `scripts/`.
- Do not leave experiment files in `docs/dev/`, `bin/`, or elsewhere in the
  repo. The root `AGENTS.md` only points here.

## Current experiments

- `001-embedding-arithmetics/` — testing whether vector arithmetic can strip
  domain markers from non-book embeddings. See `outcome.md` for conclusions.
