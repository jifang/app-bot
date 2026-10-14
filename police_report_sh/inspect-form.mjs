import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const ENTRY_URL = "https://sh.122.gov.cn/jb/page/save.jsp?s=40298542400";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const LOGIN_PATH = "/jb/page/save.jsp";

async function readConfig() {
  const configPath = path.join(ROOT, "config.json");
  try {
    return JSON.parse(await fs.readFile(configPath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return {};
    throw new Error(`无法读取 config.json: ${error.message}`);
  }
}

async function describeForm(page) {
  return page.locator("input, select, textarea, button").evaluateAll((elements) =>
    elements.map((element, index) => {
      const id = element.id || null;
      const explicitLabel = id
        ? document.querySelector(`label[for="${CSS.escape(id)}"]`)?.innerText
        : null;
      const wrappingLabel = element.closest("label")?.innerText;
      const nearbyText = element.parentElement?.innerText?.slice(0, 240);
      const options = element.tagName === "SELECT"
        ? [...element.options].map((option) => ({ value: option.value, text: option.text }))
        : undefined;
      return {
        index,
        tag: element.tagName.toLowerCase(),
        type: element.getAttribute("type"),
        id,
        name: element.getAttribute("name"),
        placeholder: element.getAttribute("placeholder"),
        value: element.type === "password" ? "<redacted>" : element.value,
        label: (explicitLabel || wrappingLabel || "").trim() || null,
        nearbyText: nearbyText?.trim() || null,
        options,
      };
    }),
  );
}

async function saveArtifacts(page) {
  const artifactDir = path.join(ROOT, "artifacts");
  await fs.mkdir(artifactDir, { recursive: true });
  const snapshot = {
    capturedAt: new Date().toISOString(),
    url: page.url(),
    title: await page.title(),
    controls: await describeForm(page),
  };
  await fs.writeFile(
    path.join(artifactDir, "form-schema.json"),
    `${JSON.stringify(snapshot, null, 2)}\n`,
    { mode: 0o600 },
  );
  await page.screenshot({ path: path.join(artifactDir, "form.png"), fullPage: true });
  console.log("\n已保存登录后页面结构：artifacts/form-schema.json");
  console.log("已保存页面截图：artifacts/form.png");
}

const config = await readConfig();
const phone = process.env.SH_REPORT_PHONE || config.phone;

const context = await chromium.launchPersistentContext(
  path.join(ROOT, ".browser-profile"),
  {
    executablePath: CHROME,
    headless: false,
    viewport: null,
    args: ["--start-maximized"],
  },
);

const pages = context.pages();
const page = pages[0] || await context.newPage();
await page.goto(ENTRY_URL, { waitUntil: "domcontentloaded" });

if (phone) {
  if (!/^1\d{10}$/.test(phone)) {
    throw new Error("手机号格式不正确；请检查 config.json 或 SH_REPORT_PHONE。");
  }
  await page.locator("#sjh").fill(phone);
}

console.log("上海交通违法视频举报平台已打开。");
console.log("请在浏览器中手工完成图形验证码、短信验证码和责任声明确认。");
console.log("脚本不会自动发送短信，也不会提交任何举报。");

let captured = false;
while (!captured && !page.isClosed()) {
  await page.waitForTimeout(1000);
  const url = new URL(page.url());
  if (url.pathname !== LOGIN_PATH) {
    await page.waitForLoadState("domcontentloaded").catch(() => {});
    await saveArtifacts(page);
    captured = true;
  }
}

if (captured) {
  console.log("请检查页面是否为举报表单，然后可关闭浏览器结束脚本。");
  await page.waitForEvent("close").catch(() => {});
}
await context.close();
