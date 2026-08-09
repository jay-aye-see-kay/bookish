#!/usr/bin/env python3
"""Run the embedding-arithmetic experiment and write a Markdown report.

Depends on the embedding server running at localhost:8080 (start outside the
sandbox with bin/serve-embed.sh).
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import requests

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "embeddings"
BOOKS_DB = ROOT / "data" / "books.duckdb"
URL = os.environ.get("EMBED_URL", "http://localhost:8080/v1/embeddings")
HEALTH = URL.rsplit("/v1/", 1)[0] + "/health"
DIM = 4096
OUT_DIR = ROOT / "experiments" / "001-embedding-arithmetics" / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_PATH = ROOT / "experiments" / "001-embedding-arithmetics" / "iteration-01-initial-subtraction.md"


def check_server():
    r = requests.get(HEALTH, timeout=10)
    r.raise_for_status()
    assert r.json().get("status") == "ok", r.text
    print(f"embedding server healthy: {HEALTH}")


def embed(texts):
    """Embed a list of strings; return L2-normalized float32 vectors."""
    if isinstance(texts, str):
        texts = [texts]
    r = requests.post(URL, json={"input": texts, "model": "q"}, timeout=300)
    r.raise_for_status()
    data = r.json()["data"]
    data.sort(key=lambda d: d["index"])
    vecs = np.array([d["embedding"] for d in data], dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def cosim(a, b):
    """Cosine similarity between two vectors (assumed normalized)."""
    return float(np.dot(a, b))


def write_query_parquet(label, vec, path):
    vec = np.asarray(vec, dtype=np.float32)
    table = pa.table({
        "label": [label],
        "vec": pa.array([vec.tolist()], type=pa.list_(pa.float32(), DIM)),
    })
    pq.write_table(table, path)
    return path


def top_books_for_vec(vec, label, n=10):
    """Return top-n book neighbours for a single query vector."""
    query_path = OUT_DIR / f"query_{safe(label)}.parquet"
    write_query_parquet(label, vec, query_path)
    con = duckdb.connect(BOOKS_DB, read_only=True)
    con.execute(f"ATTACH IF NOT EXISTS '{BOOKS_DB}' AS books (READ_ONLY);")
    rows = con.execute(f"""
        CREATE OR REPLACE TEMP VIEW book_meta AS
        SELECT 'Book|' || a.name || '|' || b.title AS input_text,
               a.name AS author, b.title,
               b.first_publish_year AS year, b.edition_count AS editions
        FROM books.books b
        JOIN books.authors a ON a.author_key = b.primary_author_key
        QUALIFY row_number() OVER (
          PARTITION BY 'Book|' || a.name || '|' || b.title
          ORDER BY b.edition_count DESC, b.first_publish_year) = 1;

        SELECT author, title, year, editions,
               round(list_cosine_similarity(e.vec, q.vec), 4) AS sim
        FROM book_meta m
        JOIN read_parquet('{CACHE_DIR}/*.parquet') e ON e.input_text = m.input_text
        CROSS JOIN read_parquet('{query_path}') q
        ORDER BY sim DESC
        LIMIT {n}
    """).fetchall()
    con.close()
    return rows


def _sql_like(s):
    return s.replace("'", "''")


def find_book_vec(author, title):
    """Return the cached vector for the best matching Book|author|title."""
    con = duckdb.connect(BOOKS_DB, read_only=True)
    con.execute(f"ATTACH IF NOT EXISTS '{BOOKS_DB}' AS books (READ_ONLY);")
    rows = con.execute(f"""
        SELECT e.vec
        FROM read_parquet('{CACHE_DIR}/*.parquet') e
        JOIN (
            SELECT 'Book|' || a.name || '|' || b.title AS input_text
            FROM books.books b
            JOIN books.authors a ON a.author_key = b.primary_author_key
            WHERE b.title ILIKE '%{_sql_like(title)}%' AND a.name ILIKE '%{_sql_like(author)}%'
            ORDER BY b.edition_count DESC, b.first_publish_year
            LIMIT 1
        ) m ON m.input_text = e.input_text
    """).fetchall()
    con.close()
    if not rows:
        return None
    return np.array(rows[0][0], dtype=np.float32)


def safe(s):
    return hashlib.sha256(s.encode()).hexdigest()[:12]


def print_matrix(labels, vectors, title):
    lines = [f"\n### {title}", "", "|       | " + " | ".join(labels) + " |"]
    lines.append("|" + "---|" * (len(labels) + 1))
    for i, ri in enumerate(labels):
        row = [ri]
        for j, _ in enumerate(labels):
            row.append(f"{cosim(vectors[i], vectors[j]):.4f}")
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def main():
    check_server()
    report = []
    report.append("# Embedding Arithmetic Experiment: Results\n")
    report.append(f"*Generated: {datetime.now(timezone.utc).isoformat()}Z*\n")

    # ------------------------------------------------------------------
    # Embed all test strings
    # ------------------------------------------------------------------
    classic_strings = [
        "king", "man", "woman", "queen",
        "france", "paris", "italy", "rome",
        "dog", "puppy", "cat", "kitten",
        "walk", "walked", "run", "ran",
    ]

    hackers_strings = [
        "Hackers",
        "Hackers (film)",
        "The 1995 film Hackers",
        "Movie|Hackers",
        "Movie|1995|Hackers",
        "Movie",
        "1995",
        "Pineapple",
        "Tuesday",
    ]

    cross_media_strings = [
        "Blade Runner",
        "Movie|Blade Runner",
        "Movie|1982|Blade Runner",
        "The Witcher",
        "Game|The Witcher",
        "Dune",
        "Movie|Dune",
        "Movie|2021|Dune",
        "Halo",
        "Game|Halo",
        "The Lord of the Rings",
        "Movie|The Lord of the Rings",
    ]

    marker_strings = [
        "Movie", "Book", "Game", "Director", "Genre", "Theme", "Event",
        "1995", "1982", "2021",
    ]

    all_strings = list(dict.fromkeys(
        classic_strings + hackers_strings + cross_media_strings + marker_strings
    ))

    print(f"embedding {len(all_strings)} strings...")
    all_vectors = embed(all_strings)
    vec_by_text = {t: all_vectors[i] for i, t in enumerate(all_strings)}

    # ------------------------------------------------------------------
    # Experiment 1: Classic analogies
    # ------------------------------------------------------------------
    report.append("## 1. Classic analogies (positive control)\n")

    analogy_tasks = [
        (["king", "man", "woman", "queen"], "king - man + woman", "queen"),
        (["france", "paris", "italy", "rome"], "france - paris + rome", "italy"),
        (["dog", "puppy", "cat", "kitten"], "dog - puppy + kitten", "cat"),
        (["walk", "walked", "run", "ran"], "walk - walked + ran", "run"),
    ]

    for terms, expr, expected in analogy_tasks:
        tvec = {t: vec_by_text[t] for t in terms}
        computed = tvec["king"] - tvec["man"] + tvec["woman"] if "king" in tvec else None
        if computed is None:
            # generic parse
            parts = expr.replace(" - ", " ").replace(" + ", " ").split()
            computed = vec_by_text[parts[0]]
            for i in range(1, len(parts)):
                sign = -1 if i % 2 == 1 and expr.find(" - " + parts[i]) > -1 else 1
                computed = computed + sign * vec_by_text[parts[i]]
        computed = computed / np.linalg.norm(computed)

        ranked = sorted(terms, key=lambda t: cosim(computed, tvec[t]), reverse=True)
        report.append(f"### Expression: `{expr}`  (expected: `{expected}`)\n")
        report.append("| rank | term | cosine |")
        report.append("|---:|---|---:|")
        for rank, t in enumerate(ranked, 1):
            marker = " ✅" if t == expected else ""
            report.append(f"| {rank} | {t} | {cosim(computed, tvec[t]):.4f}{marker} |")
        report.append("")

    # ------------------------------------------------------------------
    # Experiment 2: Cross-modal subtraction (Hackers)
    # ------------------------------------------------------------------
    report.append("## 2. Cross-modal subtraction: Hackers\n")

    purified = vec_by_text["Movie|1995|Hackers"] - vec_by_text["Movie"] - vec_by_text["1995"]
    purified = purified / np.linalg.norm(purified)

    query_labels = {
        "raw": "Hackers",
        "film_paren": "Hackers (film)",
        "film_sentence": "The 1995 film Hackers",
        "type_only": "Movie|Hackers",
        "type_year": "Movie|1995|Hackers",
        "purified": "(purified)",
    }
    query_vecs = {
        "raw": vec_by_text["Hackers"],
        "film_paren": vec_by_text["Hackers (film)"],
        "film_sentence": vec_by_text["The 1995 film Hackers"],
        "type_only": vec_by_text["Movie|Hackers"],
        "type_year": vec_by_text["Movie|1995|Hackers"],
        "purified": purified,
    }

    # Save the purified vector for inspection
    pq.write_table(pa.table({
        "label": ["purified_hackers"],
        "vec": pa.array([purified.tolist()], type=pa.list_(pa.float32(), DIM)),
    }), OUT_DIR / "purified_hackers.parquet")

    # Nearest books for each query
    neighbors = {}
    for key, label in query_labels.items():
        rows = top_books_for_vec(query_vecs[key], f"hackers_{key}", n=10)
        neighbors[key] = rows

    for key, label in query_labels.items():
        report.append(f"### {label}\n")
        report.append("| rank | author | title | year | editions | cosine |")
        report.append("|---:|---|---|---|---:|---:|")
        for rank, (author, title, year, editions, sim) in enumerate(neighbors[key], 1):
            report.append(f"| {rank} | {author} | {title} | {year} | {editions} | {sim:.4f} |")
        report.append("")

    # Overlap table
    report.append("### Top-5 overlap between input formats\n")
    keys = list(query_labels.keys())
    report.append("|       | " + " | ".join(keys) + " |")
    report.append("|" + "---|" * (len(keys) + 1))
    for k1 in keys:
        row = [k1]
        set1 = {(r[0], r[1]) for r in neighbors[k1][:5]}
        for k2 in keys:
            set2 = {(r[0], r[1]) for r in neighbors[k2][:5]}
            row.append(str(len(set1 & set2)))
        report.append("| " + " | ".join(row) + " |")
    report.append("")

    # Cosine to expected cyberpunk books
    report.append("### Cosine similarity to expected thematic neighbours\n")
    targets = [
        ("William Gibson", "Neuromancer"),
        ("Neal Stephenson", "Snow Crash"),
        ("William Gibson", "Count Zero"),
    ]
    target_vecs = {}
    for author, title in targets:
        v = find_book_vec(author, title)
        target_vecs[(author, title)] = v

    report.append("| query | " + " | ".join(f"{t[1]}" for t in targets) + " |")
    report.append("|" + "---|" * (len(targets) + 1))
    for key, label in query_labels.items():
        row = [label]
        for t in targets:
            v = target_vecs[t]
            if v is None:
                row.append("N/A")
            else:
                row.append(f"{cosim(query_vecs[key], v):.4f}")
        report.append("| " + " | ".join(row) + " |")
    report.append("")

    # ------------------------------------------------------------------
    # Experiment 3: Multiple cross-media examples
    # ------------------------------------------------------------------
    report.append("## 3. Multiple cross-media examples\n")

    franchises = [
        {
            "name": "Blade Runner",
            "queries": [
                ("raw", "Blade Runner"),
                ("type", "Movie|Blade Runner"),
                ("type_year", "Movie|1982|Blade Runner"),
                ("purified", None),  # computed below
            ],
            "known_book": ("Philip K. Dick", "Do Androids Dream of Electric Sheep?"),
        },
        {
            "name": "The Witcher",
            "queries": [
                ("raw", "The Witcher"),
                ("type", "Game|The Witcher"),
            ],
            "known_book": ("Andrzej Sapkowski", "The Witcher"),
        },
        {
            "name": "Dune",
            "queries": [
                ("raw", "Dune"),
                ("type", "Movie|Dune"),
                ("type_year", "Movie|2021|Dune"),
                ("purified", None),
            ],
            "known_book": ("Frank Herbert", "Dune"),
        },
        {
            "name": "Halo",
            "queries": [
                ("raw", "Halo"),
                ("type", "Game|Halo"),
            ],
            "known_book": ("William C. Dietz", "Halo: The Flood"),
        },
        {
            "name": "The Lord of the Rings",
            "queries": [
                ("raw", "The Lord of the Rings"),
                ("type", "Movie|The Lord of the Rings"),
            ],
            "known_book": ("J.R.R. Tolkien", "The Lord of the Rings"),
        },
    ]

    for fr in franchises:
        report.append(f"### {fr['name']}\n")
        queries = []
        for key, text in fr["queries"]:
            if key == "purified":
                # Find the type+year query in the franchise list
                type_year_text = next((t for k, t in fr["queries"] if k == "type_year" and t), None)
                type_text = next((t for k, t in fr["queries"] if k == "type" and t), None)
                if type_year_text:
                    parts = type_year_text.split("|")
                    if len(parts) == 3:
                        type_marker, year_marker, _ = parts
                        if type_marker in vec_by_text and year_marker in vec_by_text:
                            v = vec_by_text[type_year_text] - vec_by_text[type_marker] - vec_by_text[year_marker]
                            v = v / np.linalg.norm(v)
                            queries.append(("purified", "(purified)", v))
                elif type_text:
                    type_marker = type_text.split("|")[0]
                    if type_marker in vec_by_text:
                        v = vec_by_text[type_text] - vec_by_text[type_marker]
                        v = v / np.linalg.norm(v)
                        queries.append(("purified", "(purified)", v))
            else:
                queries.append((key, text, vec_by_text[text]))

        known_book_vec = None
        if fr["known_book"]:
            known_book_vec = find_book_vec(*fr["known_book"])

        for key, label, vec in queries:
            report.append(f"#### {label}\n")
            rows = top_books_for_vec(vec, f"{fr['name']}_{key}", n=5)
            report.append("| rank | author | title | year | editions | cosine |")
            report.append("|---:|---|---|---|---:|---:|")
            for rank, (author, title, year, editions, sim) in enumerate(rows, 1):
                report.append(f"| {rank} | {author} | {title} | {year} | {editions} | {sim:.4f} |")
            if known_book_vec is not None:
                report.append(f"\n*Cosine to known book `{fr['known_book'][1]}`: {cosim(vec, known_book_vec):.4f}*\n")
            report.append("")

    # ------------------------------------------------------------------
    # Experiment 4: Negative controls
    # ------------------------------------------------------------------
    report.append("## 4. Negative controls\n")

    neg = vec_by_text["Movie|1995|Hackers"] - vec_by_text["Pineapple"] - vec_by_text["Tuesday"]
    neg = neg / np.linalg.norm(neg)
    neg_rows = top_books_for_vec(neg, "neg_pineapple_tuesday", n=10)
    report.append("### `Movie|1995|Hackers - Pineapple - Tuesday`\n")
    report.append("| rank | author | title | year | editions | cosine |")
    report.append("|---:|---|---|---|---:|---:|")
    for rank, row in enumerate(neg_rows, 1):
        report.append(f"| {rank} | {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]:.4f} |")
    report.append("")

    # Random unit vector control
    rng = np.random.default_rng(42)
    random_vec = rng.normal(size=DIM).astype(np.float32)
    random_vec = random_vec / np.linalg.norm(random_vec)
    rand_rows = top_books_for_vec(random_vec, "random_unit", n=10)
    report.append("### Random unit vector\n")
    report.append("| rank | author | title | year | editions | cosine |")
    report.append("|---:|---|---|---|---:|---:|")
    for rank, row in enumerate(rand_rows, 1):
        report.append(f"| {rank} | {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]:.4f} |")
    report.append("")

    # Difference vector null test (extra)
    hackers_vec = vec_by_text["Movie|1995|Hackers"]
    neuromancer_vec = find_book_vec("William Gibson", "Neuromancer")
    if neuromancer_vec is not None:
        diff = hackers_vec - neuromancer_vec
        diff = diff / np.linalg.norm(diff)
        diff_rows = top_books_for_vec(diff, "hackers_minus_neuromancer", n=10)
        report.append("### `Movie|1995|Hackers - Neuromancer` (difference-vector null test)\n")
        report.append("| rank | author | title | year | editions | cosine |")
        report.append("|---:|---|---|---|---:|---:|")
        for rank, row in enumerate(diff_rows, 1):
            report.append(f"| {rank} | {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]:.4f} |")
        report.append("")

    # ------------------------------------------------------------------
    # Experiment 5: Type-marker geometry
    # ------------------------------------------------------------------
    report.append("## 5. Type-marker geometry\n")

    marker_vecs = [vec_by_text[t] for t in marker_strings]
    report.append(print_matrix(marker_strings, marker_vecs, "Pairwise cosine similarities among markers"))
    report.append("")

    # Extra: does | act as a separator? Compare pipe vs space vs sentence
    separator_strings = [
        "Hackers",
        "Movie Hackers",
        "Movie|Hackers",
        "The Movie Hackers",
    ]
    sep_vecs = embed(separator_strings)
    sep_by_text = {t: sep_vecs[i] for i, t in enumerate(separator_strings)}
    report.append(print_matrix(separator_strings, [sep_by_text[t] for t in separator_strings], "Pipe-vs-space separator probe"))
    report.append("")

    # ------------------------------------------------------------------
    # Summary / proven assumptions / unknowns / follow-ups
    # ------------------------------------------------------------------
    report.append("## 6. Assumptions proven or disproven\n")
    report.append("(This section is populated by inspection of the data above.)\n")
    report.append("### Proven\n")
    report.append("- TBD\n")
    report.append("### Disproven\n")
    report.append("- TBD\n")
    report.append("### Still unknown\n")
    report.append("- TBD\n")

    report.append("## 7. Follow-up experiments\n")
    report.append("- TBD\n")

    report.append("## 8. Appendix: embedded strings\n")
    report.append("```text\n")
    report.append("\n".join(all_strings + separator_strings))
    report.append("\n```\n")

    REPORT_PATH.write_text("\n".join(report) + "\n")
    print(f"report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
