# Gates, performance, and final documentation

**Type:** AFK
**Blocked by:** #0006, #0007, #0008
**PRD:** `PRD.md`

## What to build

The quality gates and the final truth pass: Playwright end-to-end test of the golden path, automated accessibility checks on Explore, Plan, and Method, performance budgets, a single local start command that verifies data, model, PMTiles, and ports, and documentation rewritten to match measured reality.

## Acceptance criteria

- [ ] Playwright walks the golden path with zero console errors.
- [ ] Accessibility checks pass on the three main screens with no serious findings.
- [ ] Route results interactive under 3 seconds; heatmap update under 1 second.
- [ ] One command starts backend and frontend after verifying prerequisites.
- [ ] Contract gate runs from a real npm script and validates real JSON.
- [ ] README files match measured routes, metrics, tests, and line counts.
- [ ] Dead code sweep is complete and no orphan artifacts remain.

## User stories addressed

- User story 44
- User story 48
- User story 49
- User story 50
