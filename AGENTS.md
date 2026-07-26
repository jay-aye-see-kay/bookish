## Running locally

- all deps from flake.nix+direnv
  - except llama.cpp comes from home brew
- data is the ["all types dump" from Open Library](https://openlibrary.org/developers/dumps)
- embeddings using (Qwen/Qwen3-Embedding-8B-GGUF:Q8_0)[https://huggingface.co/Qwen/Qwen3-Embedding-8B-GGUF]
  - downloaded with llama cli like `llama-embedding -hf Qwen/Qwen3-Embedding-8B-GGUF:Q8_0`

## Docs

- [Where data comes from](./docs/dev/extracting-data.md)

## Keep docs up to date

- keep this file (AGENTS.md) and all referenced files up to date by modifying when a mistake is found or the repo is changes/added to
