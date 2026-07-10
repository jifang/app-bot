"""
Resolve human-friendly Chinese names -> backend codes for a violation report.

The user may write, in report.json:
  "vioType":  "不按规定使用转向灯"   (name)  OR  "bagdyzxd" (code)
  "areaName": "西湖区" / "杭州市西湖区" (name)  and/or "areaCode": "" (filled in)

`resolve_report()` maps names to the codes the wfjb backend expects, using the
cached dict (police_report/dicts.json, refreshed via `cli dict ...`). It is strict:
if a name matches nothing, it raises ResolveError and the caller must NOT submit.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

DICTS_PATH = os.path.join(os.path.dirname(__file__), "dicts.json")


class ResolveError(ValueError):
    """A human name did not map to exactly one backend code. Do not submit."""


def _load_dicts(path: str = DICTS_PATH) -> dict:
    if not os.path.isfile(path):
        raise ResolveError(
            f"dict cache missing: {path}. Refresh it with "
            f"`python -m police_report.cli dict wflx` / `dict areas` (needs a token)."
        )
    return json.load(open(path, encoding="utf-8"))


# ---- vioType -------------------------------------------------------------
def resolve_vio_type(value: str, wflx: list[dict]) -> str:
    """Return the dmbh code for a vioType given as a code or a Chinese name."""
    codes = {w["dmbh"]: w["dmmc"] for w in wflx}
    if value in codes:
        return value                              # already a valid code
    by_name = {w["dmmc"]: w["dmbh"] for w in wflx}
    if value in by_name:
        return by_name[value]
    # tolerant: ignore surrounding whitespace / full-width spaces
    norm = value.strip().replace("　", "")
    by_name_norm = {k.strip().replace("　", ""): v for k, v in by_name.items()}
    if norm in by_name_norm:
        return by_name_norm[norm]
    raise ResolveError(
        f"vioType '{value}' matches no 违法类型. "
        f"Valid names: {', '.join(sorted(by_name))}"
    )


# ---- area ----------------------------------------------------------------
def _walk_areas(areas: list[dict]):
    """Yield (code, name, parent_name) for every node in the area tree."""
    def rec(nodes, parent_name):
        for n in nodes:
            code, name = n.get("dmbh"), n.get("dmmc")
            yield code, name, parent_name
            kids = n.get("next") or []
            if kids:
                yield from rec(kids, name)
    return rec(areas, None)


def resolve_area(value: str, areas: list[dict]) -> tuple[str, str]:
    """
    Map an area given as code or name -> (areaCode, canonical areaName).
    Accepts a district name ("西湖区"), a city+district ("杭州市西湖区"), or a code.
    Canonical areaName is "<city><district>" (e.g. "杭州市西湖区"), matching the app.
    """
    nodes = list(_walk_areas(areas))
    by_code = {c: (c, n, p) for c, n, p in nodes}
    if value in by_code:
        c, n, p = by_code[value]
        return c, (f"{p}{n}" if p else n)

    norm = value.strip().replace("　", "")
    # try exact district name, then city+district concatenation
    matches = []
    for c, n, p in nodes:
        full = f"{p}{n}" if p else n
        if norm == n or norm == full:
            matches.append((c, full))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        names = sorted({n for _, n, _ in nodes})
        raise ResolveError(
            f"areaName '{value}' matches no 区划. Valid names: {', '.join(names)}"
        )
    raise ResolveError(
        f"areaName '{value}' is ambiguous — matches {len(matches)}: "
        f"{', '.join(f'{n}({c})' for c, n in matches)}. Use the full name or areaCode."
    )


# ---- top level -----------------------------------------------------------
def resolve_report(d: dict, dicts: Optional[dict] = None) -> dict:
    """
    Return a copy of the report dict with vioType/areaCode/areaName resolved to
    backend codes. Raises ResolveError (before any network call) on any mismatch.
    """
    dicts = dicts or _load_dicts()
    out = dict(d)

    if "vioType" in out and out["vioType"]:
        out["vioType"] = resolve_vio_type(str(out["vioType"]), dicts["wflx"])

    # area: prefer an explicit non-placeholder areaCode; else derive from areaName
    area_in = out.get("areaCode") or ""
    area_is_code = area_in.isdigit() and len(area_in) == 6
    if not area_is_code:
        # resolve from areaName (or from a name accidentally put in areaCode)
        src = out.get("areaName") or area_in
        if not src:
            raise ResolveError("no areaName/areaCode to resolve.")
        code, canonical = resolve_area(str(src), dicts["areas"])
        out["areaCode"], out["areaName"] = code, canonical
    elif out.get("areaName"):
        # both code and name given — they must agree, else it's a mismatch.
        code_c, code_name = resolve_area(area_in, dicts["areas"])
        name_c, _ = resolve_area(str(out["areaName"]), dicts["areas"])
        if name_c != code_c:
            raise ResolveError(
                f"areaCode {area_in} ({code_name}) and areaName "
                f"'{out['areaName']}' ({name_c}) disagree. Fix one."
            )
        out["areaName"] = code_name
    return out
