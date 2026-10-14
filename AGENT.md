# Agent instructions — app-bot

Single source of truth for all agents (`CLAUDE.md` is a symlink to this file).

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
  in `police_report/reports/report.*.json` are the right level of effort — match that.
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
  Technical processing still applies: size check
  (re-encode/trim to fit the 40 MB limit); no faststart re-mux needed. OCR-autofill is limited to
  facts the user did NOT give (e.g. reading the overlay timestamp /
  burned-in address for `vioTime` / `currentAddress` when absent) — that
  OCR reads the overlay only, and is NOT a license to inspect the scene
  or the plate. Do not "verify" the video beyond these mechanical steps.
- **police_report location precedence**: if the user explicitly types a
  `vioLoc` in a filing request, always use it for `vioAddress` (and
  `currentAddress`, unless a specific street-level overlay address for that
  same corridor is already an established convention from a prior accepted
  report — see e.g. `report.2026-09-20_A93RG3.json`'s 云河街108号-8 /
  彩虹快速路/之江大桥 pairing). Do not silently substitute the dashcam
  overlay's address instead, even if the overlay reads a different street or
  district — when the overlay looks like a genuine mismatch (different city,
  clearly unrelated corridor), flag it once, but if the user says to use
  their own stated location, that decision stands for this and future
  filings, not just the one being asked about.
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
  under **40 MB**. If the source is too large, trim or speed up non-critical
  sections with `scripts/speedup_section.sh`, or re-encode it. Do **not**
  do a separate `+faststart` re-mux (no longer required). The Hangzhou police
  web player fails to open videos that exceed the size limit.
- **police_report clip start timing**: when extracting a violation clip from
  raw DVR footage, the clip MUST begin *before* the violation maneuver starts.
  For a lane-change violation, show the vehicle fully in its original lane
  first — do not start on a frame where the vehicle is already crossing the
  lane line. Begin at least 2-3 seconds before the maneuver so reviewers can
  see the baseline position and the absence of a turn signal leading up to it.
- **police_report file organization**: after a report is submitted, always
  move **both** the submitted clip(s) *and* the plate image(s) (when an image
  was filed) to `camera/reported/` — never move just the video and leave the
  image behind, or vice versa. This is `camera/reported/`, not the
  `police_report/reports/` folder (that one holds the JSON report records,
  not media). Do **not** worry about `camera/raw/` — don't
  move or manage raw/original source files (user instruction); only archive the
  submitted clip(s)/image(s) to `camera/reported/`. **Never overwrite an existing file in
  `camera/reported/`** — a second report can reuse the same plate (e.g. two
  separate incidents for the same car), and a plain `mv` onto an
  already-occupied filename silently destroys the earlier submission's
  archived clip/image. Before moving, check whether the destination path
  already exists; if it does, append a disambiguating suffix (e.g. `_2`,
  or the report date) to the incoming file instead of clobbering it.

- **police_report authorization & dry-run check**: an explicit request to
  submit a traffic-violation report authorizes uploading the evidence and
  filing it. Approximate coordinates inferred from the overlay or the user's
  location are fine — don't stop to ask for approval. Keep the report factual;
  don't invent unsupported details. When media is supplied, never run a
  `--confirm` submission until the uploaded file IDs appear in `videoList`
  and/or `picList` in the dry-run payload; afterwards verify the history
  record lists the attachments.

## Shanghai traffic-report video exception

- For submissions through `police_report_sh/`, preserve original plate
  legibility: do not downscale a clear source to 720p, trim, speed up, splice,
  or transcode it to fit the portal's suggested duration/resolution. The user
  prioritizes a readable plate over those portal suggestions.
- The Shanghai portal currently asks for at most 1 minute and 1280×720, and
  warns that edited videos may not be adopted. If the user explicitly asks to
  submit an original that exceeds the duration/resolution guidance, retain and
  upload that original unchanged, warn that the portal may reject it, and
  report the actual result. Keep the 100 MB upload-size limit.
