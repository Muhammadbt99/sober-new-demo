# Method screen and honest states

**Type:** AFK
**Blocked by:** #0003, #0005
**PRD:** `PRD.md`

## What to build

The light editorial methodology surface: model card, training window, feature list, holdout metrics, baseline comparison, calibration and failure modes, data provenance, and limitations in plain language. Explain why the severity classifier is excluded from routing. Implement the shared live/stale/unavailable vocabulary used across the app, and the explicit outside-NYC state.

## Acceptance criteria

- [ ] Every metric on screen matches the model artifact metadata exactly.
- [ ] Baseline and model are shown side by side; the gate verdict is stated.
- [ ] Limitations are written in plain language, not buried.
- [ ] Live, stale, and unavailable are visually and verbally distinct everywhere.
- [ ] Outside NYC shows `rule_based_only`, no crash points, and no confident band.
- [ ] The page is readable on a projector and on a phone.
- [ ] No number appears without a source or an artifact reference.

## User stories addressed

- User story 5
- User story 6
- User story 31
- User story 32
- User story 33
- User story 34
- User story 35
- User story 36
- User story 37
- User story 38
- User story 41
