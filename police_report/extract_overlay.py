"""
OCR the dashcam on-screen overlay (date/time + address burned into the bottom-left
of the frame) and turn it into `vioTime` / `currentAddress` for report.json.

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
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image
import pytesseract

# Overlay lives in the bottom-left corner of every frame seen so far (1920x1080
# 70迈-style DVR export): line 1 = "YYYY年M月D日 HH:MM:SS", line 2 = full address.
# Expressed as fractions of frame size so it survives other resolutions.
CROP_X_FRAC = 0.0
CROP_Y_FRAC = 0.888
CROP_W_FRAC = 0.40
CROP_H_FRAC = 0.112

_DATE_TIME_RE = re.compile(
    r"(?P<y>\d{4})年(?P<mo>\d{1,2})月(?P<d>\d{1,2})日\s*(?P<h>\d{1,2}):(?P<mi>\d{2}):(?P<s>\d{2})"
)
_ADDR_SPLIT_RE = re.compile(r"^(?P<province>.+?省)?(?P<city>.+?市)(?P<district>.+?[区县])")


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


def _crop_overlay(frame_path: str) -> Image.Image:
    img = Image.open(frame_path)
    w, h = img.size
    box = (
        int(w * CROP_X_FRAC),
        int(h * CROP_Y_FRAC),
        int(w * (CROP_X_FRAC + CROP_W_FRAC)),
        int(h * (CROP_Y_FRAC + CROP_H_FRAC)),
    )
    crop = img.crop(box)
    # Upscale — tesseract does noticeably better on small burned-in text at 2x.
    return crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)


def ocr_overlay_at(video: str, offset_s: float) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        frame_path = str(Path(tmp) / "frame.png")
        _grab_frame(video, offset_s, frame_path)
        crop = _crop_overlay(frame_path)
        return pytesseract.image_to_string(crop, lang="chi_sim", config="--psm 6")


@dataclass
class Overlay:
    offset_s: float
    vio_time: Optional[str]       # "YYYY-MM-DD HH:MM:SS"
    current_address: Optional[str]
    area_name_guess: Optional[str]  # e.g. "杭州市西湖区"
    raw_text: str


def parse_overlay(raw_text: str, offset_s: float = 0.0) -> Overlay:
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


def scan(video: str, interval: float = 2.0):
    duration = _probe_duration(video)
    t = 0.0
    results = []
    while t < duration:
        raw = ocr_overlay_at(video, t)
        results.append(parse_overlay(raw, t))
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

    raw = ocr_overlay_at(args.video, args.at)
    ov = parse_overlay(raw, args.at)

    if not ov.vio_time or not ov.current_address:
        print(f"warning: could not fully parse overlay at t={args.at}s, raw OCR text:", file=sys.stderr)
        print(raw, file=sys.stderr)

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
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
