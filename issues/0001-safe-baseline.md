# Safe baseline and honest inventory

**Type:** AFK
**Blocked by:** none
**PRD:** `PRD.md`

## What to build

Make the folder safe and truthful before any feature work. Take a timestamped backup excluding `node_modules/` and `dist/`. Remove the shipped request-history rows from the SQLite database and stop logging coordinates in the demo build. Delete dead modules, unused wrappers, and orphan calibration/metadata files with no reader. Replace the two PNG files named as JSON fixtures. Correct the documentation so route counts, line counts, test counts, and model metrics match measured reality. Confirm the app still starts after the cleanup.

## Acceptance criteria

- [ ] A timestamped backup exists outside the source tree.
- [ ] The shipped request-history database contains no geolocated rows.
- [ ] No API keys or secrets exist anywhere in the folder.
- [ ] Dead modules and orphan artifacts identified by the audit are removed or wired up.
- [ ] Contract fixtures are real JSON, not renamed binaries.
- [ ] Documentation states 15 routes, real model metrics, and real gate commands.
- [ ] The existing app still starts and serves a request after cleanup.

## User stories addressed

- User story 46
- User story 50
