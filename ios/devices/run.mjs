// The two-device check: what is set by hand on the Mac reaches the phone,
// and back, through the same files iCloud Drive carries.
//
// The Mac's half runs on the Mac's own Python (mac.py, cryptography 50); the
// phone's half is the bundled engine (Pyodide, cryptography 47), started as
// the app starts it. A folder on this machine stands in for iCloud Drive.
//
// Run after npm run bundle, from ios/: npm run devices
import { loadPyodide } from "../../web/public/engine/pyodide.js";
import createPyodideModule from "../../web/public/engine/pyodide.asm.js";
import { bootEngine } from "../../web/src/phone/boot.ts";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");
const work = mkdtempSync(path.join(tmpdir(), "tastydesk-devices-"));
const folder = path.join(work, "Tasty Desk Sync");
const macDb = path.join(work, "mac.db");
execFileSync("mkdir", ["-p", folder]);

const failures = [];
const expect = (ok, what) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${what}`);
  if (!ok) failures.push(what);
};
const mac = (action) =>
  JSON.parse(
    execFileSync("uv", ["run", "--quiet", "python", path.join(here, "mac.py"), action, folder, macDb], {
      cwd: repo,
      stdio: ["ignore", "pipe", "inherit"],
    }).toString(),
  );
const readFolder = () =>
  Object.fromEntries(
    readdirSync(folder)
      .filter((n) => n.endsWith(".tdsync"))
      .map((n) => [n, new Uint8Array(readFileSync(path.join(folder, n)))]),
  );

console.log("1/3 The Mac names a strategy and shares it...");
const first = mac("write");
expect(first.report.state === "ok" && first.report.sent > 0, `the Mac shared ${first.report.sent} records`);

console.log("2/3 The phone engine takes it in, and changes something...");
const engine = await bootEngine({
  indexURL: path.resolve(repo, "web/public/engine") + "/",
  loadPyodide,
  createPyodideModule,
  readFile: async (file) => new Uint8Array(readFileSync(file)),
  stdout: () => {},
  stderr: () => {},
});
engine.setCredentials("device-check-secret", "not-a-real-token");
await engine.start();
// The phone's own tastytrade sync would bring the account number; this
// check has no network, so it is put in directly.
await engine.pyodide.runPythonAsync(`
from tastydesk.api.main import svc
await svc()._db.connection.execute("INSERT INTO transactions (id, account_number) VALUES (1, '5WX00001')")
await svc()._db.connection.commit()
`);
const got = await engine.deviceSync(readFolder());
expect(got.report.state === "ok", `the phone read the folder (${JSON.stringify(got.report.problems ?? [])})`);
expect(got.report.received > 0, `the phone took in ${got.report.received} records`);
const named = JSON.parse((await engine.request("GET", "/api/strategies/named", "", [], "")).body);
const zb = (Array.isArray(named) ? named : named.strategies ?? []).find((n) => n.id === "zb");
expect(zb?.name === "/ZB 112 straddle", `the strategy is on the phone: ${zb?.name}`);
const settings = JSON.parse((await engine.request("GET", "/api/settings", "", [], "")).body);
expect(String(settings.match_threshold) === "0.9", `the Mac's setting is on the phone: ${settings.match_threshold}`);

const changed = await engine.request(
  "POST",
  "/api/settings",
  "",
  [["content-type", "application/json"]],
  JSON.stringify({ key: "match_threshold", value: "0.75" }),
);
expect(changed.status === 200, `the phone changes a setting -> ${changed.status}`);
const back = await engine.deviceSync(readFolder());
expect(back.outgoing instanceof Uint8Array, "the phone writes its file");
writeFileSync(path.join(folder, back.filename), back.outgoing);
const quiet = await engine.deviceSync(readFolder());
expect(quiet.outgoing === null, "a round with nothing new writes nothing");

console.log("3/3 The Mac takes in the phone's change...");
const last = mac("read");
expect(last.match_threshold === "0.75", `the phone's setting is on the Mac: ${last.match_threshold}`);
expect(JSON.stringify(last.named.zb) === JSON.stringify(["/ZB 112 straddle", ["t1", "t2"]]), "the strategy is unchanged");
expect(JSON.stringify(last.overrides) === JSON.stringify({ s2: "s1" }), "the link is unchanged");

await engine.stop();
if (failures.length) {
  console.log(`\n${failures.length} check(s) failed. Files kept in ${work}`);
  process.exit(1);
}
rmSync(work, { recursive: true, force: true });
console.log("\nThe Mac and the phone agree.");
