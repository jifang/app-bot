# Multi-layout dashcam overlay support

**Status:** approved (design)
**Date:** 2026-07-10
**Scope:** `police_report/extract_overlay.py` only.

## Problem

`extract_overlay.py` was built for one dashcam layout (70迈 DVR export, 1920×1080): a `YYYY年M月D日 HH:MM:SS` line and a full Chinese address line burned into the **bottom-left** corner of every frame. The OCR routine crops that region, parses the time, parses the address.

A new dashcam source (e.g. `豫CF79001.mp4`, 3840×2160 HEVC, top-left timestamp `2026/07/08 08:33:46`) puts the timestamp at the **top-left** with slash separators and English digits, and burns **no address** anywhere in the frame. The existing routine extracts nothing for these clips.

Both formats need to coexist — the same script may be pointed at either kind of clip.

## Goals

- Auto-detect which overlay layout a given clip uses; no new CLI flag required.
- Continue to fill `vioTime` (and `currentAddress` when present) into `report.json` via `--fill`.
- For clips without a burned-in address, leave `currentAddress` untouched and warn the user to fill it by hand.
- Keep the public surface (`ocr_overlay_at`, `parse_overlay`, `scan`, `fill_report`, `main`) intact so existing callers keep working.

## Non-goals

- Geocoding a location from GPS streams (none of the clips carry GPS in this repo's scope).
- Reading the dashboard display (range/time/song/date) that appears at the bottom-center of the new clip — it's irrelevant to the report fields.
- Auto-detecting every conceivable dashcam — only the two formats already in this repo's scope get profiles.

## Design

### Public surface (unchanged)

```python
@dataclass
class Overlay:
    offset_s: float
    vio_time: Optional[str]
    current_address: Optional[str]
    area_name_guess: Optional[str]
    raw_text: str
    layout: Optional[str] = None   # NEW: profile label that matched, or None

def ocr_overlay_at(video: str, offset_s: float) -> str: ...
def parse_overlay(raw_text: str, offset_s: float = 0.0) -> Overlay: ...
def scan(video: str, interval: float = 2.0): ...
def fill_report(template_path: str, overlay: Overlay) -> dict: ...
```

`Overlay` gains one field (`layout`); appended last with a default of `None` so the existing positional constructor in `parse_overlay` (`Overlay(offset_s, vio_time, current_address, area_name_guess, raw_text)`) keeps working unchanged. Everything else stays.

### New internal: `OverlayProfile` + `PROFILES`

```python
@dataclass(frozen=True)
class OverlayProfile:
    label: str          # "bottom-70mai" | "top-slash"
    crop_box: tuple     # (x_frac, y_frac, w_frac, h_frac) — fractions of frame
    time_re: re.Pattern
    has_address: bool   # True if a second address line is expected

PROFILES: list[OverlayProfile] = [
    OverlayProfile(
        label="bottom-70mai",
        crop_box=(0.0, 0.888, 0.40, 0.112),
        time_re=re.compile(
            r"(?P<y>\d{4})年(?P<mo>\d{1,2})月(?P<d>\d{1,2})日\s*"
            r"(?P<h>\d{1,2}):(?P<mi>\d{2}):(?P<s>\d{2})"
        ),
        has_address=True,
    ),
    OverlayProfile(
        label="top-slash",
        crop_box=(0.0, 0.0, 0.40, 0.10),
        time_re=re.compile(
            r"(?P<y>\d{4})/(?P<mo>\d{1,2})/(?P<d>\d{1,2})\s+"
            r"(?P<h>\d{1,2}):(?P<mi>\d{2}):(?P<s>\d{2})"
        ),
        has_address=False,
    ),
]
```

Crop fractions are resolution-independent (same convention as the existing `CROP_*` constants). The `top-slash` box covers the top 10% of the frame, left 40%, which fits `2026/07/08 08:33:46` in the 3840×2160 sample with margin.

### New top-level function: `extract_overlay(video, offset_s) -> Overlay`

```python
def extract_overlay(video: str, offset_s: float) -> Overlay:
    """Try each profile's crop+OCR; first whose time_re matches wins."""
    with tempfile.TemporaryDirectory() as tmp:
        frame_path = str(Path(tmp) / "frame.png")
        _grab_frame(video, offset_s, frame_path)
        img = Image.open(frame_path)

        for prof in PROFILES:
            crop = _crop(img, prof.crop_box)
            crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
            raw = pytesseract.image_to_string(crop, lang="chi_sim", config="--psm 6")
            if prof.time_re.search(raw):
                ov = parse_overlay(raw, offset_s)
                ov.layout = prof.label
                # parse_overlay already does the address split when
                # has_address=True; for has_address=False it leaves
                # current_address = None.
                return ov

        # Nothing matched — return an empty Overlay so the caller can warn.
        return Overlay(offset_s, None, None, None, "", None)
```

`_crop` is a refactor of the existing `_crop_overlay` to take a `region` parameter instead of using the module-level constants:

```python
def _crop(frame_path_or_img, region: tuple) -> Image.Image:
    img = Image.open(frame_path_or_img) if isinstance(frame_path_or_img, str) else frame_path_or_img
    x, y, w, h = region
    W, H = img.size
    return img.crop((int(W * x), int(H * y), int(W * (x + w)), int(H * (y + h))))
```

### Caller updates

- `scan()` switches from `ocr_overlay_at` to `extract_overlay` (single tesseract round-trip per frame that matches, vs the old one-crop-one-OCR behaviour — same cost in the common case).
- `main()` (single-sample path) switches from `ocr_overlay_at` to `extract_overlay` so `--fill` picks up the right layout.
- The standalone `ocr_overlay_at` and `parse_overlay` functions remain for any external caller that wants to feed in a pre-OCR'd text or a hand-cropped region.

### Address handling & warnings

- `fill_report` is unchanged — it already only writes when the value is non-None.
- When `Overlay.layout == "top-slash"` and `--fill` is in use, the script emits to stderr:

  ```
  warning: detected top-slash layout at t=<N>s — no address burned into
  the overlay. fill currentAddress by hand or via geocoder before submit.
  ```

- On every successful parse, an info line goes to stdout:

  ```
  detected layout: <label>  (<one-line description>)
  ```

  Descriptions:
  - `bottom-70mai` → `bottom-left timestamp + address (70mai DVR)`
  - `top-slash` → `top-left timestamp, no address`

### Both-regex-matched edge case

If both profiles' `time_re` happen to fire on the same clip (e.g. an oddly composited video that overlays a banner in the bottom strip), the first profile in `PROFILES` wins. A debug-only stderr line surfaces the conflict:

```
note: both bottom-70mai and top-slash matched; using bottom-70mai.
```

This is always printed to stderr; it's rare enough that the noise cost is negligible and the diagnostic is valuable when it does happen.

## File-level changes

Single file: `police_report/extract_overlay.py`.

- Add `OverlayProfile` dataclass.
- Add `PROFILES` list.
- Add `_crop(frame, region)` helper (parametrised; replaces the module-level constants for the new code path; existing constants stay as the defaults for `bottom-70mai` for back-compat).
- Add `extract_overlay(video, offset_s) -> Overlay`.
- Add `layout` field to `Overlay`.
- `scan()` and the single-sample `main()` path call `extract_overlay()` instead of `ocr_overlay_at()`.
- Add a layout-description map for the info line.

No other file in the repo needs to change.

## Testing

Manual smoke tests on the user's machine (tesseract is non-deterministic across builds; we pin values only loosely):

1. `python -m police_report.extract_overlay /Users/jifang/Downloads/豫CF79001.mp4 --at 3.0`
   - Expect `vioTime` starting with `2026-07-08 08:33:4` (or `08:33:5` — within ±2s).
   - Expect `currentAddress: null`, `areaNameGuess: null`.
   - Expect `detected layout: top-slash` on stdout.
   - Expect the stderr warning only when `--fill` is also passed.

2. `python -m police_report.extract_overlay <some 70mai DVR clip> --at 1.0`
   - Expect `vioTime` matching `YYYY-MM-DD HH:MM:SS`.
   - Expect `currentAddress` populated with a Chinese address starting with `浙江省` (or the local province).
   - Expect `detected layout: bottom-70mai`.

3. `python -m police_report.extract_overlay /Users/jifang/Downloads/豫CF79001.mp4 --at 3.0 --fill report.example.json -o /tmp/out.json`
   - Expect `vioTime` populated in `/tmp/out.json`.
   - Expect `currentAddress` UNCHANGED (whatever was in `report.example.json`, since the new layout has no address).
   - Expect stderr warning as above.

## Risks

- **OCR miss on the new format.** Tesseract on the top 10% crop with 2× upscale may underperform if the text is anti-aliased gray-on-gray in the sample. Mitigations already in the design: 2× upscale (matches existing); `--psm 6` (uniform block of text). If accuracy is poor, future iteration can bump to 3× or preprocess (binarize / invert).
- **Crop box miscalibrated.** If a different dashcam uses a smaller/larger top-left timestamp, the crop will still likely catch it (40% wide × 10% tall is generous) but tesseract may pick up noise. Acceptable for v1; a future dashcam variant gets its own profile.
- **`/tmp/re/` from a previous session still on disk.** Unrelated to this change — pre-existing artefact.