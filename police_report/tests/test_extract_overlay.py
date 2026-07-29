"""Unit tests for `police_report.extract_overlay` multi-layout support.

The spec lives at
`docs/superpowers/specs/2026-07-10-multi-layout-overlay-design.md`. These
tests pin the public contract introduced by that spec:

- `PROFILES` carries both layouts in the right order.
- `Overlay` has a new `layout` field, default `None`, positional 6th arg.
- `_build_overlay_from_profile` parses both 年月日 and /-separated timestamps.
- `extract_overlay` tries each profile and assigns `layout` on match.
- `extract_overlay` first-profile-wins when more than one profile matches.
- `extract_overlay` returns an empty `Overlay` (`layout=None`) when no
  profile's regex matches.
- `parse_overlay` (back-compat) still works with the original 年月日 format.
- `_crop` accepts either a file path or an open PIL.Image.

OCR, frame-grabbing, and ffmpeg are all mocked — these tests don't depend on
tesseract being installed or on a real video file. End-to-end OCR coverage
is exercised manually against `camera/*.mp4`.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from PIL import Image

from police_report.extract_overlay import (
    Overlay,
    OverlayProfile,
    PROFILES,
    _build_overlay_from_profile,
    _crop,
    extract_overlay,
    parse_overlay,
    scan,
)


# ----------------------------------------------------------------------------
# PROFILES structure
# ----------------------------------------------------------------------------
def test_profiles_has_both_layouts_in_documented_order():
    labels = [p.label for p in PROFILES]
    # The spec's tie-break rule is "first profile in PROFILES wins" so the order
    # matters; pin it down.
    assert labels == ["bottom-70mai", "top-slash"]


def test_profiles_are_overlayprofile_dataclass():
    for p in PROFILES:
        assert isinstance(p, OverlayProfile)
        assert p.label
        assert len(p.crop_box) == 4
        assert p.time_re.search  # compiles to a usable regex
        assert isinstance(p.has_address, bool)


def test_bottom_70mai_profile_pin_values():
    p = PROFILES[0]
    assert p.crop_box == (0.0, 0.888, 0.40, 0.112)
    assert p.has_address is True
    assert p.lang == "chi_sim"


def test_top_slash_profile_pin_values():
    p = PROFILES[1]
    assert p.crop_box == (0.0, 0.0, 0.40, 0.10)
    assert p.has_address is False
    # eng-only: this overlay is pure ASCII digits/slashes/colons.
    assert p.lang == "eng"


# ----------------------------------------------------------------------------
# Overlay dataclass back-compat
# ----------------------------------------------------------------------------
def test_overlay_5arg_construction_still_works_with_layout_default_none():
    # The existing positional form `Overlay(offset_s, vio_time, ...)` is
    # what `parse_overlay` still uses — it must keep working unchanged.
    ov = Overlay(1.0, None, None, None, "")
    assert ov.layout is None


def test_overlay_6arg_construction_sets_layout():
    ov = Overlay(1.0, "2026-07-20 20:53:32", None, None, "", "top-slash")
    assert ov.layout == "top-slash"


# ----------------------------------------------------------------------------
# _build_overlay_from_profile
# ----------------------------------------------------------------------------
def test_build_overlay_slash_format():
    p = PROFILES[1]
    raw = "2026/07/20 20:53:32\n"
    ov = _build_overlay_from_profile(raw, 1.0, p)
    assert ov.offset_s == 1.0
    assert ov.vio_time == "2026-07-20 20:53:32"
    assert ov.current_address is None
    assert ov.area_name_guess is None
    assert ov.layout == "top-slash"


def test_build_overlay_nianyue_ri_format():
    p = PROFILES[0]
    raw = "2026年7月8日 08:33:46\n浙江省杭州市西湖区之江路123号\n"
    ov = _build_overlay_from_profile(raw, 3.0, p)
    assert ov.vio_time == "2026-07-08 08:33:46"
    assert ov.current_address == "浙江省杭州市西湖区之江路123号"
    assert ov.area_name_guess == "杭州市西湖区"
    assert ov.layout == "bottom-70mai"


def test_build_overlay_slash_no_address_detected_even_if_chinese_in_text():
    p = PROFILES[1]
    # Even if chi_sim-trained OCR picks up Chinese text alongside the timestamp,
    # top-slash never has a burned-in address.
    raw = "2026/07/20 20:53:32 浙江省杭州市\n"
    ov = _build_overlay_from_profile(raw, 1.0, p)
    assert ov.vio_time == "2026-07-20 20:53:32"
    assert ov.current_address is None
    assert ov.area_name_guess is None


# ----------------------------------------------------------------------------
# _crop
# ----------------------------------------------------------------------------
def test_crop_accepts_pil_image_directly(tmp_path):
    img = Image.new("RGB", (1000, 500), "white")
    out = _crop(img, (0.0, 0.0, 0.40, 0.10))
    assert out.size == (400, 50)


def test_crop_accepts_file_path(tmp_path):
    p = tmp_path / "frame.png"
    Image.new("RGB", (1000, 500), "white").save(p)
    out = _crop(str(p), (0.0, 0.888, 0.40, 0.10))
    assert out.size == (400, 50)


# ----------------------------------------------------------------------------
# parse_overlay (back-compat)
# ----------------------------------------------------------------------------
def test_parse_overlay_nianyue_ri_format_still_works():
    # Original API path — must keep working without breaking.
    raw = "2026年7月8日 08:33:46\n浙江省杭州市西湖区之江路123号\n"
    ov = parse_overlay(raw, 3.0)
    assert ov.vio_time == "2026-07-08 08:33:46"
    assert ov.current_address == "浙江省杭州市西湖区之江路123号"
    # parse_overlay doesn't go through PROFILES — layout stays None for back-compat.
    assert ov.layout is None


def test_parse_overlay_returns_empty_slash_when_uses_old_regex_only():
    # Back-compat parser is intentionally narrow — slash format yields no vio_time.
    raw = "2026/07/20 20:53:32"
    ov = parse_overlay(raw, 1.0)
    assert ov.vio_time is None
    assert ov.layout is None


# ----------------------------------------------------------------------------
# extract_overlay — empty case + match case + tie-break case
# ----------------------------------------------------------------------------
def test_extract_overlay_no_match_returns_empty_overlay():
    """When no profile's regex matches the OCR'd crop, return an empty Overlay."""
    def fake_grab(video, offset_s, out_path):
        Image.new("RGB", (1000, 500), "white").save(out_path)

    with patch("police_report.extract_overlay._grab_frame", side_effect=fake_grab), \
         patch("police_report.extract_overlay.pytesseract.image_to_string",
               return_value="some noisy OCR with no date anywhere\n"):
        ov = extract_overlay("whatever.mp4", 2.5)

    assert ov.offset_s == 2.5
    assert ov.vio_time is None
    assert ov.current_address is None
    assert ov.layout is None


def test_extract_overlay_top_slash_match_sets_layout_and_vio_time():
    fake_frame_arg = {"counter": 0}
    fake_ocr_calls = []
    ocr_results = ["", "2026/07/20 20:53:32\n"]

    def fake_grab(video, offset_s, out_path):
        fake_frame_arg["counter"] += 1
        # Write a 1000x500 white image so _crop produces something to resize.
        Image.new("RGB", (1000, 500), "white").save(out_path)

    def fake_ocr(image, **kw):
        # Track OCR call count and language so we can verify both profiles tried.
        fake_ocr_calls.append(kw.get("config"))
        return ocr_results[len(fake_ocr_calls) - 1]

    with patch("police_report.extract_overlay._grab_frame", side_effect=fake_grab), \
         patch("police_report.extract_overlay.pytesseract.image_to_string",
               side_effect=fake_ocr):
        ov = extract_overlay("whatever.mp4", 1.0)

    assert ov.layout == "top-slash"
    assert ov.vio_time == "2026-07-20 20:53:32"
    # Single ffmpeg call per extract_overlay() invocation; one crop+OCR per profile.
    assert fake_frame_arg["counter"] == 1
    # Two OCR calls = bottom-70mai tried first (junk), then top-slash (matched).
    assert len(fake_ocr_calls) == 2


def test_extract_overlay_first_profile_wins_on_dual_match(capsys):
    """When both profiles' time_re match the same OCR result, first wins."""
    def fake_grab(video, offset_s, out_path):
        Image.new("RGB", (1000, 500), "white").save(out_path)

    def fake_ocr(image, **kw):
        # Both 年月日 and slash forms happen to OCR from this artificial image.
        # Both profiles' time_re regex search returns truthy.
        return "2026年7月20日 20:53:32 2026/07/20 20:53:32\n"

    with patch("police_report.extract_overlay._grab_frame", side_effect=fake_grab), \
         patch("police_report.extract_overlay.pytesseract.image_to_string",
               side_effect=fake_ocr):
        ov = extract_overlay("whatever.mp4", 1.0)

    assert ov.layout == "bottom-70mai"
    assert ov.vio_time == "2026-07-20 20:53:32"
    err = capsys.readouterr().err
    assert "both" in err and "bottom-70mai" in err


def test_extract_overlay_back_compat_layout_field_is_optional_kwarg_only():
    """Callers that still build `Overlay(offset_s, ..., raw_text)` must keep working."""
    # This is the constructor path used by parse_overlay internally.
    ov = Overlay(0.0, "2026-07-08 08:33:46", None, None, "raw text")
    assert ov.layout is None


# ----------------------------------------------------------------------------
# scan — delegates to extract_overlay, not ocr_overlay_at
# ----------------------------------------------------------------------------
def test_scan_uses_extract_overlay_per_sample(monkeypatch):
    """`scan` must call extract_overlay per sample (multi-layout-aware)."""
    calls: list[float] = []

    def fake_extract(video, t):
        calls.append(t)
        return Overlay(t, None, None, None, "")

    monkeypatch.setattr("police_report.extract_overlay._probe_duration", lambda v: 5.0)
    monkeypatch.setattr(
        "police_report.extract_overlay.extract_overlay", fake_extract)

    results = scan("whatever.mp4", interval=2.0)

    # 5s clip at 2s interval should yield samples at 0, 2, 4.
    assert calls == [0.0, 2.0, 4.0]
    assert [r.offset_s for r in results] == [0.0, 2.0, 4.0]


# ----------------------------------------------------------------------------
# Edge case: empty PROFILES list (defensive — not normal config)
# ----------------------------------------------------------------------------
def test_extract_overlay_handles_no_profiles_gracefully(monkeypatch):
    monkeypatch.setattr("police_report.extract_overlay.PROFILES", [])
    monkeypatch.setattr(
        "police_report.extract_overlay._grab_frame",
        lambda v, t, p: Image.new("RGB", (100, 50), "white").save(p) or None)
    ov = extract_overlay("whatever.mp4", 0.0)
    assert ov.offset_s == 0.0
    assert ov.layout is None
    assert ov.vio_time is None
