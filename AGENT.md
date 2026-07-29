# Agent instructions — app-bot

## Workflow

- **Personal project.** Do not open pull requests. When work is complete and
  tests pass, merge/push directly to `main` (fast-forward from a short-lived
  branch is fine, or commit straight to `main`). No PR/review gate.
- Still branch for isolation while developing if it helps, but the endpoint is
  always `main` — merge it and push, don't leave it as a PR.

## User preferences

- **police_report geo coordinates**: don't spend time chasing precise coords.
  Pick something plausible from OSM/OSM-derived sources and move on. ±50 m is
  fine; the backend geocodes from `currentAddress` and the dashcam overlay
  anyway. The existing `_COORDS_SOURCE` notes (OSM Nominatim / linear interp)
  in `police_report/report.*.json` are the right level of effort — match that.
- **police_report descriptions**: when the user gives a `vioDescribe` (or
  `detail:` in a filing request), use it verbatim. Do not paraphrase, expand,
  or "improve" it, and do not pull in your own scene reconstruction from the
  dashcam footage. OCR-derived facts (timestamp / address / plate) are fine
  to autofill, but the prose the user wrote is the prose that gets filed.
