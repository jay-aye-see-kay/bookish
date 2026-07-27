import { usePreferences } from "../api/hooks";
import type { BookMeta } from "../api/types";
import { RatingControl } from "./RatingControl";

/**
 * Shared rateable row: book metadata + the rating control. The rating is
 * always overlaid from the prefs cache (single source of truth), so the same
 * row renders correctly in the profile list, the modal, and the recs list.
 */
export function BookRow({
  meta,
  right,
}: {
  meta: BookMeta;
  right?: React.ReactNode;
}) {
  const { ratingByKey } = usePreferences();
  const current = ratingByKey.get(meta.work_key);

  return (
    <div className="flex items-center gap-4 border-b border-gray-100 py-2">
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium text-gray-900">{meta.title}</div>
        <div className="truncate text-sm text-gray-500">
          {meta.author}
          {meta.year ? ` · ${meta.year}` : ""}
        </div>
      </div>
      {right}
      <RatingControl meta={meta} current={current} />
    </div>
  );
}
