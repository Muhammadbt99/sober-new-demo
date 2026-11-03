# Crash-risk model with a ship gate

**Type:** HITL (gate review)
**Blocked by:** none
**PRD:** `PRD.md`

## What to build

A trained model that predicts expected injury-or-fatal crashes per km² per hour for an H3 res 8 hex, using only pre-trip information. Data is the NYC Open Data collision dataset from 2020 onward; property-damage-only crashes are excluded from the target. Weather is a minor feature and missing weather must be accepted as a missing value. The historical cell rate must be computed from the training period only, out-of-fold with respect to the target year. Validate on a temporal split (train 2020–2024, validate 2025, test 2026) and a withheld borough (Brooklyn), against a shrunken empirical-Bayes cell × hour-of-week baseline. Write the model artifact and its metadata: feature list, window, split sizes, metrics, baseline metrics, calibration table, percentile reference, version.

## Acceptance criteria

- [ ] Training is reproducible from the committed data and a documented command.
- [ ] A leakage test proves the historical-rate feature cannot see the target period.
- [ ] Held-out Poisson deviance beats the empirical-Bayes baseline on both temporal test and borough holdout.
- [ ] Top predicted decile shows at least 2× the base injury-crash rate, with monotonic deciles.
- [ ] Calibration is measured and reported, including where it is distorted.
- [ ] Missing weather produces valid predictions, marked as such.
- [ ] Artifact metadata contains everything needed to audit the model after the fact.
- [ ] A written gate verdict says pass or fail; a fail is reported rather than hidden.

## User stories addressed

- User story 31
- User story 32
- User story 33
- User story 42
- User story 47
