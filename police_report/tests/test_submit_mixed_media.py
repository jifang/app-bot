"""submit_report wire contract when both videoList and picList are non-empty.

The wfjb backend gets BOTH lists in the JSON payload (see client.py:to_payload),
but no prior report in this repo has submitted with non-empty picList, so the
contract is untested on the wire. These tests pin down the field shape, the
longitude/latitude wire swap, and the upload-then-submit happy path with both
media kinds populated.
"""
from __future__ import annotations

import json
import io
import os

import pytest
import requests

from police_report.client import BASE, ViolationReport, WfjbClient


class _Resp:
    def __init__(self, *, status=200, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _FakeSession:
    """Records each POST's json/files/data/headers, replays queued responses."""
    def __init__(self, *, post=None, posts=()):
        self.headers = {}
        self.calls = []
        self._posts = list(posts) if posts else ([post] if post else [])

    def post(self, url, *, json=None, files=None, data=None, headers=None, **kw):
        self.calls.append({"url": url, "json": json, "files": files,
                           "data": data, "headers": headers})
        return self._posts.pop(0)


def _report(*, videos=(1,), pics=()):
    return ViolationReport.from_coords(
        longitude=120.19070, latitude=30.24700,
        vio_license_plate="浙MDG0917", vio_type="sxbd",
        vio_time="2026-07-20 20:49:46",
        area_code="330102", area_name="杭州市上城区",
        vio_address="中河高架路北向上匝道(近庆春路口)",
        current_address="浙江省杭州市上城区中河高架路北向上匝道(近庆春路口)",
        phone="17280180930", name="方季",
        vio_describe="该车从右侧汇入车道压过白色实线、并入中河高架北向主路",
        video_list=list(videos), pic_list=list(pics),
    )


# --- to_payload shape --------------------------------------------------------

def test_payload_contains_both_lists_with_swapped_coords():
    r = _report(videos=[3020190], pics=[99, 100])
    p = r.to_payload()
    assert p["videoList"] == [3020190]
    assert p["picList"] == [99, 100]
    # wire swap: real longitude must end up in JSON "latitude"
    assert p["latitude"] == 120.19070
    assert p["longitude"] == 30.24700


def test_payload_default_piclist_is_empty_list_not_missing():
    r = _report(videos=[1])
    p = r.to_payload()
    assert p["picList"] == []
    assert "picList" in p


# --- POST /vio/report/submit payload shape -----------------------------------

def test_submit_sends_both_lists_in_one_request():
    session = _FakeSession(post=_Resp(body={"code": 200, "data": {"xlh": 1493}}))
    c = WfjbClient("T")
    c.s = session

    assert c.submit_report(_report(videos=[3020190], pics=[99, 100])) == {"xlh": 1493}

    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == f"{BASE}/vio/report/submit"
    body = call["json"]
    assert body["videoList"] == [3020190]
    assert body["picList"] == [99, 100]
    assert body["vioLicensePlate"] == "浙MDG0917"
    assert body["vioType"] == "sxbd"
    assert body["areaCode"] == "330102"
    # confirm Content-Type header for the JSON payload
    assert call["headers"]["Content-Type"] == "application/json;charset=UTF-8"


def test_submit_with_only_video_still_includes_empty_piclist():
    session = _FakeSession(post=_Resp(body={"code": 200, "data": {"xlh": 1}}))
    c = WfjbClient("T")
    c.s = session

    c.submit_report(_report(videos=[3020190]))
    body = session.calls[0]["json"]
    assert body["videoList"] == [3020190]
    assert body["picList"] == []


# --- upload_pic wire contract -----------------------------------------------

def test_upload_pic_returns_id_from_data_field(tmp_path):
    pic = tmp_path / "snap.jpg"
    pic.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    session = _FakeSession(post=_Resp(body={"code": 200, "data": {"id": 7777}}))
    c = WfjbClient("T")
    c.s = session

    assert c.upload_pic(str(pic)) == 7777

    call = session.calls[0]
    assert call["url"] == f"{BASE}/file/upload"
    assert call["files"] is not None
    assert call["json"] is None
    files = call["files"]
    # the form field name must be "file"
    assert "file" in files
    name, _, ct = files["file"]
    assert name == "snap.jpg"
    # mimetypes guesses image/jpeg for .jpg, but the client passes whatever it
    # guessed — we just need it to be a non-empty string.
    assert ct


def test_upload_pic_then_submit_in_one_session(tmp_path):
    """End-to-end: upload_pic → use id in report → submit."""
    pic = tmp_path / "snap.jpg"
    pic.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    session = _FakeSession(posts=[
        _Resp(body={"code": 200, "data": {"id": 7777}}),
        _Resp(body={"code": 200, "data": {"xlh": 1493}}),
    ])
    c = WfjbClient("T")
    c.s = session

    pic_id = c.upload_pic(str(pic))
    r = _report(videos=[3020190], pics=[pic_id])
    assert c.submit_report(r) == {"xlh": 1493}
    assert session.calls[0]["url"] == f"{BASE}/file/upload"
    assert session.calls[1]["json"]["picList"] == [7777]
    assert session.calls[1]["json"]["videoList"] == [3020190]


# --- error path --------------------------------------------------------------

def test_submit_propagates_business_error_with_both_lists():
    session = _FakeSession(post=_Resp(body={"code": 400, "msg": "pic too large"}))
    c = WfjbClient("T")
    c.s = session

    with pytest.raises(Exception) as e:
        c.submit_report(_report(videos=[1], pics=[2]))
    # error message should mention the backend's complaint
    assert "pic too large" in str(e.value)
