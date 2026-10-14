#!/usr/bin/env python3
"""Submit one Shanghai traffic report through its authenticated HTTP APIs."""

from __future__ import annotations

import datetime as dt
import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
BASE = "https://sh.122.gov.cn"
REPORT_PATH = ROOT / "report.json"
PROFILE_PATH = ROOT / "profile.json"
COOKIE_PATH = ROOT / ".api-session.cookies"
ARTIFACTS = ROOT / "artifacts-api"
UPLOAD_SCRIPT = ROOT / "vod-upload.mjs"


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.controls: dict[str, dict] = {}
        self.forms: list[dict] = []
        self.form: dict | None = None
        self.select: dict | None = None
        self.option: dict | None = None

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs_list)
        if tag == "form":
            self.form = {"attrs": attrs, "controls": []}
            self.forms.append(self.form)
        elif tag in {"input", "textarea", "button", "select"}:
            control = {"tag": tag, **attrs}
            if self.form:
                self.form["controls"].append(control)
            if attrs.get("id"):
                self.controls[attrs["id"]] = control
            if tag == "select":
                control["options"] = []
                self.select = control
        elif tag == "option" and self.select is not None:
            self.option = {"value": attrs.get("value", ""), "text": ""}
            self.select["options"].append(self.option)

    def handle_data(self, data: str) -> None:
        if self.option is not None:
            self.option["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "option":
            self.option = None
        elif tag == "select":
            self.select = None
        elif tag == "form":
            self.form = None


class Api:
    def __init__(self) -> None:
        if not COOKIE_PATH.is_file():
            stop("找不到 .api-session.cookies，请先完成认证。")
        self.jar = http.cookiejar.MozillaCookieJar(COOKIE_PATH)
        self.jar.load(ignore_discard=True, ignore_expires=True)
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.addheaders = [
            ("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140 Safari/537.36"),
            ("Accept-Language", "zh-CN,zh;q=0.9"),
        ]

    def request(
        self,
        url: str,
        data: dict | None = None,
        json_body: bool = False,
        referer: str | None = None,
    ) -> tuple[bytes, str, str]:
        body = None
        headers = {"Referer": referer or f"{BASE}/jb/page/default.jsp"}
        if data is not None:
            if json_body:
                body = json.dumps(data, ensure_ascii=False).encode()
                headers["Content-Type"] = "application/json; charset=utf-8"
            else:
                body = urllib.parse.urlencode(data).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        req = urllib.request.Request(url, data=body, headers=headers)
        with self.opener.open(req, timeout=45) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return raw, raw.decode(charset, errors="replace"), response.geturl()

    def signature(self) -> str:
        _, text, _ = self.request(f"{BASE}/jb/S")
        value = json.loads(text).get("result")
        if not value:
            stop("平台没有返回腾讯 VOD 上传签名。")
        return value

    def vod(self, action: str, payload: dict) -> dict:
        url = f"https://vod2.qcloud.com/v3/index.php?Action={action}"
        _, text, _ = self.request(url, payload, json_body=True)
        result = json.loads(text)
        if result.get("code") != 0:
            # Do not echo API request/credential details in the error.
            stop(f"腾讯 VOD {action} 失败，返回码 {result.get('code')}：{result.get('message', '未知错误')}")
        return result.get("data", {})

    def save_cookies(self) -> None:
        self.jar.save(ignore_discard=True, ignore_expires=True)
        os.chmod(COOKIE_PATH, 0o600)


def stop(message: str) -> None:
    raise RuntimeError(message)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        stop(f"读取 {path.name} 失败：{exc}")


def require(value: str, label: str) -> str:
    if not value:
        stop(f"缺少字段：{label}")
    return value


def select_value(parser: PageParser, control_id: str, label: str, display: str) -> str:
    control = parser.controls.get(control_id)
    if not control:
        stop(f"认证表单缺少 {label} 下拉框")
    for option in control.get("options", []):
        if option.get("text", "").strip() == display:
            return option.get("value", "")
    choices = "、".join(o.get("text", "").strip() for o in control.get("options", []))
    stop(f"平台当前没有“{display}”这个{label}选项；可选项：{choices}")


def resolve_location(api: Api, location: str, form_url: str) -> str:
    normalize = lambda value: re.sub(r"[^\u4e00-\u9fff0-9A-Za-z]", "", value)
    terms = re.findall(r"[\u4e00-\u9fff]{2,}", location)
    # Long descriptions often contain a road name plus direction/landmark text;
    # query road-name-sized pieces too so the portal can return its canonical label.
    road_terms = re.findall(
        r"[\u4e00-\u9fff]{2,}?(?:公路|高速|大道|大街|街|路|桥|隧道)", location
    )
    terms = list(dict.fromkeys([*terms, *road_terms]))
    found: dict[str, dict] = {}
    for term in terms:
        _, text, _ = api.request(
            f"{BASE}/jb/query",
            {"data_val": term},
            referer=form_url,
        )
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            continue
        for item in (result[0].get("rets", []) if result else []):
            canonical = item.get("dlmcnew", "")
            display = item.get("dlmc", "")
            if canonical:
                found[normalize(canonical)] = item
            if display:
                found[normalize(display)] = item
    match = found.get(normalize(location))
    if not match:
        options = list(dict.fromkeys(item.get("dlmcnew", "") for item in found.values() if item.get("dlmcnew")))
        candidates = "、".join(options[:20]) or "（本次查询没有返回候选项）"
        stop(
            f"官网道路联想库没有与地点精确匹配的道路：{location}。"
            f"查询候选项：{candidates}。请选取确实对应举报发生处的官网道路/路口选项；"
            "当前这一项不会再次上传视频或提交。"
        )
    return match["dlmcnew"]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_pending(path: Path, value: dict) -> None:
    ARTIFACTS.mkdir(exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.chmod(path, 0o600)


def split_plate(plate: str, label: str) -> tuple[str, str]:
    if not plate:
        stop(f"缺少{label}")
    prefixes = ["WJ", *list("沪苏浙皖鲁粤赣京藏川鄂甘桂贵黑吉冀津晋辽蒙闽宁青琼陕湘新渝豫云使CLSJN GHKVBZEX".replace(" ", ""))]
    prefix = next((p for p in sorted(prefixes, key=len, reverse=True) if plate.startswith(p)), None)
    if not prefix or not plate[len(prefix):]:
        stop(f"无法解析{label}：{plate}")
    return prefix, plate[len(prefix):].upper()


def infer_plate_color(plate: str) -> str | None:
    """Infer common mainland plate color from the user-specified plate length."""
    compact = re.sub(r"\s+", "", plate)
    if compact and compact[0] in "沪苏浙皖鲁粤赣京藏川鄂甘桂贵黑吉冀津晋辽蒙闽宁青琼陕湘新渝豫云":
        if len(compact) == 7:
            return "蓝色牌照"
        if len(compact) == 8:
            return "绿色牌照"
    return None


def discover_form(api: Api) -> tuple[str, str, PageParser]:
    redirect = (ARTIFACTS / "authenticated-form.html").read_text(errors="replace")
    match = re.search(r"save\.jsp\?s=(\d+)", redirect)
    if not match:
        stop("认证记录中没有后续表单地址；请重新完成认证。")
    _, html, final_url = api.request(f"{BASE}/jb/page/save.jsp?s={match.group(1)}")
    parser = PageParser()
    parser.feed(html)
    needed = {"val3", "val4", "val9", "val1", "val111", "val0", "val12", "val13", "val14", "val2", "val21", "val22", "val23", "myid"}
    if not needed.issubset(parser.controls):
        stop(f"当前 API 会话未返回举报表单（页面：{final_url}）；请重新认证。")
    return final_url, html, parser


def validate_report(report: dict, profile: dict) -> tuple:
    video_value = require(report.get("video", ""), "video")
    video = Path(video_value)
    if not video.is_absolute():
        video = ROOT / video
    if not video.is_file():
        stop(f"找不到视频文件：{video}")
    if video.stat().st_size > 100 * 1024 * 1024:
        stop("视频超过网站 100 MB 限制。")

    probe = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration", "-of", "json", str(video),
    ], capture_output=True, text=True)
    if probe.returncode != 0:
        stop("ffprobe 无法读取视频，未提交。")
    metadata = json.loads(probe.stdout)
    stream = metadata.get("streams", [{}])[0]
    duration = float(metadata.get("format", {}).get("duration", 0))
    width = stream.get("width", 0)
    height = stream.get("height", 0)
    if duration < 5:
        stop(f"视频时长仅 {duration:.1f} 秒，太短，未提交。")
    guidance = []
    if duration > 60:
        guidance.append(f"时长 {duration:.1f} 秒超过 60 秒")
    if width > 1280 or height > 720:
        guidance.append(f"分辨率 {width}x{height} 超过 1280x720")
    if guidance:
        print("提示：原始视频 " + "；".join(guidance) +
              "；按用户要求将原文件上传，不裁剪、不缩放、不转码；平台可能拒收。")

    occurred = require(report.get("occurredAt", ""), "occurredAt")
    try:
        when = dt.datetime.strptime(occurred, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    except ValueError:
        stop("occurredAt 必须使用 YYYY-MM-DD HH:mm:ss。")
    now = dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    if when > now:
        stop("违法时间晚于当前时间，未提交。")
    if now - when > dt.timedelta(days=10):
        stop("违法时间超过平台 10 日受理窗口，未提交。")

    name = require(profile.get("reporterName", ""), "profile.reporterName")
    phone = require(profile.get("phone", ""), "profile.phone")
    id_number = require(profile.get("idNumber", ""), "profile.idNumber")
    if not re.fullmatch(r"1\d{10}", phone):
        stop("默认手机号格式不正确。")
    if not re.fullmatch(r"[\u4e00-\u9fa5·]{2,64}", name):
        stop("默认举报人姓名格式不符合平台要求。")
    if not re.fullmatch(r"\d{17}[\dXx]", id_number):
        stop("默认身份证号码格式不正确。")
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_chars = "10X98765432"
    expected_check = check_chars[
        sum(int(digit) * weight for digit, weight in zip(id_number[:17], weights)) % 11
    ]
    if expected_check != id_number[-1].upper():
        stop("默认身份证号码校验位不正确。")

    reported_prefix, reported_suffix = split_plate(require(report.get("reportedPlate", ""), "reportedPlate"), "被举报车牌")
    reporter_prefix, reporter_suffix = split_plate(require(profile.get("reporterPlate", ""), "profile.reporterPlate"), "举证车牌")
    if not re.fullmatch(r"[0-9A-Z]{5,7}[0-9A-Z警领港澳挂学]", reported_suffix):
        stop("被举报车牌后缀格式不符合平台要求。")
    if not re.fullmatch(r"[0-9A-Z]{5,7}[0-9A-Z警领港澳挂学]", reporter_suffix):
        stop("举证车牌后缀格式不符合平台要求。")

    location = require(report.get("location", ""), "location")
    if len(location) > 64:
        stop("违法地点超过平台 64 字限制。")
    violation = require(report.get("violation", ""), "violation")
    plate_color = (report.get("plateColor") or "").strip()
    if not plate_color:
        plate_color = infer_plate_color(report["reportedPlate"])
    if not plate_color:
        stop("缺少 plateColor，且无法根据号牌格式推断。")
    return video, name, phone, id_number, location, occurred, reported_prefix, reported_suffix, reporter_prefix, reporter_suffix, violation, plate_color


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("report", nargs="?", type=Path, default=REPORT_PATH,
                     help="report JSON (default: report.json in this directory)")
    args = cli.parse_args()
    report = read_json(args.report)
    profile = read_json(PROFILE_PATH)
    (video, name, phone, id_number, location, occurred, reported_prefix, reported_suffix,
     reporter_prefix, reporter_suffix, violation, plate_color) = validate_report(report, profile)
    if not report.get("plateColor"):
        print(f"号牌颜色根据 {len(report['reportedPlate'])} 位号牌推断为：{plate_color}。")

    api = Api()
    form_url, html, page = discover_form(api)
    controls = page.controls
    # The portal sometimes leaves name and ID blank even for an authenticated real-name account.
    if controls["val4"].get("value") != phone:
        stop("表单手机号与默认实名档案不一致，未上传。")

    plate_color_code = select_value(page, "val14", "号牌颜色", plate_color)
    portal_violation = {
        "逆行": "逆向行驶",
        "加塞": "加塞（前方车辆排队时借道超车或占道穿插排队车辆）",
    }.get(violation, violation)
    select_value(page, "val2", "违法行为", portal_violation)
    select_value(page, "val12", "号牌省份", reported_prefix)
    select_value(page, "val21", "举证车牌省份", reporter_prefix)
    portal_location = resolve_location(api, location, form_url)

    address_data = {
        "val0": occurred,
        "val111": portal_location,
        "val2": portal_violation,
        "val3": name,
        "val4": phone,
        "val9": id_number,
        "val12": reported_prefix,
        "val13": reported_suffix,
        "val14": plate_color_code,
        "val21": reporter_prefix,
        "val22": reporter_suffix,
        "val23": report.get("note", ""),
    }

    print("实名会话有效；举报字段、受理日期、车牌选项和视频规格已核对。")
    print(f"官网地点选项：{portal_location}")
    if portal_violation != violation:
        print(f"违法行为使用官网选项名称：{violation} → {portal_violation}")
    print(f"视频：{video.name}（{video.stat().st_size:,} bytes）")

    video_type = video.suffix.lstrip(".").lower()
    if video_type not in {"mp4", "mov", "3gp", "mp4v", "m4v", "mkv", "avi", "flv", "f4v", "mpg", "mpeg", "mpe", "vob", "da", "rm", "rmvb", "ra", "ram", "wmv", "wm", "asf", "asx", "ts"}:
        stop(f"平台不支持视频格式：.{video_type}")

    content_hash = file_sha256(video)
    pending_path = ARTIFACTS / f"pending-{content_hash[:16]}.json"
    pending = json.loads(pending_path.read_text()) if pending_path.exists() else None
    if pending and pending.get("save_attempted"):
        stop("此前 /jb/save 结果不明确。请先在官网查询已上传记录，不要直接重试。")
    if pending and pending.get("file_id"):
        file_id = pending["file_id"]
        print("复用本机暂存的视频上传 ID。")
    else:
        api.vod("PrepareUploadUGC", {"signature": api.signature()})
        apply_result = api.vod("ApplyUploadUGC", {
            "signature": api.signature(),
            "videoName": video.stem,
            "videoType": video_type,
            "videoSize": video.stat().st_size,
            "uploadFromWeb": True,
        })

        upload = subprocess.run(
            ["node", str(UPLOAD_SCRIPT)],
            input=json.dumps({"videoPath": str(video), "apply": apply_result}),
            text=True,
            capture_output=True,
        )
        if upload.stderr:
            print(upload.stderr, end="", file=sys.stderr)
        if upload.returncode != 0:
            stop("腾讯 COS 视频上传失败；尚未提交举报。请检查网络后再确认上传状态。")

        vod_session = apply_result.get("vodSessionKey")
        if not vod_session:
            stop("视频已传到 COS，但 VOD 响应缺少 vodSessionKey；未提交举报。")
        commit_result = api.vod("CommitUploadUGC", {
            "signature": api.signature(),
            "vodSessionKey": vod_session,
        })
        file_id = commit_result.get("fileId") or commit_result.get("FileId")
        if not file_id:
            stop("视频已上传并提交到 VOD，但没有返回 fileId；未提交举报。")
        pending = {"video_sha256": content_hash, "file_id": file_id, "save_attempted": False}
        write_pending(pending_path, pending)

    payload = {**address_data, "val6": file_id}
    # This is the one and only report submission call. Never automatically retry it.
    pending["save_attempted"] = True
    write_pending(pending_path, pending)
    try:
        _, response_text, _ = api.request(f"{BASE}/jb/save", payload)
    except Exception as exc:
        stop(f"/jb/save 请求结果不确定（未自动重试，避免重复举报）：{exc}")
    response_path = ARTIFACTS / "last-submit-response.txt"
    ARTIFACTS.mkdir(exist_ok=True)
    response_path.write_text(response_text)
    os.chmod(response_path, 0o600)
    api.save_cookies()

    try:
        result = json.loads(response_text)
        item = result[0] if isinstance(result, list) else result
    except (json.JSONDecodeError, IndexError, TypeError):
        stop(f"/jb/save 已调用一次，但响应不是预期 JSON；请通过官网‘查询已上传记录’核实，不要直接重试。响应已保存在 {response_path}。")

    if str(item.get("zt")) == "1":
        pending_path.unlink(missing_ok=True)
        print(f"举报提交成功：{item.get('rets', '平台已受理')}")
        return 0
    pending["save_attempted"] = False
    write_pending(pending_path, pending)
    stop(f"平台未确认受理：{item.get('rets', '未知错误')}。视频已上传，举报数据未确认保存。")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as error:
        raise SystemExit(f"HTTP {error.code}：平台/API 请求失败。")
    except urllib.error.URLError as error:
        raise SystemExit(f"网络请求失败：{error.reason}")
    except RuntimeError as error:
        raise SystemExit(str(error))
