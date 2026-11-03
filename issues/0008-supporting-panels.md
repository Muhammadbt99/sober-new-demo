# Supporting panels

**Type:** AFK
**Blocked by:** #0005, #0006
**PRD:** `PRD.md`

## What to build

Wire the remaining product surfaces without letting them disagree with the main risk score: nearest safe places, weather alerts and destination checks, historical hotspots, city stats, location comparison, and the 24-hour trend detail. Each panel has explicit loading, empty, and unavailable states and reads risk through the same contract.

## Acceptance criteria

- [ ] Nearest safe places works from the offline index and labels the Overpass fallback.
- [ ] Alerts distinguish a failed lookup from an all-clear.
- [ ] Hotspots are labelled as historical crash locations, not live incidents.
- [ ] City stats are scoped to the model area.
- [ ] Compare and trend use the model surface, not a second calculation.
- [ ] Every panel has an empty and an unavailable state.
- [ ] No panel makes a network call that the user cannot see failing.

## User stories addressed

- User story 25
- User story 26
- User story 27
- User story 28
