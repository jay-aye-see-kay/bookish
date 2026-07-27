import { useEffect, useState } from "react";
import { useSearch } from "../api/hooks";
import { BookRow } from "./BookRow";

/** Debounce a value by `ms`. */
function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

export function AddBooksModal({ onClose }: { onClose: () => void }) {
  const [q, setQ] = useState("");
  const debouncedQ = useDebounced(q, 250);
  const search = useSearch(debouncedQ);
  const results = search.data ?? [];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="presentation"
      className="fixed inset-0 z-10 flex items-start justify-center bg-black/40 p-4 sm:p-10"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Add books"
        className="flex max-h-full w-full max-w-2xl flex-col rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-gray-200 p-4">
          {/* oxlint-disable-next-line jsx-a11y/no-autofocus */}
          <input
            autoFocus
            type="search"
            placeholder="Search books to rate…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="flex-1 rounded-md border border-gray-300 px-3 py-2 outline-none focus:border-gray-500"
          />
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-3 py-2 text-gray-600 hover:bg-gray-100"
          >
            Done
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {debouncedQ.trim() === "" ? (
            <p className="py-8 text-center text-gray-400">
              Type to search the catalog.
            </p>
          ) : search.isLoading ? (
            <p className="py-8 text-center text-gray-400">Searching…</p>
          ) : results.length === 0 ? (
            <p className="py-8 text-center text-gray-400">No matches.</p>
          ) : (
            results.map((b) => (
              <BookRow
                key={b.work_key}
                meta={{
                  work_key: b.work_key,
                  author: b.author,
                  title: b.title,
                  year: b.year,
                }}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}
