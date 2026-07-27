import { useState } from "react";
import { usePreferences } from "../api/hooks";
import { BookRow } from "../components/BookRow";
import { AddBooksModal } from "../components/AddBooksModal";

export function Preferences() {
  const [modalOpen, setModalOpen] = useState(false);
  const { data, isLoading, isError } = usePreferences();
  const ratings = data ?? [];

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Your books</h1>
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-700"
        >
          Add books
        </button>
      </div>

      {isLoading ? (
        <p className="py-10 text-center text-gray-400">Loading…</p>
      ) : isError ? (
        <p className="py-10 text-center text-red-600">
          Couldn’t load preferences — is the backend running?
        </p>
      ) : ratings.length === 0 ? (
        <p className="py-10 text-center text-gray-400">
          No rated books yet. Click “Add books” to get started.
        </p>
      ) : (
        <div>
          {ratings.map((r) => (
            <BookRow
              key={r.work_key}
              meta={{
                work_key: r.work_key,
                author: r.author,
                title: r.title,
                year: r.year,
              }}
            />
          ))}
        </div>
      )}

      {modalOpen && <AddBooksModal onClose={() => setModalOpen(false)} />}
    </div>
  );
}
