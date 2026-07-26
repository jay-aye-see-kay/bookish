# bookish

Find similar books using embeddings and other db filters.

## Running locally

- all deps from flake.nix+direnv
  - except llama.cpp comes from home brew
- data is the ["all types dump" from Open Library](https://openlibrary.org/developers/dumps)
- embeddings using (Qwen/Qwen3-Embedding-8B-GGUF:Q8_0)[https://huggingface.co/Qwen/Qwen3-Embedding-8B-GGUF]
  - downloaded with llama cli like `llama-embedding -hf Qwen/Qwen3-Embedding-8B-GGUF:Q8_0`

## Docs

There are currently only development docs (nothing user facing) in [docs/dev/](./docs/dev/). These are referenced from [AGENTS.md](./AGENTS.md)

## Ideas / next steps

* Create a CLI or bunch of scripts to test it out
- Create a webUI (go json api + react SPA)
- Join Open Library's separate **ratings** / **reading-log** dumps for real reader popularity beyond reprint count.
- Add `language` / `genres` columns pulled from editions.
