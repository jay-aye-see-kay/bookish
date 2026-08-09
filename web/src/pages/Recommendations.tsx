import { useEffect, useMemo, useRef, useState } from "react";
import { useRecommendations } from "../api/hooks";
import { ApiError } from "../api/client";
import { BookRow } from "../components/BookRow";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 400:
        return "Rate some books first — recommendations need at least one rating.";
      case 409:
        return "Your likes and dislikes cancel out. Try adding more ratings.";
      case 502:
        return "The embedding server is unreachable (see the health pill). Some rated books need it to compute recommendations.";
    }
  }
  return "Couldn’t compute recommendations — is the backend running?";
}

function AuthorFilter({
  authors,
  excluded,
  onToggle,
  onClear,
}: {
  authors: string[];
  excluded: Set<string>;
  onToggle: (author: string) => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
      >
        Authors{excluded.size > 0 ? ` (${excluded.size} hidden)` : ""}
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-1 max-h-80 w-64 overflow-y-auto rounded-md border border-gray-200 bg-white py-1 shadow-lg">
          {excluded.size > 0 && (
            <button
              type="button"
              onClick={onClear}
              className="mb-1 w-full border-b border-gray-100 px-3 py-1.5 text-left text-xs text-blue-600 hover:bg-gray-50"
            >
              Clear all filters
            </button>
          )}
          {authors.map((author) => (
            <label
              key={author}
              className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm hover:bg-gray-50"
            >
              <input
                type="checkbox"
                checked={excluded.has(author)}
                onChange={() => onToggle(author)}
              />
              <span className="truncate">{author}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

const DISPLAY_COUNT = 20;

export function Recommendations() {
  const recs = useRecommendations();
  const pool = recs.data ?? [];
  const [excluded, setExcluded] = useState<Set<string>>(new Set());

  // Authors come from the visible slice only, so filtering one in backfills
  // the next-ranked candidates without offering their authors as options.
  const visibleRows = pool
    .filter((r) => !excluded.has(r.author))
    .slice(0, DISPLAY_COUNT);

  const authors = useMemo(
    () =>
      Array.from(
        new Set([...visibleRows.map((r) => r.author), ...excluded]),
      ).sort((a, b) => a.localeCompare(b)),
    [visibleRows, excluded],
  );

  const maxScore = visibleRows.reduce((m, r) => Math.max(m, r.score), 0) || 1;

  function toggle(author: string) {
    setExcluded((prev) => {
      const next = new Set(prev);
      if (next.has(author)) next.delete(author);
      else next.add(author);
      return next;
    });
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Recommendations</h1>
        <div className="flex items-center gap-2">
          {authors.length > 0 && (
            <AuthorFilter
              authors={authors}
              excluded={excluded}
              onToggle={toggle}
              onClear={() => setExcluded(new Set())}
            />
          )}
          <button
            type="button"
            onClick={() => recs.refetch()}
            disabled={recs.isFetching}
            className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-700 disabled:opacity-50"
          >
            {recs.isFetching ? "Computing…" : "Compute"}
          </button>
        </div>
      </div>

      {recs.isError ? (
        <p className="py-10 text-center text-red-600">
          {errorMessage(recs.error)}
        </p>
      ) : pool.length === 0 && recs.isFetched && !recs.isFetching ? (
        <p className="py-10 text-center text-gray-400">No recommendations.</p>
      ) : pool.length === 0 ? (
        <p className="py-10 text-center text-gray-400">
          Click “Compute” to rank the catalog against your taste.
        </p>
      ) : visibleRows.length === 0 ? (
        <p className="py-10 text-center text-gray-400">
          All recommendations are filtered out by author.
        </p>
      ) : (
        <div>
          {visibleRows.map((r) => (
            <BookRow
              key={r.work_key}
              meta={{
                work_key: r.work_key,
                author: r.author,
                title: r.title,
                year: r.year,
              }}
              right={
                <div className="flex w-28 items-center gap-2">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100">
                    <div
                      className="h-full rounded-full bg-blue-500"
                      style={{ width: `${(r.score / maxScore) * 100}%` }}
                    />
                  </div>
                  <span className="w-10 text-right text-xs tabular-nums text-gray-500">
                    {r.score.toFixed(2)}
                  </span>
                </div>
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}
