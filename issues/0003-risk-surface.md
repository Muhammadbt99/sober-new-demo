# Model served through one consistent risk surface

**Type:** AFK
**Blocked by:** #0002
**PRD:** `PRD.md`

## What to build

Load the approved model at API startup and expose one risk surface used by every endpoint. Replace the hand-tuned crash-density term with percentile-banded model points (0–60) and keep nightlife as the only other component (0–40). Remove additive time and weather points. Rewrite `/risk`, `/heatmap`, `/risk/trend`, `/risk/compare`, `/health`, and `/stats` to use the same model and the same breakdown. Update the contract fixtures. If the artifact is missing or rejected by its gate, the API starts in an explicit `model_unavailable` state.

## Acceptance criteria

- [ ] `/risk` returns expected rate, percentile, model points, nightlife points, total, band, coverage, data quality, model version, and reasons.
- [ ] `/heatmap` values agree with `/risk` for the same cell and hour.
- [ ] `/risk/trend` and `/risk/compare` use the same model surface.
- [ ] No additive night, rush-hour, weekend, or weather points remain.
- [ ] A location cannot reach CRITICAL on model points alone.
- [ ] Outside NYC returns `rule_based_only` with no model points and a warning.
- [ ] `/health` lists all 15 routes and reports model, indexes, and providers separately.
- [ ] `/stats` excludes out-of-area requests.
- [ ] Contract fixtures cover success and error shapes for every changed endpoint.

## User stories addressed

- User story 17
- User story 18
- User story 19
- User story 23
- User story 24
- User story 40
- User story 41
- User story 43
- User story 45
