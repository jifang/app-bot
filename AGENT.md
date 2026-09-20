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
- **police_report no frame analysis**: when the user provides explicit
  violation details (type / location / detail) in a filing request, take
  them at face value. Do not extract or inspect video frames to verify or
  reconstruct the violation (e.g. checking whether an indicator lit, timing
  the lane change, etc.). **Speed matters — do not over-analyze.** Never
  verify user-supplied facts against the video: the plate (from the
  filename / request) and the location/address are trusted as given.
  **Never extract or crop frames from the video to make a plate image —
  not even when the user gave no image.** If the user provides an image,
  upload it directly for `picList`; if they provide NO image, file the
  report **video-only** (`picList: []`) — do NOT screenshot, grab a plate
  frame, run plate OCR on the video, or otherwise hunt for a readable
  plate. The police verify the plate against the video themselves; a
  video-only report is fine (many prior reports were filed that way).
  Technical processing still applies: faststart re-mux, size check
  (re-encode/trim to fit the 40 MB limit). OCR-autofill is limited to
  facts the user did NOT give (e.g. reading the overlay timestamp /
  burned-in address for `vioTime` / `currentAddress` when absent) — that
  OCR reads the overlay only, and is NOT a license to inspect the scene
  or the plate. Do not "verify" the video beyond these mechanical steps.
- **police_report plate prefix**: never question or "correct" the plate
  prefix the user provides. 浙M is a valid Hangzhou new-energy plate series
  (newly added) — do not assume it must be 浙A, and do not warn that a
  prefix is "illegal". File exactly the plate string the user gives; the
  police verify against the video themselves.
- **police_report filing flow**: when the user asks to "file report" and
  provides local media, upload the media during the same task and write the
  returned ids into `videoList` / `picList`. Do not stop at a draft JSON
  unless the user explicitly asks for draft-only or upload/auth fails.
- **police_report video constraints**: before uploading, ensure every video is
  (1) under **40 MB**, and (2) has the `moov` atom at the **start** of the
  file (web-friendly / "fast start"). If the source is too large, trim or
  speed up non-critical sections with `scripts/speedup_section.sh`, then
  re-mux with `ffmpeg -i in.mp4 -c copy -movflags +faststart out.mp4` to
  relocate the metadata. The Hangzhou police web player fails to open videos
  that exceed the size limit or lack a leading `moov`.
- **police_report clip start timing**: when extracting a violation clip from
  raw DVR footage, the clip MUST begin *before* the violation maneuver starts.
  For a lane-change violation, show the vehicle fully in its original lane
  first — do not start on a frame where the vehicle is already crossing the
  lane line. Begin at least 2-3 seconds before the maneuver so reviewers can
  see the baseline position and the absence of a turn signal leading up to it.
- **police_report file organization**: after a report is submitted, move the
  submitted clip(s) and plate image(s) to `camera/report/`. Move the original
  raw DVR source file to `camera/raw/` once processing is complete.
