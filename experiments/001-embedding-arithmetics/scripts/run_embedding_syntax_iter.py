#!/usr/bin/env python3
"""Iteration: test multiple input syntaxes and explicit marker subtraction."""

import hashlib
import os
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
REPORT_PATH = ROOT / "experiments" / "001-embedding-arithmetics" / "iteration-02-syntax-formats.md"


def check_server():
    r = requests.get(HEALTH, timeout=10)
    r.raise_for_status()
    assert r.json().get("status") == "ok", r.text


def embed(texts):
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
    return float(np.dot(a, b))


def safe(s):
    return hashlib.sha256(s.encode()).hexdigest()[:12]


def top_books_for_vec(vec, label, n=10):
    query_path = OUT_DIR / f"query_{safe(label)}_{datetime.now().strftime('%H%M%S%f')}.parquet"
    vec = np.asarray(vec, dtype=np.float32)
    table = pa.table({
        "label": [label],
        "vec": pa.array([vec.tolist()], type=pa.list_(pa.float32(), DIM)),
    })
    pq.write_table(table, query_path)
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


def find_book_vec(author, title):
    con = duckdb.connect(BOOKS_DB, read_only=True)
    con.execute(f"ATTACH IF NOT EXISTS '{BOOKS_DB}' AS books (READ_ONLY);")
    rows = con.execute(f"""
        SELECT e.vec
        FROM read_parquet('{CACHE_DIR}/*.parquet') e
        JOIN (
            SELECT 'Book|' || a.name || '|' || b.title AS input_text
            FROM books.books b
            JOIN books.authors a ON a.author_key = b.primary_author_key
            WHERE b.title ILIKE '%{title.replace(chr(39), chr(39)+chr(39))}%'
              AND a.name ILIKE '%{author.replace(chr(39), chr(39)+chr(39))}%'
            ORDER BY b.edition_count DESC, b.first_publish_year
            LIMIT 1
        ) m ON m.input_text = e.input_text
    """).fetchall()
    con.close()
    if not rows:
        return None
    return np.array(rows[0][0], dtype=np.float32)


def render_table(title, rows, expected=None):
    out = [f"### {title}", "", "| rank | author | title | year | editions | cosine |", "|---:|---|---|---|---:|---:|"]
    for rank, (author, title, year, editions, sim) in enumerate(rows, 1):
        out.append(f"| {rank} | {author} | {title} | {year} | {editions} | {sim:.4f} |")
    if expected:
        out.append(f"\n*Cosine to `{expected[1]}`: {expected[0]:.4f}*\n")
    return "\n".join(out)


def main():
    check_server()
    report = []
    report.append("# Embedding Syntax & Subtraction Iteration: Results\n")
    report.append(f"*Generated: {datetime.now(timezone.utc).isoformat()}Z*\n")

    # ------------------------------------------------------------------
    # Define concepts and formats
    # ------------------------------------------------------------------
    concepts = {
        "Hackers": {
            "title": "Hackers",
            "type": "Movie",
            "year": "1995",
            "expected_book": ("William Gibson", "Neuromancer"),
            "expected_theme": ["Neuromancer", "Snow Crash", "Count Zero"],
        },
        "Blade Runner": {
            "title": "Blade Runner",
            "type": "Movie",
            "year": "1982",
            "expected_book": ("Philip K. Dick", "Do Androids Dream of Electric Sheep?"),
            "expected_theme": ["Do Androids Dream of Electric Sheep?", "Neuromancer"],
        },
        "Dune": {
            "title": "Dune",
            "type": "Movie",
            "year": "2021",
            "expected_book": ("Frank Herbert", "Dune"),
            "expected_theme": ["Dune", "Dune Messiah"],
        },
        "Halo": {
            "title": "Halo",
            "type": "Game",
            "year": None,
            "expected_book": ("William C. Dietz", "Halo: The Flood"),
            "expected_theme": ["Halo"],
        },
        "1984": {
            "title": "1984",
            "type": "Book",
            "year": None,
            "expected_book": ("George Orwell", "1984"),
            "expected_theme": ["1984", "Animal Farm"],
        },
    }

    marker_formats = {
        "pipe": ("|", lambda t, y, tp: f"{tp}|{y}|{t}" if y else f"{tp}|{t}"),
        "pipe_no_year": ("|", lambda t, y, tp: f"{tp}|{t}"),
        "colon_nl": (": ", lambda t, y, tp: f"category: {tp}\ntitle: {t}" + (f"\nyear: {y}" if y else "")),
        "json": (": ", lambda t, y, tp: '{' + f'"category": "{tp}", "title": "{t}"' + (f', "year": "{y}"' if y else '') + '}'),
        "ini": ("=", lambda t, y, tp: f"[item]\ncategory={tp}\ntitle={t}" + (f"\nyear={y}" if y else "")),
        "yaml": (": ", lambda t, y, tp: f"category: {tp}\ntitle: {t}" + (f"\nyear: {y}" if y else "")),
        "sentence": (" ", lambda t, y, tp: f"The {' '.join(filter(None, [y, tp])).lower()} {t}" if y else f"The {tp.lower()} {t}"),
    }

    # Collect all strings to embed: bare markers, explicit markers, formats
    all_strings = set()
    for name, c in concepts.items():
        all_strings.add(c["title"])
        all_strings.add(f"{c['title']} ({c['type'].lower()})")
        all_strings.add(c["type"])
        all_strings.add(f"category: {c['type']}")
        if c["year"]:
            all_strings.add(c["year"])
            all_strings.add(f"year: {c['year']}")
            all_strings.add(f"release year: {c['year']}")
        for key, (_, fmt) in marker_formats.items():
            all_strings.add(fmt(c["title"], c["year"], c["type"]))

    all_strings = sorted(all_strings)
    print(f"embedding {len(all_strings)} strings...")
    all_vectors = embed(all_strings)
    vec_by_text = {t: all_vectors[i] for i, t in enumerate(all_strings)}

    # ------------------------------------------------------------------
    # For each concept, evaluate formats and subtraction attempts
    # ------------------------------------------------------------------
    for name, c in concepts.items():
        report.append(f"## {name}\n")
        t, y, tp = c["title"], c["year"], c["type"]

        # Baselines
        bare_vec = vec_by_text[t]
        paren_vec = vec_by_text[f"{t} ({tp.lower()})"]

        expected_vec = find_book_vec(*c["expected_book"]) if c["expected_book"] else None

        report.append(render_table("Bare title", top_books_for_vec(bare_vec, f"{name}_bare"), expected=(cosim(bare_vec, expected_vec), c["expected_book"][1]) if expected_vec is not None else None))
        report.append("")
        report.append(render_table("Parenthetical", top_books_for_vec(paren_vec, f"{name}_paren"), expected=(cosim(paren_vec, expected_vec), c["expected_book"][1]) if expected_vec is not None else None))
        report.append("")

        # Each format
        for fmt_key, (_, fmt) in marker_formats.items():
            input_text = fmt(t, y, tp)
            input_vec = vec_by_text[input_text]

            # Try subtracting the bare marker(s)
            sub_bare = input_vec - vec_by_text[tp]
            if y:
                sub_bare = sub_bare - vec_by_text[y]
            sub_bare = sub_bare / np.linalg.norm(sub_bare)

            # Try subtracting explicit category/year markers
            sub_explicit = input_vec - vec_by_text[f"category: {tp}"]
            if y:
                sub_explicit = sub_explicit - vec_by_text[f"year: {y}"]
            sub_explicit = sub_explicit / np.linalg.norm(sub_explicit)

            report.append(f"### Format: `{fmt_key}` → `{input_text.replace(chr(10), '\\n')}`\n")
            report.append(render_table("marked input", top_books_for_vec(input_vec, f"{name}_{fmt_key}_input"), expected=(cosim(input_vec, expected_vec), c["expected_book"][1]) if expected_vec is not None else None))
            report.append("")
            report.append(render_table("marked - bare markers", top_books_for_vec(sub_bare, f"{name}_{fmt_key}_sub_bare")))
            report.append("")
            report.append(render_table("marked - explicit markers", top_books_for_vec(sub_explicit, f"{name}_{fmt_key}_sub_explicit")))
            report.append("")

    # ------------------------------------------------------------------
    # Marker geometry with explicit markers
    # ------------------------------------------------------------------
    report.append("## Marker geometry: bare vs explicit\n")
    marker_labels = [
        "Movie", "category: Movie",
        "Book", "category: Book",
        "Game", "category: Game",
        "1995", "year: 1995", "release year: 1995",
        "1982", "year: 1982",
        "2021", "year: 2021",
    ]
    present = [m for m in marker_labels if m in vec_by_text]
    marker_vecs = [vec_by_text[m] for m in present]
    report.append("|       | " + " | ".join(present) + " |")
    report.append("|" + "---|" * (len(present) + 1))
    for i, mi in enumerate(present):
        row = [mi]
        for j, mj in enumerate(present):
            row.append(f"{cosim(marker_vecs[i], marker_vecs[j]):.4f}")
        report.append("| " + " | ".join(row) + " |")
    report.append("")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    report.append("## Summary\n")
    report.append("- Subtraction results are recorded above. Compare `marked input`, `marked - bare markers`, and `marked - explicit markers` for each concept/format.\n")
    report.append("- If any subtraction variant consistently outperforms the marked input or the bare title, that format is worth pursuing.\n")
    report.append("- If all subtraction variants underperform, the conclusion is that the marked input itself is the useful representation and arithmetic cannot strip the markers.\n")

    REPORT_PATH.write_text("\n".join(report) + "\n")
    print(f"report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
