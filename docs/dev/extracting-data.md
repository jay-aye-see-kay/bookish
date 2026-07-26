## Layout

```
bin/
  extract.sh   bin/extract.sh <dump.txt.gz>   -> data/openlibrary.duckdb
  clean.sh     bin/clean.sh                   -> data/books.duckdb
data/           all large files live here (source dump + both duckdb files)
```

## Usage

Put the dump in `data/`, then:

```bash
bin/extract.sh ol_dump_2026-06-30.txt.gz   # ~15 min  -> data/openlibrary.duckdb
bin/clean.sh                               # ~1 min   -> data/books.duckdb
```

Both scripts are idempotent (they `rm -f` their target first) and take no flags
or env vars. `extract.sh` takes one argument: the dump's file *name* (it must
live in `data/`).

## What each step produces

### `data/openlibrary.duckdb` (big, ~120 GB)

The dump is a gzipped 5-column TSV (`type · key · revision · timestamp · JSON`).
DuckDB reads it directly from the `.gz` — nothing is decompressed to disk. Three
typed tables, each keeping the full raw JSON so anything can be re-derived:

| table      | rows  | columns                          |
|------------|-------|----------------------------------|
| `authors`  | ~15 M | `key, name, json`                |
| `works`    | ~41 M | `key, title, json`               |
| `editions` | ~56 M | `key, title, work_key, author_key, json` |

### `data/books.duckdb` (tidy, ~250 MB)

Fiction works with 2+ editions, flattened, **no JSON columns**:

| table          | rows  | columns |
|----------------|-------|---------|
| `books`        | ~759 k | `work_key, ol_id, title, subtitle, first_publish_year, edition_count, description, subjects[], primary_author_key, cover_id, isbn13` |
| `authors`      | ~338 k | `author_key, name, birth_date, death_date, bio, alternate_names[]` |
| `book_authors` | ~890 k | `work_key, author_key, position` |

## Modelling notes

- **fiction** = the word "fiction" appears in `subjects` on the work *or* any of
  its editions (catches works with sparse metadata).
- **`edition_count`** is a popularity proxy — the number of editions of a work.
  It sorts sensibly (classics rise to the top) but skews toward much-reprinted
  public-domain titles; modern books have fewer editions.
- **`first_publish_year`** comes from the work's own field, falling back to the
  earliest edition's publish year — ~100% coverage.
- **`description`** (the main embedding text) is present on ~30% of books; OL
  stores it as `{type, value}` or a plain string — both are unwrapped, with a
  fallback to an edition description.

### Filtering down for embeddings

`books.duckdb` keeps all ~759 k fiction works so you can slice dynamically, e.g.

```sql
-- popular fiction with text to embed
SELECT * FROM books
WHERE description IS NOT NULL AND edition_count >= 5;   -- ~75 k
```

| min editions | + has description |
|--------------|-------------------|
| >= 2         | ~219 k            |
| >= 3         | ~147 k            |
| >= 5         | ~75 k             |
| >= 10        | ~28 k             |

