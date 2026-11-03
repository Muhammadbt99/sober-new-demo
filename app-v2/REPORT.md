# S.O.B.E.R. frontend v2 - build report

**Build date:** 2026-10-31
**Scope:** rebuild `D:\S.O.B.E.R NEW DEMO\app-v2` and connect it to the validated
Flask + crash-model backend in `API/MAPS`. The backend was changed only for the
approved model, route, privacy, and endpoint work. `D:\S.O.B.E.R` was not touched.

The result is a presenter-led, judged-demo build. It is optimized for a 1280-1920 px
stage and stays usable at 390 px. It is not a production hosting setup.

---

## 1. What was rebuilt

The old frontend was replaced rather than patched. The new app is organized around
four surfaces and one presenter layer:

- **Explore** - a live risk surface at a point, with the 0-100 score, the model and
  nightlife breakdown, the hourly trend, historical hotspots, the nearest safe stop,
  and an explicit outside-coverage state.
- **Plan** - origin/destination comparison with two provider routes, expected-crash
  totals, distance and duration, a route recommendation based on expected crashes,
  and a segment-by-segment explanation.
- **Method** - a light editorial model card: the gate, the measured metrics, the
  feature table, the score construction, and what is deliberately not in the score.
- **Present** - a seven-beat presenter bar that drives the whole demo with the arrow
  keys, plus a globe intro that lands on the selected NYC route.

The map is a single MapLibre instance shared by the surfaces. The route, risk hexes,
hotspots, and segment selection are layers on that instance, not separate maps.

---

## 2. Stack and source

| Layer | Choice |
|---|---|
| Build | Vite 8.3.5 |
| UI | React 19.3.1, TypeScript 6.0.3 (`strict`, `noUncheckedIndexedAccess`) |
| Styling | Tailwind CSS 4.3.3, Radix primitives, shadcn-style local components |
| Map | MapLibre GL 6.15 (direct, no react-map-gl wrapper), PMTiles 4.5, h3-js 4.5 |
| Routing | React Router 7.18.5 |
| Server state | TanStack Query 5.105 |
| Validation | Zod 4.6.7 |
| Motion | Motion 12.43 |
| Backend | Flask, SQLite, scikit-learn boosted Poisson, pandas/numpy |

Measured source size:

```text
src/                 23 files, 6,805 lines
scripts/             106 lines (validate-contract.ts)
```

The 23 `src/` files are:

```text
App.tsx  main.tsx  index.css
components/DataState.tsx  RiskPill.tsx  TimeDock.tsx  TopBar.tsx
components/TrendStrip.tsx  Wordmark.tsx
features/explore/ExplorePanel.tsx
features/method/MethodScreen.tsx
features/plan/PlaceSearch.tsx  PlanPanel.tsx  RouteCard.tsx
features/present/PresentBar.tsx
lib/api.ts  contract.ts  queries.ts  risk.ts  use-url-state.ts  utils.ts
map/basemap-style.ts  MapCanvas.tsx
```

The rebuilt UI uses MapLibre. `leaflet`, `react-leaflet`, and `@types/leaflet`
remain in `package.json` from the earlier prototype but are not imported by the new
source. They do not appear in the bundle.

---

## 3. Design and screens

The design is deliberately split:

- **Map and planner:** dark, cinematic, instrument-panel typography (Space Grotesk
  for display, IBM Plex Mono for data). The map is the stage; the planner is a
  narrow left rail.
- **Method:** light editorial paper. It is the one screen that is read rather than
  operated, so it gets a different surface, a large headline, and a serif-free
  editorial grid.
- **Wordmark:** the **S.O.B.E.R.** wordmark is kept. The top bar reads
  `SAFER ROUTING / NYC`, with Explore, Plan, Method, a live model status, and Present.

The planner panel has a fixed header, a scrollable body, and a sticky footer. After a
successful comparison it collapses the origin/destination form into a compact trip
summary; **Edit trip** restores the form. The route improvement is a single
`9.1% lower` chip in the route footer, not a separate row. The panel scroll resets
to the top on route/edit changes, and `overflow-anchor: none` prevents the browser
from fighting that reset.

The segment detail sits **above** the segment list. Selecting a segment auto-scrolls
the detail into view. It shows the segment action callout first, then the metrics
(rate, this-segment expected crashes, percentile, model points, nightlife points),
the reasons, the full H3 cell, and the midpoint. The misleading "walking proxy" line
from the prototype was removed.

The risk legend has five rows: LOW, MEDIUM, HIGH, CRITICAL, and a gray **NO MODEL**
row that matches the `rule_based_only` hex color. The legend is raised above the
TimeDock (`bottom: 156px`).

The TimeDock is a single time control for both surfaces: hour slider, day select,
and a **Now** button. The scrubber commits on pointer release, key release, and
blur, rather than firing a network request on every slider tick. The route and risk
queries follow the committed hour.

Explore selections from the hotspot list ease the map to the chosen point. Plan
keeps camera control for the route fit. Outside Plan, route lines and A/B markers are
hidden so Explore shows only the risk surface.

---

## 4. Globe intro

The intro is native MapLibre, not a custom WebGL layer:

- `projection: { type: "globe" }` in the basemap style.
- A dark `sky` atmosphere.
- An offline 110m world-countries GeoJSON for land, coast, and borders. This adds
  no runtime dependency and no network request.
- A red destination dot for the selected route.

The flight is two-legged on purpose: a globe hold, a regional view at zoom `6.2`,
then the street-level route camera. A single globe-to-street flight would spend most
of its time over a local PMTiles extract that only covers NYC; the regional leg keeps
the camera over the extract while the zoom crosses the basemap's useful range.

The PMTiles layers start at zoom 9 and fade in. The world land stays opaque to
zoom 9.5, then fades out by zoom 12; the coast fades by zoom 8 and the borders by
zoom 7.5. That removes the hard rectangular tile patch at the regional stage.

Responsive globe zoom:

| Viewport width | Globe zoom |
|---|---|
| `< 600` | `0.95` (intro skipped) |
| `< 1000` | `1.35` |
| `< 1500` | `1.8` |
| `< 1800` | `2.0` |
| `>= 1800` | `2.15` |

A/B markers hide below zoom `7.4` and fade back in at the route. The intro respects
`prefers-reduced-motion` by jumping straight to the destination. At 600 px and below
it is skipped entirely, because the mobile bottom sheet would hide the globe; the
map goes straight to the framed route. A bottom-right replay control restarts it.

The route camera is computed manually in Web Mercator (`routeCamera`) rather than
with `map.cameraForBounds`, so the panel-aware padding is explicit. `fitRouteBounds`
uses `map.easeTo` with the same camera. Verified final cameras:

| Viewport | Route camera |
|---|---|
| 1280x900 | zoom `12.179` |
| 1440x900 | zoom `12.179`, center `[-74.0137, 40.7229]` |
| 1920x1080 | zoom `12.530` |
| 390x844 | zoom `12.050` |

Dev-only globals `window.__soberMap`, `window.__soberGlobe`, and `window.__soberLog`
exist for testing and are not in production builds.

---

## 5. Plan and segment explanation

The demo scenario is **Times Square -> Brooklyn Borough Hall** (`40.6928, -73.9901`)
on Saturday at 23:00. The route API returns two provider routes; the UI scores both
on expected injury-or-fatal crashes, not on the 0-100 average risk.

At 23:00:

```text
safest   0.1521 expected crashes   12.3 km   18 min
fastest  0.1673 expected crashes    8.9 km   18 min
improvement 9.1% lower
```

At 03:00:

```text
safest   0.1000 expected crashes
fastest  0.1177 expected crashes
improvement 15.0% lower
```

The recommendation uses the **lowest total expected injury crashes**. Duration only
drives the fastest/balanced labels. A synthetic detour is labelled as synthetic and
is only reported as an improvement when it is measured.

Each segment exposes `reasons`, `action`, `model_points`, and `nightlife_points`.
The worst segment at 23:00 is Segment 4: 99.6th percentile, model `60/60`, nightlife
`40/40`, expected rate `0.0232`, this-segment expected crashes `0.025`. The segment
list shows the full H3 cell IDs.

The outside-NYC state is explicit. At Newark (`40.7357, -74.1724`) the panel shows
**Outside model coverage**, crash model `0 / 60`, rate `-`, no crash points, and a
gray `NO MODEL` legend. The API coverage is `rule_based_only`; only the nightlife
component contributes. The point's coordinate is used as the title because the
reverse geocoder returned a misleading neighborhood label for the earlier Hoboken
test; the panel is honest rather than confidently wrong.

---

## 6. Present mode

`Present` opens a bottom presenter bar with seven beats. `ArrowRight` and
`ArrowLeft` move, `Escape` exits and removes `present=1`. Navigation goes through a
single `goTo(panel, patch)` helper so the path and the search params cannot race.

| # | Beat | Verified state |
|---|---|---|
| 1 | Live risk surface | Explore at Times Square; CRITICAL 100; model 60/60; nightlife 40/40 |
| 2 | Load the scenario | Plan; Times Square -> Brooklyn Borough Hall; 23:00 |
| 3 | Fastest vs safest | Two routes; safest 0.152 vs fastest 0.167; `9.1% lower` |
| 4 | Explain the worst segment | Segment 3 selected (`seg=3`); detail scrolled into view |
| 5 | Change the hour | 03:00; `15.0%` lower; 0.100 vs 0.118 |
| 6 | Show the measured gate | Method; real metrics and version |
| 7 | Be honest outside NYC | Newark; outside coverage; no crash points |

Step 3 selects the worst segment once route data resolves, even if the presenter
advances before the query lands (`worstSegmentAppliedRef`). Step 4 auto-scrolls the
segment detail; at 1440 the detail lands at `detailTop 199` with `scrollTop 586`.
Step 6 uses Newark rather than Hoboken because Hoboken was reverse-geocoded as
"Lower Manhattan".

The presenter captions are corrected: step 2 says "Brooklyn Borough Hall"; step 6
says "Newark is outside the model."

---

## 7. Data contracts and API

The frontend validates every response with Zod. The contract gate checks 24 recorded
live-response fixtures against the current Zod contract; it reads local files and
does not call the backend.

Every JSON response carries `schema_version: "1.0.0"` through an `after_request`
hook. The backend exposes **15 routes**:

```text
/health  /risk  /safe_route  /heatmap  /stats  /alerts
/alerts/check_location  /ml/stats  /ml/severity  /ml/confusion_matrix
/ml/feature_importance  /risk/trend  /hotspots  /nearest_safe  /risk/compare
```

The demo UI calls 11. The four `/ml/*` routes are legacy API-only endpoints and are
not surfaced in the rebuilt UI.

Demo rate limits are `60/minute` for `/safe_route` and `/heatmap`. The heatmap
regression test derives its request count from the `HEATMAP_RATE_LIMIT` module
constant, so the test and the server cannot drift.

Coverage is the NYC model bounding box. Outside it the API returns
`rule_based_only`, no crash points, and a null crash-model rate. The frontend renders
that state rather than substituting a fake score.

The dev server and the production preview both proxy `/api` to
`http://127.0.0.1:5000` and `/geocode` to Nominatim. The browser sees one origin.
The geocoder is live but the demo uses preset coordinates and does not need it.

---

## 8. Model and scoring

The model is a boosted Poisson regressor (`poisson_deep`) over H3 res-8 cells,
version `2026.10.29-1`, status `approved`.

| Field | Value |
|---|---|
| Model surface | 1,120 H3 res-8 cells |
| Training window | 2020-01-01 to 2026-06-11 |
| Crashes | 192,219 injury-or-fatal crashes |
| Temporal deviance | **0.035436** vs EB baseline **0.046691** (24.1% lower) |
| Brooklyn holdout deviance | **0.055368** vs EB baseline **0.056917** (2.7% lower) |
| Temporal MAE | 0.006480 vs baseline 0.006955 |
| Brooklyn holdout MAE | 0.008337 vs baseline 0.007697 (slightly worse) |
| Top-decile lift | **3.73x** temporal / **1.99x** Brooklyn; baseline 2.72x / 1.70x |
| Deciles | monotonic on both tests |
| Gate | #0002 passed 2026-10-29 |

The model beats the empirical-Bayes baseline on Poisson deviance on both the temporal
test and the Brooklyn spatial holdout. The holdout MAE is slightly worse and the
holdout top-decile lift is 1.99x, just under the 2x label. The gate condition is
recorded as passed; the Method screen shows the exact numbers. Do not claim a 2x
holdout lift.

Features: leakage-safe historical rate (prior years only), 2020 census housing
density per NTA, H3 centroid, borough, hour/weekday/month/weekend/holiday, COVID era,
and optional weather (temperature, rain, wind; `NaN` when missing). Housing density
is the external, non-crash covariate that fixed the Brooklyn transfer.

The metadata `target` string says "per km2 per hour". The serving code treats
`model.predict(x)` as expected crashes per cell-hour and derives the per-km2-hour
area rate by dividing by `area_km2`. The UI and route math use the cell-hour value.
The metadata string is stale; the code is the source of truth.

Scoring:

```text
model 0-60    percentile tiers: <P50 0, P50-P75 15, P75-P90 30,
                               P90-P99 50, >=P99 60
nightlife 0-40  0 bars 0; 1-5 8; 6-13 16; 14-50 28; >50 40
total 0-100     capped at 100
bands           LOW <25, MEDIUM 25-49, HIGH 50-74, CRITICAL >=75
```

Route exposure is distance-linear:

```text
expected_segment = expected_rate * segment_km / sqrt(cell_area_km2)
```

The per-10-km figure is derived from the total and the route length. Duration only
labels the fastest/balanced routes. The route recommendation uses the lowest total
expected injury crashes, not the lowest 0-100 average.

---

## 9. Privacy

The request log schema is coordinate-free:

- `risk_logs` has no `latitude` or `longitude` columns.
- `route_logs` has no origin or destination columns.
- `log_risk` and `log_route` no longer accept coordinates.
- `/stats` returns aggregate counts, the average score, the most dangerous hour, and
  the band breakdown only. There is no `recent_requests` and no
  `highest_risk_location`.
- The old coordinate-bearing tables were dropped and recreated during the privacy
  migration.
- Privacy regression tests cover the schema and the `/stats` response.

The `/stats` endpoint caches for 60 seconds. Clearing the demo DB directly can leave
a stale cached response for up to a minute.

---

## 10. Verification

Commands run for this build:

| Gate | Command | Result |
|---|---|---|
| Types | `npx.cmd tsc -b` | exit 0 |
| Lint | `npm.cmd run lint` | exit 0, 0 errors, 6 warnings |
| Build | `npm.cmd run build` | exit 0, `✓ built in 1.70s` |
| Main JS | `dist/assets/index-B00Elox2.js` | 1,791,219 bytes (1,791.21 kB; gzip 513.33 kB) |
| CSS | `dist/assets/index-BApUmeaP.css` | 123,275 bytes (123.27 kB) |
| Sourcemap | `dist/assets/index-B00Elox2.js.map` | 6,016,586 bytes |
| Contract | `npx.cmd tsx scripts\validate-contract.ts` | **24 fixtures checked, 0 failed** (health_deep skipped: no fixture) |
| Backend | `pytest -q` in `API/MAPS` | **193 passed** in 16.44 s (last full run; backend unchanged since) |

The build prints a chunk-size warning because MapLibre is one large dependency. It is
a warning, not a failure. Sourcemaps are enabled in `vite.config.ts`.

Lint warnings (all non-blocking, 0 errors):

- `DataState.tsx` - fast-refresh only-export-components.
- `TimeDock.tsx` - `Date` purity during render; set-state-in-effect.
- `PresentBar.tsx` - fast-refresh only-export-components (the step constant).
- `PlaceSearch.tsx` - set-state-in-effect (two).

There is no automated Playwright suite in the repository. Browser QA was run
manually through the Playwright MCP against the live dev server.

---

## 11. Browser QA

Verified at 1440x900, 1280x900, 1920x1080, and 390x844:

- Globe intro: full globe, atmosphere rim, red destination dot, no A/B markers at
  globe zoom, replay lands on the route.
- Plan: route cards, `9.1% lower` chip, segment list, segment detail, footer, legend.
- Segment: worst segment selected, detail auto-scrolled, full H3 and midpoint.
- Explore: Times Square CRITICAL 100, model/nightlife breakdown, hourly trend,
  hotspots, nearest safe stop, weather fallback.
- Outside NYC: Newark outside coverage, crash model 0/60, rate `-`, no crash points,
  gray NO MODEL hexes.
- Method: real metrics, version, inputs, "Not in the score".
- Present: seven beats, arrow keys, Escape, zero console errors.
- URL round-trip: 23:00 and 03:00 reload with the same values and route camera.

Zero console errors on every verified screen. One operational caveat: a tab that has
been clicked and navigated many times can retain a stray map point or a stale panel.
The fix is a fresh tab. This is a presenter runbook item, not a product bug observed
on a fresh load.

Screenshots are in `D:\S.O.B.E.R NEW DEMO\docs\screenshots\`:

| File | Screen |
|---|---|
| `01-globe.png` | Globe intro |
| `02-plan-1440.png` | Plan, 1440 |
| `03-segment-1440.png` | Worst segment |
| `04-explore-1440.png` | Explore, Times Square |
| `05-method-1440.png` | Method / model card |
| `06-outside-1440.png` | Outside NYC, Newark |
| `07-mobile-390.png` | Mobile, 390 |
| `08-present-0300.png` | Present, 03:00 |
| `09-present-outside.png` | Present, outside NYC |
| `10-plan-1920.png` | Plan, 1920 |
| `11-plan-1280.png` | Plan, 1280 |
| `12-regional-transition.png` | Regional transition (transitional frame, not a hero) |

---

## 12. Known limitations and honest caveats

- **Mobile 390 px:** the planner is a bottom sheet and covers the lower half of the
  route. The sheet scrolls and the route start is visible, but there is no collapse
  control yet. The globe intro is skipped at this width.
- **Live route provider:** the comparison needs OSRM/FOSSGIS at run time. The backend
  route cache is in-memory and expires after 300 seconds. Restarting the backend
  clears it. Pre-warm both demo hours within five minutes of the run.
- **Weather:** live weather is unavailable without `WEATHER_API_KEY`. The model takes
  its missing-value path and the UI says so. Do not present it as live.
- **Holdout quality:** deviance improves on the Brooklyn holdout; MAE is slightly
  worse and the top-decile lift is 1.99x. The Method screen does not hide this.
- **`/ml/*`:** the legacy severity endpoints remain API-only. The rebuilt UI does not
  surface a severity model or a Trust tab.
- **Unused dependencies:** `leaflet`, `react-leaflet`, and `@types/leaflet` remain in
  `package.json` from the prototype but are not imported. They can be removed in a
  cleanup pass; the current build does not include them.
- **Metadata target string:** says per km2-hour; the serving code and UI use
  cell-hour. Treat the string as stale.
- **Sourcemaps:** enabled for the demo build. They add a 6 MB `.map` file to `dist`.
- **No git:** the only rollback is `D:\S.O.B.E.R NEW DEMO_BACKUP_20261029_090621`.
- **No automated e2e:** browser checks are manual. The backend suite and the contract
  gate are automated.

---

## 13. Reproduce

```powershell
# backend
cd "D:\S.O.B.E.R NEW DEMO\API\MAPS"
pip install -r requirements.txt
& "D:\Python and GIT\python.exe" -u app.py

# frontend
cd "D:\S.O.B.E.R NEW DEMO\app-v2"
npm.cmd install
npm.cmd run dev

# gates
npx.cmd tsc -b
npm.cmd run lint
npm.cmd run build
npx.cmd tsx scripts\validate-contract.ts
```

The demo URL and the seven-beat script are in the root `README.md`.
