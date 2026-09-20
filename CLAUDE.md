# CLAUDE.md

Agent instructions for this repo. `AGENT.md` is the canonical, full set of
workflow rules (personal project: work is merged/pushed straight to `main`,
no PRs). The rules below are called out here because they have been repeated
by the user and must not be violated.

## police_report — hard rules

- **No screenshots / no plate-frame extraction.** When filing a traffic
  violation report, if the user does **NOT** provide a license-plate image,
  file the report **video-only** (`picList: []`). Do **NOT** extract or crop
  a frame from the video to make a plate image, do **NOT** run plate OCR on
  the video, and do **NOT** hunt for a "clear plate" frame. This applies even
  though an older note elsewhere said to grab one plate frame — that is
  withdrawn. The police verify the plate against the video themselves; many
  prior reports were accepted video-only.
- **Do not verify or reconstruct the violation from the video.** Take the
  user's facts (plate from filename/request, type, location, detail) at face
  value. Do not inspect frames to check the indicator, time the lane change,
  confirm the plate, etc. Speed matters — do not over-analyze.
- **Use the user's `detail` prose verbatim** as `vioDescribe`; do not
  paraphrase, expand, or pull scene reconstruction from the footage.
- If the user provides an image, upload it directly for `picList` — never
  crop or re-extract.
- Mechanical/technical processing is still expected (these are NOT
  "verification"): re-mux with `+faststart`, and keep every video under
  40 MB (re-encode/trim/speed-up non-critical sections if needed).
- OCR autofill is allowed **only** for facts the user did not give — reading
  the dashcam overlay (timestamp, burned-in address) to fill `vioTime` /
  `currentAddress`. That OCR reads the overlay text only; it is not a
  license to inspect the scene or the plate.
- After a report is accepted, move submitted clip(s)/image(s) to
  `camera/reported/` and the raw DVR source to `camera/raw/`.

See `AGENT.md` for the complete workflow.
