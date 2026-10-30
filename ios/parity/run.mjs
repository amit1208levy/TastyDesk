// The parity check: the same fake tastytrade account through the Mac's Python
// and through the phone engine (the bundle, started as the app starts it).
// Every number the screens would show must come out the same.
//
// Run after npm run bundle, from ios/: npm run parity
import { loadPyodide } from "../../web/public/engine/pyodide.js";
import createPyodideModule from "../../web/public/engine/pyodide.asm.js";
import { bootEngine } from "../../web/src/phone/boot.ts";
import { execFileSync } from "node:child_process";
import { readFileSync, rmSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");
const out = mkdtempSync(path.join(tmpdir(), "tastydesk-parity-"));

console.log("1/3 The Mac's Python (uv run)...");
const mac = execFileSync("uv", ["run", "--quiet", "python", path.join(here, "collect.py")], {
  cwd: repo,
  maxBuffer: 64 * 1024 * 1024,
  stdio: ["ignore", "pipe", "ignore"],
}).toString();
writeFileSync(path.join(out, "mac.json"), mac);

console.log("2/3 The phone engine (Pyodide)...");
const engine = await bootEngine({
  indexURL: path.join(repo, "web/public/engine") + "/",
  loadPyodide,
  createPyodideModule,
  readFile: async (file) => new Uint8Array(readFileSync(file)),
  stdout: () => {},
  stderr: () => {},
});
const py = engine.pyodide;
py.FS.mkdirTree("/parity");
py.FS.mount(py.FS.filesystems.NODEFS, { root: here }, "/parity");
// collect.py finds the bridge at ../engine/py, as in the repo.
py.FS.mkdirTree("/engine");
py.FS.mount(py.FS.filesystems.NODEFS, { root: path.join(repo, "ios/engine") }, "/engine");
const phone = await py.runPythonAsync(`
import sys
sys.path.insert(0, "/parity")
import collect
await collect.main_json()
`);
writeFileSync(path.join(out, "phone.json"), phone);

console.log("3/3 Comparing...");
try {
  const report = execFileSync(
    "uv",
    ["run", "--quiet", "python", path.join(here, "compare.py"), path.join(out, "mac.json"), path.join(out, "phone.json")],
    { cwd: repo, stdio: ["ignore", "pipe", "inherit"] },
  ).toString();
  console.log(report);
  rmSync(out, { recursive: true, force: true });
} catch (error) {
  console.log(error.stdout?.toString() ?? "");
  console.log(`Outputs kept in ${out}`);
  process.exit(1);
}
