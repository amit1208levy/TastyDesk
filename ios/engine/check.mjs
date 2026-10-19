// Starts the bundled engine exactly as the app does (web/src/phone/boot.ts)
// and asks it a few questions, with no credentials and no network needed.
// Pyodide's disk is in memory, so nothing on this machine is touched.
// Run after npm run bundle: npm run check
import { loadPyodide } from "../../web/public/engine/pyodide.mjs";
import { bootEngine } from "../../web/src/phone/boot.ts";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const indexURL = path.resolve(here, "../../web/public/engine") + "/";

const failures = [];
const expect = (ok, what) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${what}`);
  if (!ok) failures.push(what);
};

const started = Date.now();
const engine = await bootEngine({
  indexURL,
  loadPyodide,
  readFile: async (file) => new Uint8Array(readFileSync(file)),
  stdout: () => {},
  stderr: (line) => console.error("  python:", line),
});
console.log(`Engine loaded in ${((Date.now() - started) / 1000).toFixed(1)}s (commit ${engine.commit})`);

expect(!engine.hasCredentials(), "starts with no credentials");
await engine.start();
expect(true, "start() returns without credentials or network");

const get = async (p) => engine.request("GET", p, "", [["accept", "application/json"]], "");

const health = await get("/api/health");
expect(health.status === 200, `GET /api/health -> ${health.status}`);
expect(JSON.parse(health.body).credentials_present === false, "health says credentials are missing");

const open = await get("/api/strategies/open");
expect(open.status === 200 && open.body === "[]", `GET /api/strategies/open -> ${open.status} ${open.body}`);

const summary = await get("/api/portfolio/summary");
expect(summary.status === 503, `GET /api/portfolio/summary without credentials -> ${summary.status}`);

const saved = await engine.request(
  "POST",
  "/api/settings",
  "",
  [["content-type", "application/json"]],
  JSON.stringify({ key: "position_columns", value: JSON.stringify(["underlying", "open_pnl"]) }),
);
expect(saved.status === 200, `POST /api/settings -> ${saved.status}`);

const copy = engine.exportDb();
expect(copy instanceof Uint8Array && copy.length > 0, `exportDb() after a change -> ${copy?.length} bytes`);
expect(engine.exportDb() === null, "exportDb() with nothing new -> null");
const report = engine.checkDb(copy);
expect(report.ok && report.counts.settings >= 1, `checkDb(copy) -> ${JSON.stringify(report)}`);
expect(!engine.checkDb(new Uint8Array(4096)).ok, "checkDb(zeros) is refused");

const imported = await engine.importDb(copy);
expect(imported.ok && imported.saved?.length > 0, "importDb(copy) restarts and saves at once");
const after = JSON.parse((await get("/api/settings")).body);
expect(after.position_columns.includes("open_pnl"), "the imported settings are in force");

engine.setCredentials("not-a-real-secret", "not-a-real-token");
expect(engine.hasCredentials(), "setCredentials() is seen by the app");
engine.clearCredentials();
expect(!engine.hasCredentials(), "clearCredentials() removes them");

await engine.stop();
console.log(failures.length ? `\n${failures.length} check(s) failed` : "\nAll checks passed");
process.exit(failures.length ? 1 : 0);
