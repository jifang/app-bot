"""
WfjbClient — client for the Hangzhou police 违法举报 (wfjb) backend.

Reverse-engineered from com.hzpd.jwztc (警察叔叔) app traffic. All calls hit the
H5 backend at https://wfjb.police.hangzhou.gov.cn:7443/wfjb-front-api/ and
authenticate with an `x-token` JWT (minted by the app's mgop login — see auth.py).
"""
from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass, field
from typing import Any

import requests

BASE = "https://wfjb.police.hangzhou.gov.cn:7443/wfjb-front-api"

# The app sends a doubled UA string; the server only cares that it looks like the
# embedded UWS webview. This is the exact UA observed on the wire.
_UA = (
    "Mozilla/5.0 (Linux; U; Android 11; zh-CN; Redmi Note 8 Build/RKQ1.201004.002) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/69.0.3497.100 "
    "UWS/3.22.1.271 Mobile Safari/537.36 Jupiter/1.0 "
    "AppChannel/421424f5fe7b444782907d18955b8e1a"
)


class WfjbError(RuntimeError):
    """Non-200 business code or transport error from the wfjb backend."""


@dataclass
class ViolationReport:
    """
    One 违法举报. Construct with real-world coordinates via `from_coords`; do NOT
    set `latitude`/`longitude` by hand unless you understand the wire swap below.

    WIRE SWAP (verified against captured app traffic): the app puts the real
    LONGITUDE in the JSON field `latitude`, and the real LATITUDE in `longitude`.
    We reproduce that exactly so the backend geocodes correctly.
    """
    vio_license_plate: str            # 车牌, e.g. "浙AXXXXX"
    vio_type: str                     # 违法类型码 from dict/wflx, e.g. "bagdyzxd"
    vio_time: str                     # "YYYY-MM-DD HH:MM:SS"
    area_code: str                    # 区划码 from dict/100004, e.g. "330106"
    area_name: str                    # e.g. "杭州市西湖区"
    vio_address: str                  # 违法地址 (free text)
    current_address: str              # 当前定位地址 (reverse-geocoded)
    phone: str
    name: str
    vio_describe: str                 # 描述
    video_list: list[int] = field(default_factory=list)   # ids from upload_video
    pic_list: list[int] = field(default_factory=list)     # ids from upload_pic
    _lat_field: float = 0.0           # goes into JSON "latitude"  (== real longitude)
    _lng_field: float = 0.0           # goes into JSON "longitude" (== real latitude)

    @classmethod
    def from_coords(cls, *, longitude: float, latitude: float, **kw) -> "ViolationReport":
        """Build from real-world lng/lat; handles the wire swap internally."""
        r = cls(**kw)
        r._lat_field = longitude      # app puts longitude into "latitude"
        r._lng_field = latitude       # app puts latitude into "longitude"
        return r

    def to_payload(self) -> dict[str, Any]:
        return {
            "vioLicensePlate": self.vio_license_plate,
            "currentAddress": self.current_address,
            "latitude": self._lat_field,
            "longitude": self._lng_field,
            "phone": self.phone,
            "areaCode": self.area_code,
            "videoId": "",
            "areaName": self.area_name,
            "vioAddress": self.vio_address,
            "vioTime": self.vio_time,
            "vioType": self.vio_type,
            "name": self.name,
            "vioDescribe": self.vio_describe,
            "picList": self.pic_list,
            "videoList": self.video_list,
        }


class WfjbClient:
    def __init__(self, x_token: str, cna_cookie: str = "", base: str = BASE,
                 timeout: int = 60, provider=None):
        if not x_token:
            raise ValueError("x_token is required (see auth.py / README for how to get one)")
        self.base = base.rstrip("/")
        self.timeout = timeout
        # Optional TokenProvider: reads refresh-and-retry once through it; submit
        # refreshes through it *before* posting. Left None for one-shot/manual use.
        self.provider = provider
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": _UA,
            "X-Token": x_token,
            "X-Requested-With": "com.hzpd.jwztc",
            "Origin": "https://wfjb.police.hangzhou.gov.cn:7443",
            "Referer": "https://wfjb.police.hangzhou.gov.cn:7443/",
            "Accept-Language": "zh-CN,en-US;q=0.9",
        })
        if cna_cookie:
            self.s.headers["Cookie"] = f"cna={cna_cookie}"

    def _set_token(self, token: str) -> None:
        self.s.headers["X-Token"] = token

    def _refresh_before(self, min_ttl_s: int = 600) -> None:
        """Swap in a token that is fresh enough, refreshing via the provider if
        needed. Used before a submit — no post-response retry is ever attempted."""
        if self.provider:
            self._set_token(self.provider.get_token(min_ttl_s=min_ttl_s))

    # ---- low level ----
    def _get(self, path: str, **kw) -> dict:
        """GET an idempotent read. On failure, refresh once via the provider and
        retry exactly once (safe: reads are idempotent)."""
        try:
            return self._unwrap(self.s.get(self.base + path, timeout=self.timeout, **kw))
        except WfjbError:
            if not self.provider:
                raise
            res = self.provider.refresh(force=True)
            if not res.token:               # refresh couldn't help — surface original class
                raise
            self._set_token(res.token)
            return self._unwrap(self.s.get(self.base + path, timeout=self.timeout, **kw))

    def _post(self, path: str, **kw) -> dict:
        # No automatic retry here: _post backs submit_report, where a blind retry
        # after an ambiguous response could file a duplicate real report.
        return self._unwrap(self.s.post(self.base + path, timeout=self.timeout, **kw))

    @staticmethod
    def _unwrap(resp: requests.Response) -> dict:
        try:
            body = resp.json()
        except ValueError:
            raise WfjbError(f"non-JSON response {resp.status_code}: {resp.text[:200]}")
        if body.get("code") != 200:
            raise WfjbError(f"code={body.get('code')} msg={body.get('msg')} ({resp.status_code})")
        return body

    # ---- read-only ----
    def user_info(self) -> dict:
        """{'name','phone','star','totalReport'}"""
        return self._get("/user/info")["data"]

    def violation_types(self) -> list[dict]:
        """dict/wflx -> [{'dmbh': code, 'dmmc': name}, ...]"""
        return self._get("/common/dict/wflx")["data"]

    def areas(self) -> list[dict]:
        """dict/100004 -> region tree (city -> districts under 'next')"""
        return self._get("/common/dict/100004")["data"]

    def address_tips(self, area_code: str) -> list[dict]:
        return self._get(f"/common/dict/addressTips/{area_code}")["data"]

    def report_history(self) -> list[dict]:
        return self._get("/vio/report/history")["data"]

    def notice(self, code: str = "report.user.notice") -> dict:
        return self._get("/common/config", params={"code": code})["data"]

    # ---- uploads ----
    def upload_video(self, path: str) -> int:
        """POST /file/upload (multipart field 'file'). Returns the new file id."""
        return self._upload(path, default_ct="video/mp4")

    def upload_pic(self, path: str) -> int:
        return self._upload(path, default_ct="image/jpeg")

    def _upload(self, path: str, default_ct: str) -> int:
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        ct = mimetypes.guess_type(path)[0] or default_ct
        fname = os.path.basename(path)
        self._refresh_before()      # a stale token here just wastes an upload; refresh first
        with open(path, "rb") as fh:
            files = {"file": (fname, fh, ct)}
            body = self._post("/file/upload", files=files)
        return body["data"]["id"]

    # ---- submit (files a REAL police report) ----
    def submit_report(self, report: ViolationReport) -> dict:
        """
        POST /vio/report/submit. This files a real 违法举报 with Hangzhou police.
        Returns the success payload (contains 回执号 'xlh').
        """
        self._refresh_before()      # ensure a fresh token BEFORE we file; never retry after
        return self._post(
            "/vio/report/submit",
            json=report.to_payload(),
            headers={"Content-Type": "application/json;charset=UTF-8",
                     "Accept": "application/json, text/plain, */*"},
        )["data"]
