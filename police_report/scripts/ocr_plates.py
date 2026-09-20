"""
OCR vehicle license plates in dashcam photos and rename each image with its
creation timestamp plus plate number(s).

Uses HyperLPR3, a purpose-built Chinese license-plate detector+recogniser
(ONNX). It finds and reads blue plates and green new-energy plates directly —
no manual cropping. Each file is renamed to its creation time
(`yyyy_mm_dd_hh_mm`) followed by the plate(s), e.g.
`2026_09_07_15_34_浙A8Z329_沪ABH7866.jpg` (multiple plates joined with `_`).
If no plate can be read the file is still renamed, to the timestamp alone
(`2026_09_07_15_34.jpg`) so nothing is left behind.

Requires:
  pip install hyperlpr3 opencv-python
(hyperlpr3 pulls onnxruntime; the recognition model downloads on first run.)

Examples:
  # dry run — prints old -> new, renames nothing
  python -m police_report.scripts.ocr_plates camera/
  # actually rename
  python -m police_report.scripts.ocr_plates camera/ --apply
  python -m police_report.scripts.ocr_plates photo1.jpg photo2.jpg --apply
"""
from __future__ import annotations

import argparse
import re
import sys
import warnings
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

try:
    import hyperlpr3 as lpr3
except ImportError:  # pragma: no cover
    print("error: hyperlpr3 not installed. run: pip install hyperlpr3",
          file=sys.stderr)
    raise

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:  # HEIC support is optional
    pillow_heif = None

from PIL import Image, ImageOps

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff",
            ".heic", ".heif"}

_PROVINCES = "京津沪渝冀晋蒙辽吉黑苏浙皖闽赣鲁豫鄂湘粤桂琼川贵云藏陕甘青宁新"
# Valid mainland plate: province glyph + city letter + 5 (blue) or 6 (green
# new-energy) serial chars. Serial never uses I or O.
_PLATE_RE = re.compile(
    r"[" + _PROVINCES + r"][A-Z][A-HJ-NP-Z0-9]{5,6}$"
)

# Minimum recognition confidence to accept a reading. Blurry/distant plates
# read below this are skipped rather than risk a wrong plate in the filename.
_MIN_CONF = 0.85
# Scales to run detection at. 2x rescues moderately small plates; beyond that
# interpolation noise hurts more than it helps.
_SCALES = (1.0, 2.0)

_catcher = None


def _get_catcher():
    global _catcher
    if _catcher is None:
        # HIGH = 640x640 detection grid. The default LOW (320x320) shrinks a
        # small/distant plate in a 2560px-wide frame to well under detection
        # range and misses it entirely — especially at night.
        _catcher = lpr3.LicensePlateCatcher(
            detect_level=lpr3.DETECT_LEVEL_HIGH)
    return _catcher


def _is_green_plate(frame: np.ndarray, box, scale: float) -> bool:
    """True if the detected plate region is a green new-energy plate.

    hyperlpr3's plate-type code is unreliable on dark/blurry frames (it tags
    some blue plates as type 3 and emits a spurious 8th character), so decide
    by the region's hue directly: green new-energy plates sit in HSV green,
    regular plates in blue."""
    x1, y1, x2, y2 = (int(v / scale) for v in box)
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return False
    roi = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0]
    sat = hsv[:, :, 1]
    coloured = hue[sat > 80]
    if coloured.size == 0:
        return False
    green = ((coloured >= 35) & (coloured <= 95)).mean()
    blue = ((coloured >= 100) & (coloured <= 130)).mean()
    return green > blue


def _normalize(code: str, green: bool) -> str | None:
    """Coerce a raw hyperlpr reading into a valid plate or None.

    hyperlpr occasionally emits a doubled province prefix (`苏浙M…`) or a
    spurious trailing char on blue plates, or drops a char on green plates.
    Fix length by colour (blue = 7 chars, green = 8) and validate."""
    code = code.strip().upper()
    # Collapse a doubled/garbled leading province: keep the last province glyph
    # in the leading run, e.g. 苏浙MDB5826 -> 浙MDB5826.
    while len(code) > 7 and code[0] in _PROVINCES and code[1] in _PROVINCES:
        code = code[1:]
    target_len = 8 if green else 7
    if len(code) > target_len:
        code = code[:target_len]
    if len(code) != target_len:
        return None
    return code if _PLATE_RE.match(code) else None


def _load_image(path: Path) -> np.ndarray | None:
    """Load an image as a BGR ndarray.

    cv2.imread can't decode HEIC/HEIF (iPhone photos), so those go through
    pillow-heif, applying EXIF orientation. Returns None if unreadable."""
    if path.suffix.lower() in (".heic", ".heif"):
        if pillow_heif is None:
            print(f"warning: {path.name} is HEIC but pillow-heif not installed "
                  "(pip install pillow-heif); skipping", file=sys.stderr)
            return None
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                rgb = np.asarray(im)
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        except Exception:
            return None
    return cv2.imread(str(path))


def ocr_plates_in_image(path: Path) -> list[str]:
    """Return the distinct plate strings found in `path`, best first.

    Plates are ranked by on-screen size × confidence (the nearest vehicle is
    the usual subject), duplicates and province-only conflicts (e.g. the same
    serial read as both 浙A… and 京A… on a blurry frame) are collapsed to the
    single strongest reading."""
    img = _load_image(path)
    if img is None:
        raise ValueError(f"cannot read image: {path}")

    catcher = _get_catcher()
    # plate suffix (letter+serial) -> (score, full_plate)
    best: dict[str, tuple[float, str]] = {}
    # hyperlpr3 calls np.mean() on an empty confidence list when a detected
    # candidate yields no characters, raising a benign "Mean of empty slice"
    # RuntimeWarning. Suppress it — it just means that candidate read nothing.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Mean of empty slice", category=RuntimeWarning)
        warnings.filterwarnings(
            "ignore", message="invalid value encountered",
            category=RuntimeWarning)
        for scale in _SCALES:
            frame = (img if scale == 1.0
                     else cv2.resize(img, None, fx=scale, fy=scale,
                                     interpolation=cv2.INTER_CUBIC))
            for raw_code, conf, _ptype, box in catcher(frame):
                if conf < _MIN_CONF:
                    continue
                code = _normalize(raw_code, _is_green_plate(img, box, scale))
                if code is None:
                    continue
                x1, y1, x2, y2 = box
                area = ((x2 - x1) * (y2 - y1)) / (scale ** 2)
                # Rank near/large, high-confidence reads first.
                score = area * conf
                # Collapse same-plate repeats across scales; keep best score.
                prev = best.get(code)
                if prev is None or score > prev[0]:
                    best[code] = (score, code)

    # Merge province-only conflicts: readings that share the city-letter +
    # serial suffix (everything after the province glyph) but disagree on the
    # province — keep the highest-scoring one.
    by_suffix: dict[str, tuple[float, str]] = {}
    for score, code in best.values():
        suffix = code[1:]  # drop province glyph
        prev = by_suffix.get(suffix)
        if prev is None or score > prev[0]:
            by_suffix[suffix] = (score, code)

    ranked = sorted(by_suffix.values(), key=lambda t: -t[0])
    return [code for _score, code in ranked]


def _creation_ts(path: Path) -> str:
    """File creation time as `yyyy_mm_dd_hh_mm`.

    Uses the filesystem birth time (when the photo was created). On platforms
    without a birth timestamp (older Linux) it falls back to mtime."""
    st = path.stat()
    ts = getattr(st, "st_birthtime", None)
    if ts is None:  # Linux fallback
        ts = st.st_mtime
    return datetime.fromtimestamp(ts).strftime("%Y_%m_%d_%H_%M")


def _target_name(path: Path, plates: list[str]) -> Path:
    ts = _creation_ts(path)
    # No plate read -> timestamp only; otherwise timestamp + plate(s).
    stem = ts + ("_" + "_".join(plates) if plates else "")
    target = path.with_name(stem + path.suffix.lower())
    if target == path:
        return target
    n = 2
    while target.exists():
        target = path.with_name(f"{stem}_{n}{path.suffix.lower()}")
        n += 1
    return target


def _iter_images(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            out.extend(sorted(q for q in path.rglob("*")
                              if q.suffix.lower() in IMG_EXTS))
        elif path.is_file():
            out.append(path)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+",
                    help="image files or directories (directories are scanned "
                         "recursively)")
    ap.add_argument("--apply", action="store_true",
                    help="actually rename files (default: dry run)")
    args = ap.parse_args(argv)

    images = _iter_images(args.paths)
    if not images:
        print("no images found", file=sys.stderr)
        return 1

    n_renamed = n_noplate = 0
    for img in images:
        try:
            plates = ocr_plates_in_image(img)
        except Exception as e:  # unreadable image shouldn't abort the batch
            print(f"ERROR  {img}: {e}", file=sys.stderr)
            continue
        label = ", ".join(plates) if plates else "no plate read"
        if not plates:
            n_noplate += 1
        target = _target_name(img, plates)
        if target == img:
            print(f"OK     {img.name}  ({label})")
            continue
        print(f"RENAME {img.name}  ->  {target.name}  ({label})")
        if args.apply:
            img.rename(target)
        n_renamed += 1

    mode = "renamed" if args.apply else "would rename"
    print(f"\n{len(images)} image(s): {n_renamed} {mode} "
          f"({n_noplate} with no plate found, renamed to timestamp only)"
          f"{'' if args.apply else ' (dry run — pass --apply to rename)'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
