#!/usr/bin/env python3
"""Interactively authenticate to the Shanghai traffic-report API and inspect its form.

This intentionally sends at most one SMS per process and never submits a report.
"""

from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_URL = "http://sh.122.gov.cn"
ENTRY_PATH = "/jb/page/default.jsp"
COOKIE_PATH = ROOT / ".api-session.cookies"
ARTIFACT_DIR = ROOT / "artifacts-api"


class FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self._form: dict | None = None
        self._select: dict | None = None
        self._option: dict | None = None

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs_list)
        if tag == "form":
            self._form = {"action": attrs.get("action"), "method": attrs.get("method", "get"), "controls": []}
            self.forms.append(self._form)
        elif self._form is not None and tag in {"input", "textarea", "button"}:
            self._form["controls"].append({"tag": tag, **attrs})
        elif self._form is not None and tag == "select":
            self._select = {"tag": tag, **attrs, "options": []}
            self._form["controls"].append(self._select)
        elif self._select is not None and tag == "option":
            self._option = {"value": attrs.get("value"), "text": ""}
            self._select["options"].append(self._option)

    def handle_data(self, data: str) -> None:
        if self._option is not None:
            self._option["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "option":
            self._option = None
        elif tag == "select":
            self._select = None
        elif tag == "form":
            self._form = None


class ApiClient:
    def __init__(self) -> None:
        self.cookies = http.cookiejar.MozillaCookieJar(COOKIE_PATH)
        if COOKIE_PATH.exists():
            self.cookies.load(ignore_discard=True, ignore_expires=True)
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.opener.addheaders = [
            ("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140 Safari/537.36"),
            ("Accept-Language", "zh-CN,zh;q=0.9"),
        ]

    def request(self, path: str, data: dict[str, str] | None = None) -> tuple[bytes, str, str]:
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        request = urllib.request.Request(
            urllib.parse.urljoin(BASE_URL, path),
            data=body,
            headers={"Referer": urllib.parse.urljoin(BASE_URL, ENTRY_PATH)},
        )
        with self.opener.open(request, timeout=30) as response:
            raw = response.read()
            content_type = response.headers.get_content_charset() or "utf-8"
            return raw, raw.decode(content_type, errors="replace"), response.geturl()

    def save(self) -> None:
        self.cookies.save(ignore_discard=True, ignore_expires=True)
        os.chmod(COOKIE_PATH, 0o600)


def load_profile() -> dict:
    try:
        return json.loads((ROOT / "profile.json").read_text())
    except FileNotFoundError:
        raise SystemExit("缺少 profile.json；请先复制并填写 profile.example.json")


def hidden_fields(html: str) -> dict[str, str]:
    fields = {}
    for match in re.finditer(r'<input\b[^>]*type=["\']hidden["\'][^>]*>', html, re.I):
        tag = match.group(0)
        name = re.search(r'\bname=["\']([^"\']+)', tag, re.I)
        value = re.search(r'\bvalue=["\']([^"\']*)', tag, re.I)
        if name:
            fields[name.group(1)] = value.group(1) if value else ""
    return fields


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send-sms",
        action="store_true",
        help="send one authentication SMS without the interactive YES prompt",
    )
    args = parser.parse_args()

    profile = load_profile()
    phone = profile.get("phone", "")
    if not re.fullmatch(r"1\d{10}", phone):
        raise SystemExit("profile.json 中的 phone 格式不正确")

    client = ApiClient()
    _, entry_html, _ = client.request(ENTRY_PATH)
    fields = hidden_fields(entry_html)
    if "sn" not in fields:
        raise SystemExit("入口页面缺少 sn，网站流程可能已变化")

    print(f"将向默认手机号 {phone[:3]}****{phone[-4:]} 发送一次短信验证码。")
    if not args.send_sms:
        consent = input("确认已阅读平台公告、愿意承担法律责任并发送？输入 YES：").strip()
        if consent != "YES":
            print("已取消，未发送短信。")
            return 1

    _, send_text, _ = client.request("/jb/sendMsg", {"sjhm": phone, "code": "1"})
    print(f"短信接口响应：{send_text[:200] or '<empty>'}")
    sms_code = getpass.getpass("请输入收到的 6 位短信验证码：").strip()
    if not re.fullmatch(r"\d{6}", sms_code):
        raise SystemExit("短信验证码必须是 6 位数字")

    _, verify_text, _ = client.request("/jb/jy", {"val17": sms_code, "code": "1", "sjhm": phone})
    try:
        verify_result = json.loads(verify_text)
    except json.JSONDecodeError:
        raise SystemExit(f"短信校验接口返回了非 JSON 内容：{verify_text[:200]}")
    if not verify_result or str(verify_result[0].get("zt")) != "1":
        raise SystemExit("短信验证码校验失败；为避免频繁发送，本次进程不会重发短信")

    payload = {**fields, "sjh": phone, "yzm": sms_code}
    _, form_html, final_url = client.request("/jb/check", payload)
    client.save()

    ARTIFACT_DIR.mkdir(exist_ok=True)
    html_path = ARTIFACT_DIR / "authenticated-form.html"
    schema_path = ARTIFACT_DIR / "authenticated-form.json"
    html_path.write_text(form_html)
    parser = FormParser()
    parser.feed(form_html)
    schema_path.write_text(json.dumps({"url": final_url, "forms": parser.forms}, ensure_ascii=False, indent=2) + "\n")
    os.chmod(html_path, 0o600)
    os.chmod(schema_path, 0o600)

    print(f"认证成功，未提交举报。\n已保存：{html_path}\n已保存：{schema_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:500]
        raise SystemExit(f"HTTP {error.code}: {detail}")
    except urllib.error.URLError as error:
        raise SystemExit(f"网络请求失败：{error.reason}")
