// The layout check: every tab, at an iPhone's width, fits the iPhone.
//
// The phone shows the Mac's own pages, so a change made for the Mac lands on
// the phone too. This catches the ways that goes wrong on a narrow screen:
//   - a page wider than the screen (it scrolls sideways);
//   - a figure cut short ("$96.…" instead of "$96.4k");
//   - the header pushed past the edge.
// It runs the Mac's server on the parity check's fake account and the pages
// from web/ in Vite, opens each tab at 393 x 852 (iPhone 15/16/17), and fails
// naming the tab and what does not fit. Screenshots of every tab land in
// ios/layout/shots/ for a look.
//
// Run from ios/: npm run layout   (web/ needs npm ci first)
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");
const shots = path.join(here, "shots");
const PHONE = { width: 393, height: 852 };
const API = "http://127.0.0.1:8787";
const PAGES = "http://127.0.0.1:5273";

const children = [];
// Each in a process group of its own, stopped as a group: Vite and uv start
// children of their own, and killing only the parent left them running.
function start(cmd, args, cwd) {
  const child = spawn(cmd, args, { cwd, stdio: "ignore", detached: true });
  children.push(child);
  return child;
}
function stopAll() {
  for (const child of children.splice(0)) {
    try {
      process.kill(-child.pid);
    } catch {
      // already gone
    }
  }
}
process.on("SIGINT", () => process.exit(130));
process.on("exit", stopAll);

async function waitFor(url, what) {
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`${what} did not start`);
}

for (const url of [API, PAGES]) {
  const busy = await fetch(url).then(() => true, () => false);
  if (busy) throw new Error(`Something is already running at ${url}; stop it first.`);
}
start("uv", ["run", "--quiet", "python", path.join(here, "serve.py")], repo);
start(path.join(repo, "web/node_modules/.bin/vite"), ["--port", "5273", "--strictPort"], path.join(repo, "web"));
await waitFor(`${API}/api/health`, "The Mac's server");
await waitFor(PAGES, "Vite");
const sync = await fetch(`${API}/api/sync`, { method: "POST" });
if (!sync.ok) throw new Error(`Sync on the fake account failed: ${sync.status}`);

mkdirSync(shots, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: PHONE,
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
  colorScheme: "dark",
});
await page.goto(PAGES);
await page.waitForSelector("header nav");
await page.waitForTimeout(3000);

// The tabs, read from the header and its menu, so a new tab is checked too.
async function openMenu() {
  const more = page.locator("header nav button[aria-expanded]");
  if ((await more.count()) && (await more.getAttribute("aria-expanded")) !== "true") await more.click();
}
async function tabNames() {
  await openMenu();
  const names = await page.locator("header nav button:not([aria-expanded])").allInnerTexts();
  // The menu button toggles it shut again.
  const more = page.locator("header nav button[aria-expanded=true]");
  if (await more.count()) await more.click();
  return [...new Set(names.map((n) => n.trim()).filter(Boolean))];
}

const problems = [];
const tabs = await tabNames();
for (const tab of tabs) {
  const inBar = page.locator("header nav > span > button", { hasText: tab });
  if (await inBar.count()) await inBar.first().click();
  else {
    await openMenu();
    await page.locator("header nav button:not([aria-expanded])", { hasText: tab }).first().click();
  }
  await page.waitForTimeout(3000);

  const found = await page.evaluate((width) => {
    const out = [];
    const wide = document.documentElement.scrollWidth;
    if (wide > width) out.push(`the page is ${wide}px wide on a ${width}px screen`);
    for (const el of document.querySelectorAll("header button, header nav")) {
      const r = el.getBoundingClientRect();
      if (r.width && r.right > width + 1) out.push(`the header runs off the screen ("${el.textContent.trim().slice(0, 30)}")`);
    }
    // Figures only: a long sentence may end in "…" on purpose, a number may not.
    for (const el of document.querySelectorAll(".figure, .num")) {
      if (el.closest(".overflow-x-auto")) continue;
      if (getComputedStyle(el).textOverflow === "ellipsis" && el.scrollWidth > el.clientWidth + 1) {
        out.push(`the figure "${el.textContent.trim()}" is cut short`);
      }
    }
    return [...new Set(out)];
  }, PHONE.width);

  await page.screenshot({ path: path.join(shots, `${tab.replace(/\W+/g, "_")}.png`), fullPage: true });
  console.log(`${found.length ? "FAIL" : "ok  "} ${tab}${found.length ? ": " + found.join("; ") : ""}`);
  for (const f of found) problems.push(`${tab}: ${f}`);
}

await browser.close();
stopAll();
if (tabs.length < 5) {
  console.log(`Only ${tabs.length} tabs found; the header has changed and this check needs updating.`);
  process.exit(1);
}
if (problems.length) {
  console.log(`\n${problems.length} thing(s) do not fit an iPhone. Screenshots: ios/layout/shots/`);
  process.exit(1);
}
console.log(`\nAll ${tabs.length} tabs fit an iPhone (${PHONE.width} x ${PHONE.height}).`);
