# S.O.B.E.R. — Product Requirements Document

**Product:** S.O.B.E.R. — Situational Observation Based Emergency Risk System
**Scope:** Full revamp of `S.O.B.E.R NEW DEMO` (frontend rebuild + model-backed risk engine)
**Target:** A judged, live, ~5-minute demonstration on a laptop/projector
**Status:** Approved decisions from the design interview; ready for implementation
**Date:** 2026-10-29

---

## Problem Statement

S.O.B.E.R. plans safer driving routes instead of only faster ones. The current demo has the right idea but cannot carry a judged presentation:

- **The frontend is a stale snapshot.** It is missing the newest search work, its main component is a 1,000-line monolith, and its layout, copy, and states were built incrementally rather than designed.
- **The risk score is hand-tuned.** Crash risk is a set of fixed thresholds over a weighted crash count, mixed with additive night, rush-hour, weekend, and weather points. Different screens can disagree, and the score cannot answer "how much more dangerous is this hour?"
- **The AI model answers the wrong question.** The shipped severity classifier predicts how bad a crash was *after* it happened, using post-crash attributes. It cannot inform a route before a trip, so it is excluded from scoring — leaving the product with no learned risk surface at all.
- **The map and the data are fragile.** The basemap depends entirely on live tile servers, several panels fail without internet or an API key, and failures are not always visually distinct from "all clear."
- **The documentation overclaims.** Route counts, line counts, test counts, and model metrics differ between files, and some fixture files are PNGs with a `.json` extension.

For a judged demo, the result is a product that is hard to explain, easy to break on stage, and weaker than the idea behind it.

## Solution

A ground-up revamp of the demo around one coherent, model-backed risk engine and one designed product surface:

1. **A learned crash-risk model that belongs in the product.** A gradient-boosted Poisson model predicts **expected injury-or-fatal crashes per km² per hour** for an H3 res 8 hex, from pre-trip information only: location, hour-of-week, month, holiday, weather, and a leakage-safe historical rate. It is validated against a shrunken empirical-Bayes baseline on a temporal split and a held-out borough. It replaces the hand-tuned crash-density term.
2. **One score, one explanation.** Risk is `model points (0–60) + nightlife points (0–40)`. Time and weather are owned by the model, not added twice. The 0–100 scale and the LOW/MEDIUM/HIGH/CRITICAL bands stay unchanged, and a location can never be CRITICAL on crash data alone.
3. **Route comparison as the hero.** Every route is scored by integrating the model along its geometry. The app shows **total expected injury crashes** for the trip and a **per-10-km** comparison, plus per-segment colouring. When the router returns fewer than two distinct routes, the planner synthesises detour candidates around the highest-risk hexes and scores them.
4. **A rebuilt frontend.** A persistent map shell with URL-driven state, a dark cinematic product surface, a light editorial methodology section, a real routing/query layer, explicit unavailable states, a responsive layout, and a present mode that drives real interactions.
5. **A demo that survives the room.** A locally bundled NYC PMTiles basemap removes the biggest visual failure mode. Live data stays live; when a source is down, the UI says so plainly and never substitutes a cached or invented number.
6. **Honest diagnostics.** The old severity model is removed from the demo path; the new model gets a model card with measured metrics, calibration, holdout results, and limitations. If the new model fails its gate, the demo ships the existing rule score and reports the negative result.

The result is a product a judge can understand in one sentence — *"it predicts where and when injury crashes happen, then routes around them"* — and a demo that can be run five times in a row without a surprise.

---

## User Stories

### The judged demo

1. As a presenter, I want the app to open on a live risk map of New York at night, so that the first thing judges see is the product working, not a form.
2. As a presenter, I want one visible control that sets a realistic scenario (origin, destination, Saturday 23:00), so that I can start the comparison without typing in front of an audience.
3. As a presenter, I want the fastest and safest routes shown side by side with a concrete difference, so that the value is obvious in under ten seconds.
4. As a presenter, I want to change the departure hour and watch the risk surface change, so that I can demonstrate the model is time-aware rather than static.
5. As a presenter, I want to click the worst segment of a route and see why it is risky, so that the score is explainable rather than a black box.
6. As a presenter, I want a methodology screen with measured numbers and stated limitations, so that I can answer "how do you know?" without improvising.
7. As a presenter, I want every screen to be understandable without narration, so that the demo still works if judges walk up on their own.
8. As a presenter, I want the demo to work when the venue Wi-Fi is poor, so that a network failure does not end the presentation.
9. As a presenter, I want a one-command local start that verifies data, model, and basemap before launch, so that setup cannot silently half-work.
10. As a presenter, I want the app to tell me plainly when a live source is unavailable, so that I never present a fallback as if it were live data.

### The driver

11. As a driver, I want to enter an origin and destination, so that I can plan a trip.
12. As a driver, I want to set a departure time, so that the risk reflects when I will actually drive.
13. As a driver, I want to see the fastest route and the safest route together, so that I can choose the trade-off consciously.
14. As a driver, I want to see the total expected injury crashes for each route, so that the comparison is about harm rather than distance.
15. As a driver, I want a per-10-km comparison, so that a longer route is not unfairly favoured or penalised.
16. As a driver, I want the route drawn in segments coloured by predicted risk, so that I can see where the danger is.
17. As a driver, I want to tap a segment and see its predicted rate and percentile, so that I understand the number.
18. As a driver, I want to see the nightlife density contribution separately, so that I understand why a bar district scores high even when crash data alone is moderate.
19. As a driver, I want a plain-language recommendation (avoid, caution, normal), so that I do not have to interpret the score myself.
20. As a driver, I want to search for a place by name or address, so that I do not need coordinates.
21. As a driver, I want my current location to be an option, so that planning starts where I am.
22. As a driver, I want the map to stay usable while results load, so that the app never feels frozen.
23. As a driver, I want a clear "no data for this area" state outside New York, so that I am not shown a confident score built on nothing.
24. As a driver, I want weather shown as context, so that I know what conditions the prediction assumed.
25. As a driver, I want to see nearby police, hospitals, clinics, and parking, so that I know where help or safety is.
26. As a driver, I want to see current weather alerts for my destination, so that I am not surprised on arrival.
27. As a driver, I want to compare two locations directly, so that I can judge a neighbourhood at a given hour.
28. As a driver, I want a 24-hour risk trend for a location, so that I can choose when to travel.
29. As a driver, I want the interface to work on my phone, so that I can use it in the car.
30. As a driver, I want large, legible type and strong contrast, so that I can read it at a glance.

### The judge or assessor

31. As a judge, I want to know what data the model was trained on, so that I can judge the claim.
32. As a judge, I want to see held-out metrics, including where the model fails, so that I can trust the honesty of the result.
33. As a judge, I want to see the baseline the model had to beat, so that "it works" is not a bare assertion.
34. As a judge, I want to see the model's limitations in plain language, so that I can ask informed questions.
35. As a judge, I want to understand why the severity classifier is not used for routing, so that the exclusion looks deliberate rather than hidden.
36. As a judge, I want every number on screen traceable to a source or a model artifact, so that nothing feels decorative.
37. As a judge, I want the app to distinguish live, stale, and unavailable data, so that I can tell what is actually happening.
38. As a judge, I want the demo to recover gracefully from a failed request, so that reliability is demonstrated, not claimed.

### The fleet or rideshare operator (product story)

39. As a fleet operator, I want routes ranked by expected harm, so that I can set a safety policy instead of a speed policy.
40. As a fleet operator, I want a consistent risk surface across map, route, and trend views, so that drivers and dispatchers see the same answer.
41. As a fleet operator, I want to know when the model is outside its training region, so that I do not apply New York risk to another city.

### The maintainer

42. As a maintainer, I want one module that owns model training, validation, and inference, so that training and serving cannot drift.
43. As a maintainer, I want one pure scoring function, so that score behaviour is testable without a server.
44. As a maintainer, I want one route-planner module with an injectable provider, so that fallback and detour behaviour can be tested offline.
45. As a maintainer, I want a frozen API contract with fixtures, so that frontend and backend can change independently.
46. As a maintainer, I want the model artifact to carry its metrics and feature list, so that a deployed model can be audited after the fact.
47. As a maintainer, I want leakage tests, so that a flattering metric cannot come from future data.
48. As a maintainer, I want one end-to-end test that walks the demo path, so that "the demo works" is a test result.
49. As a maintainer, I want the old rule score retained as a reference baseline until the model passes its gate, so that a failed model does not leave the product without a score.
50. As a maintainer, I want stale documentation and dead code removed, so that the repository describes what it actually does.

---

## Implementation Decisions

### 1. Model: target and data

- **Target:** expected **injury-or-fatal** crashes per km² per hour. Property-damage-only crashes are excluded from the headline target; they remain available for a sensitivity check.
- **Spatial unit:** H3 resolution 8 (roughly 0.7 km² per hex, ~460 m across), covering the NYC bounding box used by the existing product.
- **Temporal unit:** one cell, one date, one hour. The model predicts a rate for a requested cell-hour.
- **Training window:** 2020-01-01 through 2026 (the currently fetched dataset). A COVID-period indicator and year features are included so the 2020–2021 anomaly is modelled rather than ignored.
- **Source:** NYC Open Data collision dataset `h9gi-nx95`, already fetched. Target events: approximately 191k injury crashes and 1,182 fatal crashes in the current window.
- **No post-crash attributes.** Vehicle types and contributing factors are forbidden as features. The model may only use information available before a trip.

### 2. Model: features

- Smoothed historical injury-crash rate for the cell, computed **only from the training period** and out-of-fold with respect to the target year. This is the primary spatial signal and the main leakage risk.
- Latitude and longitude, borough code.
- Hour of day, day of week, hour-of-week encoding, month, weekend flag, holiday flag.
- Weather as a **minor feature**: temperature, rain, wind. Missing weather is passed as a missing value; the model must handle it natively rather than impute a fake value.
- COVID-period flag and year.
- Exposure (hours represented by the row) is handled explicitly so the model predicts a rate, not a raw count.

### 3. Model: training and validation

- **Model class:** gradient-boosted Poisson regression, benchmarked against a shrunken empirical-Bayes cell × hour-of-week lookup table.
- **Temporal split:** train 2020–2024, validate 2025, test 2026.
- **Spatial holdout:** one entire borough (Brooklyn) is withheld from training and used only for evaluation, to test generalisation rather than memorisation.
- **Metrics:** Poisson deviance, MAE, calibration curve, top-decile lift, and a decile monotonicity check (observed injury crash rate must rise with predicted decile).
- **Ship gate:** the model must beat the empirical-Bayes baseline on held-out Poisson deviance for both the temporal test and the borough holdout, and the top decile must show at least a 2× lift over the base rate. If either fails, the demo ships the existing rule score and reports the negative result.
- **Candidate selection:** a small, fixed candidate set (depth/learning-rate/weight variants) compared on validation only; the winner is scored once on test. No test-set tuning.
- **Artifact:** the saved model is accompanied by metadata containing the feature list, training window, split sizes, all measured metrics, baseline metrics, calibration table, percentile reference, and a model version string.

### 4. Model: inference

- The model is loaded at API startup. If the artifact is missing or incompatible, the API starts in an explicit `model_unavailable` state and the UI says so; it never silently falls back to old numbers.
- Predictions are vectorised for heatmap and route requests.
- Percentiles are computed against a precomputed reference distribution of all cells × 168 hour-of-week buckets at typical weather.
- The API reports the expected rate, the percentile, the model version, and the data quality of the weather input.

### 5. Risk score

- **Model component (0–60 points):** percentile tiers over the cell-hour rate: below P50 = 0, P50–P75 = 15, P75–P90 = 30, P90–P99 = 50, P99+ = 60.
- **Nightlife component (0–40 points):** venue tiers within 1 km, using the existing calibrated bands: at or below P50 (2 venues) = 0, up to P75 (13) = 15, up to P90 (50) = 30, above P90 = 40.
- **Total:** `min(100, model + nightlife)`. The 0–100 scale and existing bands are unchanged: LOW < 25, MEDIUM 25–49, HIGH 50–74, CRITICAL ≥ 75.
- **Safety property:** model points alone max out at 60, so no location is CRITICAL on crash data alone; nightlife exposure must co-occur.
- **Removed:** additive night, rush-hour, weekend, rain, wind, and heat points. Those effects now live inside the model. Weather and time remain visible as context.
- **Breakdown:** the response returns model points, expected rate, percentile, nightlife points and venue count, total, band, coverage, data quality, model version, and reasons. Reasons are generated from the same numbers, not duplicated logic.
- **Outside the model area:** `coverage: rule_based_only`, no crash-model points, a plain warning, and no confident band claim.

### 6. API contract

- `/risk` returns the new breakdown, expected rate, percentile, model version, coverage, and warnings.
- `/heatmap` returns per-hex expected rate, percentile, points, and band from the same model surface.
- `/safe_route` returns, for each route: geometry, duration, distance, total expected injury crashes, expected crashes per 10 km, per-segment rate and colour bucket, provider name, and whether the route was synthesised as a detour.
- `/risk/trend`, `/risk/compare`, and `/hotspots` use the same model surface for crash risk so no two views can disagree.
- `/nearest_safe`, `/alerts`, `/alerts/check_location`, and `/stats` keep their current behaviour, with `/stats` filtered to the model area.
- `/health` advertises all 15 routes and reports model status, data-index status, and each external provider separately.
- All responses carry a schema version and, where relevant, a model version.
- Parameter errors stay JSON 400s; route scoring keeps its deadline and returns 504 rather than blocking a worker.

### 7. Route planning

- **Exposure integration:** sample route geometry at a fixed spacing, map each sample to its H3 cell, look up the cell-hour rate, and integrate along the route. Expected crashes use a stated corridor width and the route's speed; the absolute value is an estimate and the comparison between routes is the product.
- **Headline:** total expected injury crashes for the trip. **Secondary:** expected crashes per 10 km.
- **Segment colouring:** each segment is bucketed by its cell-hour percentile, using the same tiers as the score.
- **Alternative generation:** request alternatives from the existing provider chain, then add road-snapped alternatives by routing through waypoints placed beside the fastest route (the flanks of its highest-risk stretch first). A waypoint route is kept only if it is a genuinely different road, is not absurdly longer than the fastest route, and does not drive out and back along itself; every candidate is scored on the same model surface and the response reports what was tried and dropped (`via_alternatives`). Only if the provider returned fewer than two distinct routes and no waypoint route survived does the planner fall back to synthesising up to three offset detour candidates around the highest-risk hex.
- **Selection:** the fastest route is always shown; the safest route is the candidate with the lowest total expected crashes. If no candidate improves on the fastest route, the app says so rather than inventing a win.
- **Fallbacks:** provider timeout, no route, and geometry errors produce explicit states; the planner never returns a route with missing geometry as if it were valid.

### 8. Frontend architecture

- **Stack:** Vite 8, React 19, TypeScript 6 with strict and `noUncheckedIndexedAccess`, Tailwind 4, shadcn/Radix primitives, MapLibre GL 6, React Router 7, TanStack Query, Motion, Zod.
- **Shell:** one persistent map shell. Panels are routes over it and the map never unmounts. URL state carries origin, destination, hour, day, scenario, and panel, so every view is shareable and reload-safe.
- **Screens/panels:** Explore (risk surface, hotspots, time scrubber), Plan (search, time, route comparison, breakdown), Method (model card, data provenance, limitations, demo notes).
- **Present mode:** a toggle that drives real UI state (a named scenario) through the same URL state as a human interaction. It does not fabricate data or bypass the API.
- **Data layer:** one query module per endpoint, with explicit loading, empty, unavailable, and stale states. No component calls `fetch` directly.
- **Contract:** Zod schemas are the only way API data enters the app; parse failures surface as an explicit contract error, not as undefined behaviour.
- **Map module:** a single deep component owns PMTiles basemap, hex risk layer, route layers, segment colouring, selection, and fit-bounds. Parents pass data and receive selection events.
- **State:** server data lives in the query cache; UI state lives in the URL; no duplicated derived state.
- **Performance:** route results interactive under 3 seconds on the demo laptop; heatmap updates under 1 second; the map stays interactive during fetches; the map bundle is lazily loaded.

### 9. Visual design

- **Two moods:** dark, cinematic map/planner surface; light, editorial methodology surface. The transition is deliberate, not a theme toggle for its own sake.
- **Palette:** near-black neutral base; amber for caution, red for high risk, teal/green for safe; risk colour is never the only signal (labels and patterns accompany it).
- **Typography:** a locally bundled grotesk for UI and a mono for numbers/coordinates; no runtime font network dependency. Large type on the projected layout.
- **Motion:** restrained — map fly-to, panel transitions, number changes. No animation that delays interaction.
- **Accessibility:** WCAG AA contrast on both surfaces, visible focus, keyboard-operable panels, map data also available as text lists (routes, segments, breakdown), reduced-motion support.
- **Responsive:** designed for 16:9 at 1280–1920 px; collapses to a single-column mobile layout with the map staying primary.

### 10. Basemap and availability

- Bundle a local NYC PMTiles vector basemap generated from an OSM extract; use it as the primary style.
- Keep live tiles as an optional override, never as a requirement.
- Route, weather, geocoding, alerts, and risk data remain live. No cached or synthetic risk values are ever shown as if they were live.
- When a live source fails, the UI shows an explicit unavailable state for that source and keeps the rest of the app usable. The last successful value is not presented as current.
- The map itself remains fully functional offline.

### 11. Privacy, data, and housekeeping

- Remove the shipped request-history database rows (688 geolocated entries). Do not log coordinates in the demo build.
- No API keys in the folder. Weather is optional; the app runs without it and reports the missing input.
- Remove dead endpoints' UI entry points, dead modules, orphan calibration/metadata files with no reader, and duplicated constants.
- Replace the two PNG files masquerading as JSON fixtures; the contract gate must validate real JSON.
- Rewrite the root README and the frontend README to match reality: 15 routes, real model metrics, real gates, real line counts.
- Add a single local start command that checks data, model, PMTiles, and ports before launching backend and frontend.

### 12. Build order

1. Model: dataset, training, validation, artifact, gate result.
2. Backend: inference module, scoring module, route planner, API contract, tests.
3. Contract freeze: publish fixtures for every endpoint, validate them.
4. Frontend: shell, map, planner, insights, method, design system.
5. E2E, accessibility, performance, documentation.

### 13. Demo script (target 5 minutes)

- **0:00** Map opens on nighttime NYC, risk hexes visible, time scrubber at the current hour.
- **0:45** Present mode sets Times Square → a Brooklyn destination for Saturday 23:00.
- **1:15** Fastest and safest routes appear; read the total expected injury crashes and per-10-km difference.
- **2:15** Click the worst segment; show predicted rate, percentile, and the nightlife contribution.
- **3:00** Move the hour to 03:00; the risk surface and route recommendation change.
- **3:45** Method screen: data sources, model card, holdout results, baseline comparison, limitations.
- **4:30** Close on the "works anywhere, honest outside NYC" state.

---

## Testing Decisions

A good test checks externally observable behaviour: a score for an input, a route for a provider response, a contract for a payload. Tests must not assert private helpers, internal call order, or exact implementation details that a reasonable refactor would change. Fixtures are real JSON; no binary files with renamed extensions.

### Backend

- **Crash model**
  - Leakage: the historical-rate feature for a target year must not include that year's crashes; a deliberately leaky build must fail the test.
  - Split integrity: temporal and borough holdouts are disjoint from training; the artifact records the exact split.
  - Gate behaviour: a model that fails the deviance or lift gate is rejected and the API reports `model_unavailable` rather than serving it.
  - Artifact round-trip: save, reload, and produce identical predictions.
  - Missing weather: predictions succeed with missing weather and the response marks weather unavailable.
  - Calibration and decile monotonicity on held-out data.
- **Risk score**
  - Pure function tests for tier boundaries, the 60-point model cap, the 40-point nightlife cap, and the 100-point total.
  - A location cannot be CRITICAL from model points alone.
  - Breakdown values sum to the total; reasons are consistent with the breakdown.
  - Outside the model area: `rule_based_only` coverage, no model points, explicit warning.
- **Route planner**
  - Fake provider returns two routes: the lower-exposure route is selected as safest.
  - Fake provider returns one route: detour synthesis is attempted and the synthesised route is scored.
  - Detour never worsens the reported safest route; if it does, the fastest route is kept and the response says no improvement.
  - Provider timeout and empty geometry produce explicit failures, not partial routes.
  - Exposure integration is linear in route length for a constant rate.
- **API**
  - Contract fixtures for every route in both success and error shapes.
  - `/health` lists all routes; `/stats` excludes out-of-area requests; error responses remain JSON 400/404/504.
  - Rate limits and deadlines are exercised without live network.

### Frontend

- **Contract:** every fixture parses; malformed payloads produce a contract error.
- **Pure logic:** risk-delta math, per-10-km formatting, segment colour bucket boundaries, URL state serialisation and round-trip, hour/day conversion.
- **Data layer:** loading, unavailable, and stale states render distinctly (mock server, no live network).
- **End-to-end (Playwright):** the exact demo path — open map, apply present scenario, run comparison, open the worst segment, change the hour, open the method screen — with zero console errors.
- **Accessibility:** automated checks on Explore, Plan, and Method; keyboard path through the golden flow; contrast assertions on both themes.
- **Not included:** visual regression, cross-browser matrix, load testing.

### Test infrastructure

- Backend tests run offline; network clients are stubbed at the boundary, and no test starts a background poller that calls a live API at import.
- The E2E test starts the real backend with a committed validation model artifact and a local PMTiles fixture, and uses recorded HTTP fixtures for weather/geocoding/routing so it is deterministic.
- The contract gate is a real script, referenced by a real npm script.

---

## Out of Scope

- The four vision prototypes (`sleeping-model`, `phone-detection`, `seatbelt-detection`, `drunk_detection`). They stay untouched and are documented as prototypes.
- Integrating live camera monitoring into the demo.
- Crash data or risk calibration for cities outside NYC, including the UAE. Outside NYC the app is explicitly rule-based only.
- Self-hosted OSRM, GraphHopper, or OpenRouteService keys. The existing provider chain and detour synthesis are sufficient.
- Authentication, user accounts, saved trips, payments, fleet dashboards, and multi-user persistence.
- Public hosting, CI/CD, and production observability. The deliverable is a local judged demo.
- The old static `website/` frontend. It is not part of this folder.
- The old severity classifier in the main flow. It may remain as an appendix or be removed from the UI; it is not part of the risk score.
- Mobile-native apps and offline routing data. Only the basemap is offline.
- Git history or remote pushes. The folder is a local test environment; timestamped backups are taken before destructive changes.

---

## Further Notes

### Contingency

The current rule-based score remains in the codebase as a reference baseline until the new model passes its gate. If it fails, the demo ships the reference score, the Method screen reports the negative result, and the revamp still delivers the rebuilt frontend, the consistent contract, the local basemap, and the route comparison. A failed model is a finding, not a reason to fabricate a win.

### Risks

- **Model gate failure.** Mitigated by the reference-score contingency.
- **PMTiles size and generation time.** Mitigated by limiting the extract to NYC and city-level zoom.
- **Route provider returning duplicate or nonsensical alternatives.** Mitigated by deduplication, detour synthesis, and a "no improvement" state.
- **Poisson rates being small and hard to display.** Mitigated by showing total and per-10-km with fixed precision and explaining the unit.
- **Projector contrast.** Mitigated by the two-surface design and contrast checks.
- **Weather key absent.** The model accepts missing weather; the UI marks it unavailable.

### Definition of done

1. The model passes its gate, or the fallback is explicitly active and documented.
2. Every screen reads from the same model surface; no view uses the old density thresholds.
3. The golden path passes as an automated E2E test.
4. The app runs with no API key and no internet except for live data panels, which fail visibly and honestly.
5. The map works offline from the local PMTiles basemap.
6. The privacy cleanup is complete and no real request history ships.
7. Documentation matches measured reality: routes, metrics, tests, line counts.
8. The 5-minute demo has been rehearsed end to end at least twice without a manual fix.

### Explicit non-goals for "polish"

Polish means: coherent design, consistent numbers, honest states, fast interactions, readable type, working keyboard and screen-reader basics, and no dead UI. It does not mean: a full design system, a marketing site, animations everywhere, or a visual-regression suite.
