// Writes engine/pyodide-lock.json: every Python package the phone engine
// loads, with its exact version, file and SHA-256.
//
// Run it only when a dependency changes (npm run lock), then commit the lock.
// The app build (npm run bundle) downloads exactly what the lock names and
// refuses any file whose hash differs.
//
// Versions follow the Mac's uv.lock wherever a WebAssembly build exists, so
// the phone runs the same libraries the Mac does. The exceptions are listed
// in EXCEPTIONS and must be justified there.
import { loadPyodide } from "pyodide";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const uvLock = readFileSync(path.join(here, "../../uv.lock"), "utf8");

// What the engine imports, directly or through tastydesk.
const SAME_AS_MAC = [
  "tastytrade",
  "fastapi",
  "starlette",
  "httpx",
  "httpx2",
  "httpx2-jsfetch",
  // tastytrade asks for httpx2[ws]; micropip does not follow the extra.
  "wsproto",
  "h11",
  "anyio",
  "pandas-market-calendars",
  "exchange-calendars",
];

// numpy and pandas come in through exchange-calendars and are compiled, so
// they are Pyodide's own builds (numpy 2.4.6 and pandas 3.0.2 against the
// Mac's 2.5.3 and 3.0.5). Tasty Desk's own code uses neither.
//
// Packages whose Mac version has no WebAssembly build. Each must be checked
// by the parity test (ios/parity) before it ships.
const EXCEPTIONS = {
  // pydantic-core is compiled; the first WebAssembly build on PyPI is 2.47.
  // The Mac's pydantic 2.13.5 needs core 2.46.5 exactly, so the phone uses
  // the nearest pair that exists.
  pydantic: "2.14.1",
  "pydantic-core": "2.50.1",
};

function macVersion(name) {
  const block = uvLock.split("[[package]]").find((b) => b.includes(`\nname = "${name}"\n`));
  const match = block && block.match(/\nversion = "([^"]+)"/);
  if (!match) throw new Error(`${name} is not in uv.lock`);
  return match[1];
}

const pins = [
  ...SAME_AS_MAC.map((name) => `${name}==${macVersion(name)}`),
  ...Object.entries(EXCEPTIONS).map(([name, version]) => `${name}==${version}`),
];

const py = await loadPyodide();
await py.loadPackage("micropip");
const micropip = py.pyimport("micropip");
console.log("Installing:\n  " + pins.join("\n  "));
await micropip.install(pins);

// freeze() lists everything Pyodide could load; keep what was installed.
const norm = (name) => name.toLowerCase().replace(/[-_.]+/g, "-");
const installed = new Set(
  py.runPython("import micropip; [p.name for p in micropip.list().values()]").toJs().map(norm),
);
// micropip itself is only needed to make this lock, not on the phone.
installed.delete("micropip");
const full = JSON.parse(micropip.freeze());
const lock = { info: full.info, packages: {} };
for (const [key, pkg] of Object.entries(full.packages)) {
  if (!installed.has(norm(pkg.name))) continue;
  // Pyodide's own builds are named by file alone (bundle.mjs fetches them
  // from Pyodide's release); PyPI files keep their full URL.
  if (!/^https:\/\//.test(pkg.file_name)) pkg.file_name = path.basename(pkg.file_name);
  lock.packages[key] = pkg;
}
const keys = new Set(Object.keys(lock.packages));
for (const pkg of Object.values(lock.packages)) {
  for (const dep of pkg.depends) {
    if (!keys.has(dep)) throw new Error(`${pkg.name} needs ${dep}, which is not in the lock`);
  }
}
writeFileSync(path.join(here, "pyodide-lock.json"), JSON.stringify(lock, null, 2) + "\n");

const names = Object.values(lock.packages)
  .map((p) => `${p.name} ${p.version}`)
  .sort();
console.log(`\nLocked ${names.length} packages:\n  ` + names.join("\n  "));
