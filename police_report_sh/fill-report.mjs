import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const ENTRY_URL = "https://sh.122.gov.cn/jb/page/save.jsp?s=40298542400";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

function fail(message) {
  throw new Error(message);
}

async function readReport() {
  const reportPath = process.argv[2]
    ? path.resolve(process.argv[2])
    : path.join(ROOT, "report.json");
  let report;
  try {
    report = JSON.parse(await fs.readFile(reportPath, "utf8"));
  } catch (error) {
    fail(`无法读取举报配置 ${reportPath}: ${error.message}`);
  }
  let profile = {};
  try {
    profile = JSON.parse(await fs.readFile(path.join(ROOT, "profile.json"), "utf8"));
  } catch (error) {
    if (error.code !== "ENOENT") fail(`无法读取 profile.json: ${error.message}`);
  }
  return { report: { ...profile, ...report }, reportPath };
}

async function validate(report) {
  const required = ["phone", "location", "occurredAt", "reportedPlate", "plateColor", "violation", "video"];
  for (const key of required) {
    if (!report[key]) fail(`缺少必填字段：${key}`);
  }
  if (!/^1\d{10}$/.test(report.phone)) fail("phone 不是有效的 11 位手机号");
  if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(report.occurredAt)) {
    fail("occurredAt 必须使用 YYYY-MM-DD HH:mm:ss 格式");
  }
  for (const key of ["reportedPlate", "reporterPlate"]) {
    if (report[key] && report[key].length < 2) fail(`${key} 车牌格式不完整`);
  }

  const video = path.resolve(report.video);
  const stat = await fs.stat(video).catch(() => null);
  if (!stat?.isFile()) fail(`视频文件不存在：${video}`);
  if (stat.size > 100 * 1024 * 1024) fail("视频超过网站规定的 100 MB 上限");

  const probe = spawnSync("ffprobe", [
    "-v", "error", "-select_streams", "v:0",
    "-show_entries", "stream=width,height:format=duration",
    "-of", "json", video,
  ], { encoding: "utf8" });
  if (probe.status === 0) {
    const metadata = JSON.parse(probe.stdout);
    const stream = metadata.streams?.[0] || {};
    const duration = Number(metadata.format?.duration);
    if (duration > 60.05) fail(`视频时长 ${duration.toFixed(1)} 秒，超过 1 分钟上限`);
    if (stream.width > 1280 || stream.height > 720) {
      fail(`视频分辨率 ${stream.width}x${stream.height}，超过 1280x720 上限`);
    }
  } else {
    console.warn("警告：未能调用 ffprobe，跳过时长和分辨率检查。");
  }
  report.video = video;
}

async function controlNearText(page, label, selector, ordinal = 0) {
  const handle = await page.evaluateHandle(({ label, selector, ordinal }) => {
    const normalize = (value) => value.replace(/[\s：:]/g, "");
    const wanted = normalize(label);
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
      const text = normalize(walker.currentNode.textContent || "");
      if (text === wanted || text.includes(wanted)) nodes.push(walker.currentNode);
    }
    for (const node of nodes) {
      let parent = node.parentElement;
      for (let depth = 0; parent && depth < 6; depth += 1, parent = parent.parentElement) {
        const controls = [...parent.querySelectorAll(selector)].filter((item) => item.offsetParent !== null);
        if (controls[ordinal]) return controls[ordinal];
      }
    }
    return null;
  }, { label, selector, ordinal });
  const element = handle.asElement();
  if (!element) fail(`找不到“${label}”附近的控件 ${selector}`);
  return element;
}

async function fillInput(page, label, value, ordinal = 0) {
  if (value == null || value === "") return;
  const control = await controlNearText(page, label, "input:not([type=hidden]), textarea", ordinal);
  await control.fill(value);
}

async function selectLabel(page, label, value, ordinal = 0) {
  const control = await controlNearText(page, label, "select", ordinal);
  try {
    await control.selectOption({ label: value });
  } catch {
    const options = await control.locator("option").allTextContents();
    fail(`“${label}”没有选项“${value}”。可选值：${options.join("、")}`);
  }
}

async function fillPlate(page, label, plate) {
  if (!plate) return;
  await selectLabel(page, label, plate.slice(0, 1), 0);
  await fillInput(page, label, plate.slice(1), 0);
}

async function verifyIdentity(page, label, expected, { fillIfEmpty = true } = {}) {
  if (!expected) return;
  const control = await controlNearText(page, label, "input:not([type=hidden])");
  let actual = (await control.inputValue()).trim();
  if (!actual && fillIfEmpty) {
    await control.fill(expected);
    actual = (await control.inputValue()).trim();
  }
  if (actual !== expected) {
    fail(`页面“${label}”为“${actual}”，与本机默认档案不一致；已停止填表`);
  }
}

const { report, reportPath } = await readReport();
await validate(report);

const context = await chromium.launchPersistentContext(path.join(ROOT, ".browser-profile"), {
  executablePath: CHROME,
  headless: false,
  viewport: null,
  args: ["--start-maximized"],
});
const page = context.pages()[0] || await context.newPage();
await page.goto(ENTRY_URL, { waitUntil: "domcontentloaded" });
console.log(`已读取：${reportPath}`);
const reportForm = page.locator('input[type="file"]').first();
const loginPhone = page.locator("#sjh");

if (await reportForm.isVisible().catch(() => false)) {
  console.log("检测到仍有效的登录态，已直接进入举报表单。");
} else {
  await loginPhone.waitFor({ state: "visible", timeout: 30_000 }).catch(() => {
    fail(`页面既不是短信验证页，也不是举报表单：${page.url()}`);
  });
  await loginPhone.fill(report.phone);
  console.log("当前登录态已失效，请手工完成图形验证码、短信验证码和责任声明，然后点击“继续”。");
  await reportForm.waitFor({ state: "visible", timeout: 10 * 60 * 1000 });
}

await verifyIdentity(page, "举报人姓名", report.reporterName);
await verifyIdentity(page, "举报人手机", report.phone, { fillIfEmpty: false });
await verifyIdentity(page, "身份证号码", report.idNumber);
await fillInput(page, "违法地点", report.location);
await fillInput(page, "违法时间", report.occurredAt);
await fillPlate(page, "被举报车辆号牌", report.reportedPlate);
await selectLabel(page, "号牌颜色", report.plateColor);
await selectLabel(page, "违法行为", report.violation);
await fillPlate(page, "举证车辆号牌", report.reporterPlate);
await fillInput(page, "备注", report.note || "");

const fileInput = page.locator('input[type="file"]').first();
await fileInput.setInputFiles(report.video);
console.log("\n表单和视频已在本地选好。脚本不会点击“上传”。");
console.log("请逐项核对页面内容与视频，确认准确后由你本人点击“上传”。");
await page.waitForEvent("close").catch(() => {});
await context.close();
