# bookish

Find similar books using embeddings and other db filters.

Each book (from [Open Library](https://openlibrary.org/developers/dumps)) is
embedded with a local LLM, then compared by cosine similarity in DuckDB. Give it
a few books you like and it recommends more — either from the SQL exploration
tools or the little web app.

## What's here

- **Data pipeline** — extract & clean the Open Library dump into DuckDB, then
  embed ~27.5k books with `Qwen/Qwen3-Embedding-8B` (see `bin/`).
- **Exploration tools** — read-only DuckDB macros for finding neighbours,
  comparing books, and recommending from a preferences file (`sql/similar.sql`).
- **Web app** — a Go JSON API over embedded DuckDB (`cmd/serve`, `internal/`)
  plus a React + TS + Vite frontend (`web/`) to rate books and get
  recommendations.

## Running locally

- all deps from flake.nix+direnv
  - except llama.cpp comes from home brew
- data is the ["all types dump" from Open Library](https://openlibrary.org/developers/dumps)
- embeddings using [Qwen/Qwen3-Embedding-8B-GGUF:Q8_0](https://huggingface.co/Qwen/Qwen3-Embedding-8B-GGUF)
  - downloaded with llama cli like `llama-embedding -hf Qwen/Qwen3-Embedding-8B-GGUF:Q8_0`

Then, roughly: build the DuckDB from the dump (`bin/extract.sh`, `bin/clean.sh`),
embed the corpus (`bin/embed.py`), and run the backend (`bin/serve.sh`) + frontend
(`cd web && npm run dev`). Full step-by-step is in the docs below.

## Docs

Development docs (nothing user facing yet) live in [docs/dev/](./docs/dev/) and
are referenced from [AGENTS.md](./AGENTS.md) — start there for the technical
detail on data extraction, embeddings, the comparison tooling, and the web
backend/frontend.

## Ideas / next steps

- Join Open Library's separate **ratings** / **reading-log** dumps for real
  reader popularity beyond reprint count.
- Add `language` / `genres` columns pulled from editions.
- Clustered recommendations (see [ideas/clustered-recommendations.md](./ideas/clustered-recommendations.md)).
