# Web frontend (React + TS + Vite)

A local, single-user web UI over the Go JSON backend (`docs/dev/web-backend.md`).
Two pages: build a taste profile (**Preferences**) and get ranked
**Recommendations**. Runs as its own Vite dev server that proxies `/api` to the
Go backend on `:8090`, so the browser talks same-origin (the backend has no CORS
headers).

## Run

Two processes:

```bash
bin/serve.sh            # Go backend on :8090      (terminal 1)
cd web && npm run dev   # Vite dev server on :5173 (terminal 2)
```

Then open the Vite URL. `curl :5173/api/health` should proxy through to the
backend. The CGO backend **must** be running on `:8090` for the UI to be useful.
The embedding server (`bin/serve-embed.sh`, outside the sandbox) is only needed
for likes outside the 27.5k-vector corpus; recommendations work offline
otherwise.

All JS deps are pinned in `web/package-lock.json`; only Node comes from nix
(`nodejs_26` in `flake.nix`). Run `npm install` in `web/` after cloning.

### Scripts

```bash
npm run dev      # vite dev server
npm run build    # tsc --noEmit && vite build
npm run preview  # serve the production build
npm run lint     # oxlint
npm run fmt      # oxfmt
```

## Stack

| Package | Notes |
|---|---|
| react / react-dom 19 | |
| react-router 8 | import from `react-router` (not `react-router-dom`); declarative `<BrowserRouter>/<Routes>/<Route>` |
| @tanstack/react-query 5 | server-state cache, optimistic updates, on-demand refetch |
| vite 8 + @vitejs/plugin-react | strips TS via esbuild (no tsc in the dev path) |
| tailwindcss 4 + @tailwindcss/vite | CSS-first: `@import "tailwindcss"`, **no** `tailwind.config.js` |
| typescript 7 | native Go compiler; only for `tsc --noEmit` + IDE |
| oxlint / oxfmt | replace eslint+typescript-eslint / prettier. `.oxlintrc.json` enables the react plugins. oxfmt is pre-1.0 — pinned, expect churn on upgrades |

## Architecture: ratings have one source of truth

The 4-segment rating control appears in three places (profile list, add-books
modal, recs list). It does **not** hold its own state per list. Instead:

- `usePreferences()` (query key `['prefs', username]`) is the single source of
  truth for "my rating of book X" and exposes a derived
  `Map<work_key, RatingValue>` (`ratingByKey`).
- `<RatingControl meta current />` renders segments; `current` is looked up from
  that map by `<BookRow>`. Search results (`Book`) and recs (`Recommendation`)
  supply only **metadata**; the rating is overlaid from the prefs cache.
- Rating anything, anywhere, mutates the prefs cache → every visible control for
  that book re-renders. No cross-list syncing.

This is why "the modal shows existing ratings" and "recs rows are rateable" come
for free.

### Optimistic mutations (`useRate` / `useUnrate`)

- `useRate({ workKey, rating, meta })` → `PUT /api/preferences{workKey}`.
  `onMutate` cancels the prefs query, snapshots, and upserts the entry (building
  it from `meta` when the book isn't already in the list). `onError` rolls back,
  `onSettled` invalidates.
- `useUnrate({ workKey })` → `DELETE`; removes the entry optimistically. Clicking
  the already-selected segment toggles to unrated (there is no `0` rating).

Query keys include `username`, so switching users (Header field, persisted in
`localStorage`) auto-refetches everything.

Recommendations are **on-demand**: `useRecommendations()` has `enabled: false`
and is triggered by the Compute button's `refetch()`. Rating a rec just shows the
new rating until the next Compute, when the backend drops rated works.

## Layout

```
web/
  index.html  package.json  package-lock.json  vite.config.ts  tsconfig.json
  .oxlintrc.json
  src/
    main.tsx                 # QueryClientProvider + <BrowserRouter> + routes
    App.tsx                  # layout: <Header/> + routed <Outlet/>
    index.css                # @import "tailwindcss";
    vite-env.d.ts            # vite/client types (css side-effect imports)
    api/
      client.ts              # fetch wrapper: base /api, inject X-User, ApiError{status,message}
      types.ts               # Health, Book, Rating, Recommendation, RatingValue, BookMeta
      hooks.ts               # useHealth, useSearch, usePreferences, useRecommendations, useRate, useUnrate
    state/user.ts            # useUser(): username in localStorage (useSyncExternalStore)
    components/
      Header.tsx             # nav + username field + health pill
      RatingControl.tsx      # 4-segment control ({-2,-1,1,2})
      BookRow.tsx            # metadata + RatingControl (shared, overlays prefs cache)
      AddBooksModal.tsx      # debounced search box + result rows
    pages/
      Preferences.tsx        # rated list + Add books modal
      Recommendations.tsx    # Compute button + ranked rateable rows + score bar
```

## Backend API

See `docs/dev/web-backend.md` for the authoritative reference. All book-scoped
calls send `X-User: <username>`. `work_key` includes the leading `/works/…`, so
paths are built by concatenation: `` `/api/preferences${workKey}` `` →
`/api/preferences/works/OL64365W`.

Error states surfaced on the Recommendations page: `400` (no ratings), `409`
(likes/dislikes cancel out — degenerate centroid), `502` (embedding server
unreachable — points the user at the health pill).

## Out of scope for v1

- Recs filters (`n`, `exclude_rated_authors`) — the API supports them, the UI
  doesn't expose them yet.
- Bulk import from `preferences.txt`.
- Serving the built assets from the Go binary (single-origin prod). For now it's
  dev-server + proxy only.
