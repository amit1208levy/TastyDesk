// The layout check: every tab, at an iPhone's width, fits the iPhone.
//
// The phone shows the Mac's own pages, so a change made for the Mac lands on
// the phone too. This catches the ways that goes wrong on a narrow screen:
//   - a page wider than the screen (it scrolls sideways);
//   - a figure cut short ("$96.…" instead of "$96.4k");
//   - the header pushed past the edge.
// It runs the Mac's server on the parity check's fake account and the pages
// from web/ in Vite, opens each tab at 393 x 852 (iPhone 15/16/17), 440 x 956
// (the Pro Max) and an iPad's 820 x 1180 both ways up, and fails naming the
// size, the tab and what does not fit. Screenshots of every tab land in
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
const PHONES = [
  { name: "iPhone", width: 393, height: 852 },
  { name: "Pro Max", width: 440, height: 956 },
  // An iPad, upright and on its side: the app's frame around the wide layouts.
  { name: "iPad", width: 820, height: 1180 },
  { name: "iPad landscape", width: 1180, height: 820 },
];
const API = "http://127.0.0.1:8787";
const PAGES = "http://127.0.0.1:5273";

const children = [];
// What the two servers printed, shown only if one of them does not come up.
let logs = "";
// Each in a process group of its own, stopped as a group: Vite and uv start
// children of their own, and killing only the parent left them running.
function start(cmd, args, cwd) {
  const child = spawn(cmd, args, { cwd, stdio: ["ignore", "pipe", "pipe"], detached: true });
  const keep = (chunk) => {
    logs = (logs + chunk).slice(-4000);
  };
  child.stdout.on("data", keep);
  child.stderr.on("data", keep);
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
  console.log(logs);
  throw new Error(`${what} did not start`);
}

for (const url of [API, PAGES]) {
  const busy = await fetch(url).then(() => true, () => false);
  if (busy) throw new Error(`Something is already running at ${url}; stop it first.`);
}
start("uv", ["run", "--quiet", "python", path.join(here, "serve.py")], repo);
start(path.join(repo, "web/node_modules/.bin/vite"), ["--host", "127.0.0.1", "--port", "5273", "--strictPort"], path.join(repo, "web"));
await waitFor(`${API}/api/health`, "The Mac's server");
await waitFor(PAGES, "Vite");
const sync = await fetch(`${API}/api/sync`, { method: "POST" });
if (!sync.ok) throw new Error(`Sync on the fake account failed: ${sync.status}`);

mkdirSync(shots, { recursive: true });
const browser = await chromium.launch();
const problems = [];
let tabs = [];
for (const PHONE of PHONES) {
  const page = await browser.newPage({
    viewport: { width: PHONE.width, height: PHONE.height },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    colorScheme: "dark",
  });
  await page.goto(PAGES);
  // On a phone the tabs are in the bar at the bottom ([data-tabs]).
  await page.waitForSelector("nav[data-tabs]");
  await page.waitForTimeout(3000);

  // The tabs, read from the bar and from the More sheet, so a new tab is checked too.
  const more = page.locator("nav[data-tabs] button[data-more]");
  const sheet = page.locator('[role="dialog"]');
  const openMenu = async () => {
    if (!(await sheet.count())) await more.click();
    await sheet.waitFor();
    await page.waitForTimeout(450);
  };
  const closeMenu = async () => {
    if (await sheet.count()) await sheet.getByRole("button", { name: "Done" }).click();
    await sheet.waitFor({ state: "detached" });
  };
  await openMenu();
  // In the sheet a row carries a line of description under its name.
  const listed = await page
    .locator("button[data-tab]")
    .evaluateAll((els) => els.map((el) => [el.getAttribute("data-tab"), (el.querySelector("span span") ?? el).textContent.trim()]));
  // The sheet itself has to fit the screen too.
  const sheetWide = await sheet.evaluate((el) => Math.max(...[...el.querySelectorAll("*")].map((n) => n.getBoundingClientRect().right)));
  if (sheetWide > PHONE.width + 1) problems.push(`${PHONE.name} More: the sheet is ${Math.round(sheetWide)}px wide`);
  await page.screenshot({ path: path.join(shots, `${PHONE.name.replace(/\W+/g, "_")}-More.png`) });
  await closeMenu();
  tabs = [...new Map(listed).entries()];

  for (const [id, tab] of tabs) {
    const button = page.locator(`button[data-tab="${id}"]`);
    if (!(await button.count())) await openMenu();
    await button.click();
    if (await sheet.count()) await sheet.waitFor({ state: "detached" });
    await page.waitForTimeout(3000);

    const problemsHere = await page.evaluate((width) => {
      const out = [];
      const wide = document.documentElement.scrollWidth;
      if (wide > width) out.push(`the page is ${wide}px wide on a ${width}px screen`);
      for (const el of document.querySelectorAll("header button, header nav")) {
        const r = el.getBoundingClientRect();
        if (r.width && r.right > width + 1) out.push(`the header runs off the screen ("${el.textContent.trim().slice(0, 30)}")`);
      }
      for (const el of document.querySelectorAll("nav[data-tabs] button")) {
        const r = el.getBoundingClientRect();
        if (r.width && (r.right > width + 1 || el.scrollWidth > el.clientWidth + 1)) {
          out.push(`the tab "${el.textContent.trim()}" does not fit the bar`);
        }
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

    await page.screenshot({ path: path.join(shots, `${PHONE.name.replace(/\W+/g, "_")}-${tab.replace(/\W+/g, "_")}-screen.png`) });
    // The whole page too, on a phone only: a whole-page screenshot leaves
    // Chromium no longer emulating a touch screen, which is what gives an
    // iPad the app's frame (a phone has it by its width).
    if (PHONE.width < 640) {
      await page.screenshot({ path: path.join(shots, `${PHONE.name.replace(/\W+/g, "_")}-${tab.replace(/\W+/g, "_")}.png`), fullPage: true });
    }

    // A position's detail comes up as a sheet: it has to fit the screen as well.
    if (id === "positions") {
      const card = page.locator('[data-positions-card] li [role="button"]').first();
      if (await card.count()) {
        await card.click();
        await sheet.waitFor();
        await page.waitForTimeout(1500);
        const wide = await sheet.evaluate((el) => {
          const inScroller = (n) => n.closest(".overflow-x-auto") && !n.classList.contains("overflow-x-auto");
          return Math.max(...[...el.querySelectorAll("*")].filter((n) => !inScroller(n)).map((n) => n.getBoundingClientRect().right));
        });
        if (wide > PHONE.width + 1) {
          problemsHere.push(`the position sheet is ${Math.round(wide)}px wide`);
        }
        await page.screenshot({ path: path.join(shots, `${PHONE.name.replace(/\W+/g, "_")}-Position_sheet.png`) });
        await closeMenu();
      }
    }
    console.log(`${problemsHere.length ? "FAIL" : "ok  "} ${PHONE.name} ${tab}${problemsHere.length ? ": " + problemsHere.join("; ") : ""}`);
    for (const f of problemsHere) problems.push(`${PHONE.name} ${tab}: ${f}`);
  }
  await page.close();
}

await browser.close();
stopAll();
if (tabs.length < 5) {
  console.log(`Only ${tabs.length} tabs found; the header has changed and this check needs updating.`);
  process.exit(1);
}
if (problems.length) {
  console.log(`\n${problems.length} thing(s) do not fit. Screenshots: ios/layout/shots/`);
  process.exit(1);
}
console.log(`\nAll ${tabs.length} tabs fit an iPhone and an iPad (${PHONES.map((p) => `${p.width} x ${p.height}`).join(", ")}).`);
