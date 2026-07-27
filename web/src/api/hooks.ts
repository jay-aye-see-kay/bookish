import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { apiFetch } from "./client";
import { useUser } from "../state/user";
import type {
  Book,
  BookMeta,
  Health,
  Rating,
  RatingValue,
  Recommendation,
} from "./types";

export function useHealth() {
  const { username } = useUser();
  return useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => apiFetch<Health>("/health", { username, signal }),
    refetchInterval: 30_000,
  });
}

export function useSearch(q: string) {
  const { username } = useUser();
  const trimmed = q.trim();
  return useQuery({
    queryKey: ["search", trimmed],
    enabled: trimmed.length > 0,
    queryFn: ({ signal }) =>
      apiFetch<Book[]>(
        `/books?q=${encodeURIComponent(trimmed)}&limit=20`,
        { username, signal },
      ),
    placeholderData: (prev) => prev,
  });
}

export function usePreferences() {
  const { username } = useUser();
  const query = useQuery({
    queryKey: ["prefs", username],
    queryFn: ({ signal }) =>
      apiFetch<Rating[]>("/preferences", { username, signal }),
  });
  const ratingByKey = new Map<string, RatingValue>();
  for (const r of query.data ?? []) ratingByKey.set(r.work_key, r.rating);
  return { ...query, ratingByKey };
}

export function useRecommendations() {
  const { username } = useUser();
  return useQuery({
    queryKey: ["recs", username],
    enabled: false, // on-demand: computed via refetch()
    gcTime: 0,
    queryFn: ({ signal }) =>
      apiFetch<Recommendation[]>(
        "/recommendations?n=20&exclude_rated_authors=false",
        { username, signal },
      ),
  });
}

type RateVars = { workKey: string; rating: RatingValue; meta: BookMeta };

export function useRate() {
  const { username } = useUser();
  const qc = useQueryClient();
  const key = ["prefs", username];
  return useMutation({
    mutationFn: ({ workKey, rating }: RateVars) =>
      apiFetch(`/preferences${workKey}`, {
        method: "PUT",
        username,
        body: { rating },
      }),
    onMutate: async ({ workKey, rating, meta }: RateVars) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<Rating[]>(key) ?? [];
      const idx = prev.findIndex((r) => r.work_key === workKey);
      let next: Rating[];
      if (idx >= 0) {
        next = prev.slice();
        next[idx] = { ...next[idx], rating };
      } else {
        next = [{ ...meta, rating }, ...prev];
      }
      qc.setQueryData<Rating[]>(key, next);
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx) qc.setQueryData(key, ctx.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: key }),
  });
}

type UnrateVars = { workKey: string };

export function useUnrate() {
  const { username } = useUser();
  const qc = useQueryClient();
  const key = ["prefs", username];
  return useMutation({
    mutationFn: ({ workKey }: UnrateVars) =>
      apiFetch(`/preferences${workKey}`, { method: "DELETE", username }),
    onMutate: async ({ workKey }: UnrateVars) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<Rating[]>(key) ?? [];
      qc.setQueryData<Rating[]>(
        key,
        prev.filter((r) => r.work_key !== workKey),
      );
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx) qc.setQueryData(key, ctx.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: key }),
  });
}
