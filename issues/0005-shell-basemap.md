# Shell, basemap, and live risk surface

**Type:** HITL (design checkpoint)
**Blocked by:** #0003
**PRD:** `PRD.md`

## What to build

The new application shell: persistent map, URL-driven state, design tokens, responsive layout, and a locally bundled NYC PMTiles basemap that works without internet. Render the model's risk hexes, a time scrubber, hotspot markers, and the map selection events that the planner and insights panels consume. Keep the map as one deep module that receives data and emits selection; no feature component talks to MapLibre directly.

## Acceptance criteria

- [ ] The map renders from local PMTiles with networking disabled.
- [ ] Hex colours change when the hour changes and match the API surface.
- [ ] URL state round-trips origin, destination, hour, day, panel, and scenario.
- [ ] The map stays interactive during data fetches.
- [ ] The layout works at 1280, 1920, and 390 px widths without horizontal overflow.
- [ ] Dark product surface and light method surface pass contrast checks.
- [ ] Keyboard focus is visible and panels are operable without a mouse.
- [ ] Design checkpoint approved before feature panels are layered on.

## User stories addressed

- User story 1
- User story 4
- User story 22
- User story 29
- User story 30
- User story 37
