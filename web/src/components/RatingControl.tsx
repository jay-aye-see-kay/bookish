import { useRate, useUnrate } from "../api/hooks";
import type { BookMeta, RatingValue } from "../api/types";

const SEGMENTS: { value: RatingValue; label: string; title: string }[] = [
  { value: -2, label: "−−", title: "strongly dislike" },
  { value: -1, label: "−", title: "dislike" },
  { value: 1, label: "+", title: "like" },
  { value: 2, label: "++", title: "strongly like" },
];

function segClass(value: RatingValue, selected: boolean): string {
  const negative = value < 0;
  if (selected) {
    return negative
      ? "bg-red-600 text-white"
      : "bg-green-600 text-white";
  }
  return negative
    ? "bg-red-50 text-red-700 hover:bg-red-100"
    : "bg-green-50 text-green-700 hover:bg-green-100";
}

/**
 * The one rating primitive: 4 segments matching {-2,-1,1,2}.
 * Click unselected → PUT; click selected → DELETE (toggle to unrated).
 * Current value is read from the prefs cache by the caller and passed in.
 */
export function RatingControl({
  meta,
  current,
}: {
  meta: BookMeta;
  current: RatingValue | undefined;
}) {
  const rate = useRate();
  const unrate = useUnrate();
  const workKey = meta.work_key;

  return (
    <div className="inline-flex overflow-hidden rounded-md border border-gray-300 text-sm font-medium">
      {SEGMENTS.map((seg, i) => {
        const selected = current === seg.value;
        return (
          <button
            key={seg.value}
            type="button"
            title={seg.title}
            aria-label={seg.title}
            aria-pressed={selected}
            className={`w-9 py-1 ${i > 0 ? "border-l border-gray-300" : ""} ${segClass(seg.value, selected)}`}
            onClick={() => {
              if (selected) unrate.mutate({ workKey });
              else rate.mutate({ workKey, rating: seg.value, meta });
            }}
          >
            {seg.label}
          </button>
        );
      })}
    </div>
  );
}
