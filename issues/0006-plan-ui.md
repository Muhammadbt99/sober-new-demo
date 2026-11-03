# Plan and route comparison UI

**Type:** AFK
**Blocked by:** #0004, #0005
**PRD:** `PRD.md`

## What to build

The planning flow: place search with keyboard support, current-location option, departure time picker, route request, fastest vs safest cards, total and per-10-km risk delta, segment selection, and the breakdown panel. Every request has loading, empty, unavailable, and contract-error states. Selecting a segment updates the map and the breakdown without refetching unrelated data.

## Acceptance criteria

- [ ] A user can search a place, choose a result, set a time, and get routes without touching the URL.
- [ ] Fastest and safest routes are visually comparable, with a concrete risk delta.
- [ ] Selecting a segment highlights it on the map and shows rate, percentile, and points.
- [ ] Nightlife contribution is shown separately from the learned crash term.
- [ ] Unavailable weather or routing is visible and does not look like an all-clear.
- [ ] Search works with keyboard only; Escape and Enter behave predictably.
- [ ] No component calls `fetch` directly; all data enters through the query layer and Zod contract.
- [ ] Route results are interactive within 3 seconds on the demo laptop.

## User stories addressed

- User story 11
- User story 12
- User story 13
- User story 14
- User story 15
- User story 16
- User story 17
- User story 18
- User story 19
- User story 20
- User story 21
