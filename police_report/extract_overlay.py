"""
OCR the dashcam on-screen overlay (date/time + address burned into a corner of
the frame) and turn it into `vioTime` / `currentAddress` for report.json.

Supports multiple dashcam layouts through `PROFILES` — see
`docs/superpowers/specs/2026-07-10-multi-layout-overlay-design.md`. The two
currently-recognised layouts are:

- `bottom-70mai`: bottom-left `YYYY年M月D日 HH:MM:SS` timestamp + full Chinese
  address line burned in (70迈 DVR export).
- `top-slash`: top-left `YYYY/M/D HH:MM:SS` timestamp only, no address burned
  in (e.g. Tesla-style HUD reflection or other modern cameras that emit a
  top-banner overlay).

Requires:
  brew install tesseract tesseract-lang      # ships chi_sim traineddata
  pip install pytesseract pillow

Examples:
  python -m police_report.extract_overlay DVR_2026-07-08_08-20-42_Front.mov
  python -m police_report.extract_overlay clip.mov --at 6.5
  python -m police_report.extract_overlay clip.mov --scan --interval 2
  python -m police_report.extract_overlay clip.mov --at 6.5 \
      --fill report.example.json -o report.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image
import pytesseract

# Back-compat: original module-level crop fractions for the 70mai bottom-left
# layout. External callers that imported these constants (none in this repo,
# but kept for safety) still work; the actual matching goes through PROFILES.
CROP_X_FRAC = 0.0
CROP_Y_FRAC = 0.888
CROP_W_FRAC = 0.40
CROP_H_FRAC = 0.112

_DATE_TIME_RE = re.compile(
    r"(?P<y>\d{4})年(?P<mo>\d{1,2})月(?P<d>\d{1,2})日\s*(?P<h>\d{1,2}):(?P<mi>\d{2}):(?P<s>\d{2})"
)
_ADDR_SPLIT_RE = re.compile(r"^(?P<province>.+?省)?(?P<city>.+?市)(?P<district>.+?[区县])")


# ----------------------------------------------------------------------------
# Overlay profiles (see docs/superpowers/specs/2026-07-10-multi-layout-overlay-design.md)
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class OverlayProfile:
    label: str           # "bottom-70mai" | "top-slash"
    crop_box: tuple      # (x_frac, y_frac, w_frac, h_frac) — fractions of frame
    time_re: "re.Pattern[str]"
    has_address: bool    # True if a second address line is expected on the overlay
    lang: str = "chi_sim"  # tesseract language model
    psm: int = 6           # tesseract page-segmentation mode


PROFILES: list[OverlayProfile] = [
    OverlayProfile(
        label="bottom-70mai",
        crop_box=(CROP_X_FRAC, CROP_Y_FRAC, CROP_W_FRAC, CROP_H_FRAC),
        time_re=_DATE_TIME_RE,
        has_address=True,
        lang="chi_sim",
        psm=6,
    ),
    OverlayProfile(
        label="top-slash",
        # top-left, full width × top 10%. 0.40 wide × 0.10 tall covers the
        # `YYYY/M/D HH:MM:SS` timestamp burned into the HUD banner on Tesla-style
        # and similar modern cameras with margin to spare.
        crop_box=(0.0, 0.0, 0.40, 0.10),
        time_re=re.compile(
            r"(?P<y>\d{4})/(?P<mo>\d{1,2})/(?P<d>\d{1,2})\s+"
            r"(?P<h>\d{1,2}):(?P<mi>\d{2}):(?P<s>\d{2})"
        ),
        has_address=False,
        # The top-slash overlay is pure ASCII digits/slashes/colons — chi_sim
        # mis-recognises the slashes as Chinese radicals ("V" / "¥"). eng gives
        # noticeably cleaner reads on this format.
        lang="eng",
        psm=6,
    ),
] 

_LAYOUT_HINT: dict[str, str] = {
    "bottom-70mai": "bottom-left timestamp + address (70mai DVR)",
    "top-slash":    "top-left timestamp, no address",
}


# ----------------------------------------------------------------------------
@dataclass
class Overlay:
    offset_s: float
    vio_time: Optional[str]            # "YYYY-MM-DD HH:MM:SS"
    current_address: Optional[str]
    area_name_guess: Optional[str]     # e.g. "杭州市西湖区"
    raw_text: str
    layout: Optional[str] = None      # NEW: matched profile label, or None


# ----------------------------------------------------------------------------
# Frame grabbing
# ----------------------------------------------------------------------------
class _FrameGrabError(RuntimeError):
    """Raised when ffmpeg seek lands at/past EOF — not a hard ffmpeg failure."""


def _probe_duration(video: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _grab_frame(video: str, offset_s: float, out_path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(offset_s), "-i", video,
         "-frames:v", "1", "-update", "1", out_path],
        capture_output=True, check=True,
    )
    # ffmpeg exits 0 even when the seek lands at/past EOF and writes nothing.
    # Treat "no output file produced" as a miss so callers see a clean return
    # instead of a downstream FileNotFoundError.
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        if os.path.exists(out_path):
            os.remove(out_path)
        raise _FrameGrabError(
            f"ffmpeg produced no output for {video} @ {offset_s}s "
            f"(EOF or out-of-range seek)"
        )


def _crop(frame_path_or_img, region: tuple) -> Image.Image:
    """Crop `region` (x_frac, y_frac, w_frac, h_frac) out of a frame file or
    already-open PIL.Image. Returns the cropped image."""
    img = (Image.open(frame_path_or_img)
           if isinstance(frame_path_or_img, str)
           else frame_path_or_img)
    x, y, w, h = region
    W, H = img.size
    return img.crop((int(W * x), int(H * y), int(W * (x + w)), int(H * (y + h))))


# ----------------------------------------------------------------------------
# OCR + parsing
# ----------------------------------------------------------------------------
def ocr_overlay_at(video: str, offset_s: float) -> str:
    """Back-compat single-crop OCR. Always uses the bottom-70mai layout — the
    original behaviour before multi-layout support was added. New code should
    call `extract_overlay` instead, which tries every profile in `PROFILES`."""
    with tempfile.TemporaryDirectory() as tmp:
        frame_path = str(Path(tmp) / "frame.png")
        _grab_frame(video, offset_s, frame_path)
        crop = _crop(frame_path, (CROP_X_FRAC, CROP_Y_FRAC, CROP_W_FRAC, CROP_H_FRAC))
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
        return pytesseract.image_to_string(crop, lang="chi_sim", config="--psm 6")


def parse_overlay(raw_text: str, offset_s: float = 0.0) -> Overlay:
    """Parse OCR'd bottom-70mai text into an `Overlay`.

    Only recognises the `YYYY年M月D日 HH:MM:SS` line + Chinese address line
    pattern. For multi-layout OCR use `extract_overlay` instead. Kept for
    back-compat with any caller that has a pre-OCR'd text or a hand-cropped
    region in this single format."""
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]

    vio_time = None
    m = _DATE_TIME_RE.search(raw_text)
    if m:
        g = m.groupdict()
        vio_time = (f"{g['y']}-{int(g['mo']):02d}-{int(g['d']):02d} "
                    f"{int(g['h']):02d}:{g['mi']}:{g['s']}")

    current_address = None
    for line in lines:
        if _DATE_TIME_RE.search(line):
            continue
        if "省" in line or "市" in line:
            current_address = line
            break

    area_name_guess = None
    if current_address:
        am = _ADDR_SPLIT_RE.match(current_address)
        if am:
            area_name_guess = f"{am.group('city')}{am.group('district')}"

    return Overlay(offset_s, vio_time, current_address, area_name_guess, raw_text)


def _build_overlay_from_profile(raw_text: str, offset_s: float,
                                prof: OverlayProfile) -> Overlay:
    """Parse OCR'd text using the matched profile's time_re."""
    vio_time = None
    m = prof.time_re.search(raw_text)
    if m:
        g = m.groupdict()
        # All known time_re groups yield YYYY M D H M S; normalise to the
        # 2-digit month/day/hour shape regardless of source separator style.
        vio_time = (f"{g['y']}-{int(g['mo']):02d}-{int(g['d']):02d} "
                    f"{int(g['h']):02d}:{g['mi']}:{g['s']}")

    current_address = None
    if prof.has_address:
        for line in (l.strip() for l in raw_text.splitlines() if l.strip()):
            if prof.time_re.search(line):
                continue
            if "省" in line or "市" in line:
                current_address = line
                break

    area_name_guess = None
    if current_address:
        am = _ADDR_SPLIT_RE.match(current_address)
        if am:
            area_name_guess = f"{am.group('city')}{am.group('district')}"

    return Overlay(offset_s, vio_time, current_address, area_name_guess,
                   raw_text, prof.label)


def extract_overlay(video: str, offset_s: float) -> Overlay:
    """Try each profile's crop+OCR; first whose `time_re` matches wins.

    Returns an `Overlay` with `layout` set to the matched profile's label, or
    an empty `Overlay` (`layout=None`) if no profile's regex matches. When
    more than one profile matches, the first in `PROFILES` wins and a debug
    note goes to stderr."""
    # Cache the frame on disk per (pid, offset_s) so repeated calls from
    # `scan()` and `main()` overlap cleanly with OCR and past-the-end seeks
    # short-circuit to an empty Overlay (ffmpeg exits 0 but writes no file
    # when `-ss` lands at or past EOF).
    cache_root = Path(tempfile.gettempdir()) / "police_report_overlay_cache"
    cache_root.mkdir(exist_ok=True)
    frame_path = cache_root / f"frame_{os.getpid()}_{offset_s:.3f}.png"
    try:
        _grab_frame(video, offset_s, str(frame_path))
    except _FrameGrabError:
        # Past-the-end seek: nothing to OCR, return empty.
        return Overlay(offset_s, None, None, None, "", None)
    with Image.open(frame_path) as img:

        matched: list[tuple[OverlayProfile, str]] = []
        for prof in PROFILES:
            crop = _crop(img, prof.crop_box)
            # Upscale — tesseract does noticeably better on small burned-in
            # text at 2x.
            crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
            raw = pytesseract.image_to_string(crop, lang=prof.lang, config=f"--psm {prof.psm}")
            if prof.time_re.search(raw):
                matched.append((prof, raw))

        if not matched:
            return Overlay(offset_s, None, None, None, "", None)

        if len(matched) > 1:
            labels = ", ".join(p.label for p, _ in matched)
            print(f"note: both {labels} matched; using {matched[0][0].label}.",
                  file=sys.stderr)

        prof, raw = matched[0]
        return _build_overlay_from_profile(raw, offset_s, prof)


# ----------------------------------------------------------------------------
# Whole-clip scan + report.json fill
# ----------------------------------------------------------------------------
def scan(video: str, interval: float = 2.0):
    """Try to extract an `Overlay` from each `--interval`-second sample.
    Single tesseract round-trip per sample that matches; no-op for samples
    whose profile doesn't match (caller sees the empty Overlay)."""
    duration = _probe_duration(video)
    t = 0.0
    results = []
    while t < duration:
        results.append(extract_overlay(video, t))
        t += interval
    return results


def fill_report(template_path: str, overlay: Overlay) -> dict:
    d = json.load(open(template_path, encoding="utf-8"))
    if overlay.vio_time:
        d["vioTime"] = overlay.vio_time
    if overlay.current_address:
        d["currentAddress"] = overlay.current_address
    if overlay.area_name_guess and d.get("areaName", "").strip() in ("", "XX"):
        d["areaName"] = overlay.area_name_guess
    return d


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video")
    p.add_argument("--at", type=float, default=1.0, help="seconds into the video to sample (default 1.0)")
    p.add_argument("--scan", action="store_true", help="sample the whole video every --interval seconds")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--fill", metavar="TEMPLATE_JSON", help="merge result into an existing report json")
    p.add_argument("-o", "--out", help="output path for --fill (default: overwrite TEMPLATE_JSON)")
    args = p.parse_args(argv)

    if args.scan:
        for ov in scan(args.video, args.interval):
            print(f"t={ov.offset_s:6.1f}s  vioTime={ov.vio_time!r}  currentAddress={ov.current_address!r}")
        return

    ov = extract_overlay(args.video, args.at)

    if ov.vio_time:
        hint = _LAYOUT_HINT.get(ov.layout or "", "")
        print(f"detected layout: {ov.layout}  ({hint})")

    if not ov.vio_time:
        print(f"warning: no overlay matched any profile at t={args.at}s; "
              "fill vioTime by hand or geocode from the address.",
              file=sys.stderr)
        print(f"warning: raw OCR for the last-tried profile:\n{ov.raw_text}",
              file=sys.stderr)
    elif ov.layout == "top-slash" and args.fill:
        print("warning: detected top-slash layout at t="
              f"{args.at}s — no address burned into the overlay. "
              "fill currentAddress by hand or via geocoder before submit.",
              file=sys.stderr)

    if args.fill:
        out_path = args.out or args.fill
        filled = fill_report(args.fill, ov)
        json.dump(filled, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"wrote {out_path}")
        print(json.dumps(filled, ensure_ascii=False, indent=2))
        return

    print(json.dumps({
        "vioTime": ov.vio_time,
        "currentAddress": ov.current_address,
        "areaNameGuess": ov.area_name_guess,
        "layout": ov.layout,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
