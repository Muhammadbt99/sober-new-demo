# Route comparison engine

**Type:** AFK
**Blocked by:** #0003
**PRD:** `PRD.md`

## What to build

A route planner that scores geometry against the model surface. Integrate expected injury crashes along each route, report total for the trip and expected crashes per 10 km, and bucket each segment by cell-hour percentile. Request alternatives from the existing provider chain; when fewer than two distinct routes exist, synthesise up to three detour candidates around the highest-risk hex on the fastest route and score them. The safest route is the candidate with the lowest total; if none improves on the fastest route, say so. Keep timeouts and provider fallbacks explicit.

## Acceptance criteria

- [ ] `/safe_route` returns total expected injury crashes and per-10-km values for every route.
- [ ] Every segment carries its cell rate and colour bucket.
- [ ] Two-route fake provider selects the lower-exposure route as safest.
- [ ] One-route fake provider triggers detour synthesis and scores the result.
- [ ] A detour that does not improve is not presented as an improvement.
- [ ] Provider timeout, empty geometry, and no-route cases produce explicit errors.
- [ ] Exposure integration is linear in route length for a constant rate.
- [ ] The planner is testable with an injected fake provider and no network.

## User stories addressed

- User story 13
- User story 14
- User story 15
- User story 16
- User story 40
