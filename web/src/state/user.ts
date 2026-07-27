import { useSyncExternalStore } from "react";

const KEY = "bookish.username";
const DEFAULT = "me";

function read(): string {
  return localStorage.getItem(KEY) ?? DEFAULT;
}

const listeners = new Set<() => void>();

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function setUsername(name: string): void {
  const next = name.trim() || DEFAULT;
  localStorage.setItem(KEY, next);
  listeners.forEach((cb) => cb());
}

/** The current username (= `X-User`), persisted in localStorage. */
export function useUser(): { username: string; setUsername: (n: string) => void } {
  const username = useSyncExternalStore(subscribe, read, () => DEFAULT);
  return { username, setUsername };
}
