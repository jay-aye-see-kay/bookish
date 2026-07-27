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

export function Recommendations() {
  const recs = useRecommendations();
  const rows = recs.data ?? [];
  const maxScore = rows.reduce((m, r) => Math.max(m, r.score), 0) || 1;

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Recommendations</h1>
        <button
          type="button"
          onClick={() => recs.refetch()}
          disabled={recs.isFetching}
          className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-700 disabled:opacity-50"
        >
          {recs.isFetching ? "Computing…" : "Compute"}
        </button>
      </div>

      {recs.isError ? (
        <p className="py-10 text-center text-red-600">
          {errorMessage(recs.error)}
        </p>
      ) : rows.length === 0 && recs.isFetched && !recs.isFetching ? (
        <p className="py-10 text-center text-gray-400">No recommendations.</p>
      ) : rows.length === 0 ? (
        <p className="py-10 text-center text-gray-400">
          Click “Compute” to rank the catalog against your taste.
        </p>
      ) : (
        <div>
          {rows.map((r) => (
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
