# app-v2 - S.O.B.E.R. frontend

This is the rebuilt S.O.B.E.R. frontend: Vite 8 + React 19 + TypeScript 6, Tailwind
4, MapLibre GL, React Router, TanStack Query, and Zod. It talks to the Flask API in
`../API/MAPS`.

The main project documentation is in the repository root:

- `../README.md` - run commands, demo script, model card, API surface, limitations.
- `./REPORT.md` - the full build report, design decisions, and verification.

## Run

The backend must be on `http://127.0.0.1:5000`.

```powershell
npm.cmd install
npm.cmd run dev      # http://localhost:5173
```

On this Windows machine use the `.cmd` shims; the `.ps1` shims are blocked by the
execution policy.

The dev server and the production preview proxy:

- `/api` -> `http://127.0.0.1:5000`
- `/geocode` -> `https://nominatim.openstreetmap.org`

The browser only sees one origin, so there is no CORS setup. Override the API target
with `SOBER_API` if needed.

```powershell
$env:SOBER_API = "http://127.0.0.1:5000"
npm.cmd run dev
```

## Build and gates

```powershell
npx.cmd tsc -b
npm.cmd run lint
npm.cmd run build
npm.cmd run preview   # http://localhost:4173
npx.cmd tsx scripts\validate-contract.ts
```

The build prints a chunk-size warning because MapLibre is a single large dependency.
That is expected; it is not a failure. Sourcemaps are enabled in `vite.config.ts`.

The contract gate checks 24 recorded API fixtures against the current Zod contract.
It reads local files; the backend does not need to be running.

## Structure

```text
src/
  App.tsx                 shell, panel routing, Present wiring
  main.tsx                router + query client
  index.css               Tailwind 4 theme and base styles
  components/             TopBar, TimeDock, TrendStrip, RiskPill, Wordmark, DataState
  features/explore/       ExplorePanel
  features/plan/          PlanPanel, RouteCard, PlaceSearch
  features/method/        MethodScreen
  features/present/       PresentBar
  lib/                    api.ts, contract.ts, queries.ts, risk.ts, use-url-state.ts
  map/                    MapCanvas.tsx, basemap-style.ts
public/
  basemap/nyc.pmtiles     30.3 MB local NYC extract
  geo/world-countries.geojson  191 KB offline globe backdrop
scripts/
  validate-contract.ts    API contract gate
```

## URL state

The app keeps its state in the URL so a reload and the Present bar cannot disagree:

```text
/explore?lat=40.758&lon=-73.9855&hour=23&day=5
/plan?olat=...&olon=...&oname=...&dlat=...&dlon=...&dname=...&hour=23&day=5&run=1
/method
```

`goTo(panel, patch)` is the single navigation helper. Do not call `setPanel` and a
separate URL update; that races the path and the search params.

## Map notes

- The basemap is a local PMTiles extract. It has no glyph/symbol layers, so it never
  requests a font at runtime.
- The globe is the native MapLibre globe projection plus the offline 110m world
  GeoJSON. It is skipped at 600 px and below, and respects `prefers-reduced-motion`.
- The route camera is computed manually in Web Mercator (`routeCamera`), not with
  `cameraForBounds`, so the left panel padding is explicit.
- MapLibre is excluded from Vite dependency pre-bundling (`optimizeDeps.exclude`);
  its worker URL otherwise points into `.vite/deps`, where Vite does not emit the
  worker file.

## Demo notes

- Use a fresh tab for the judged run. A tab with many prior clicks can retain a stale
  map point or panel.
- The backend route cache is in-memory and expires after 300 seconds. Pre-warm both
  demo hours within five minutes of the run; do not restart the backend after warming.
- Weather is unavailable without `WEATHER_API_KEY`; the UI shows the fallback. Do not
  present it as live.
- The route provider is the only live network dependency in the route path. If it
  fails, the UI shows the error; it does not invent a route.
