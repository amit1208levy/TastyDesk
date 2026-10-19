// Puts everything the phone engine needs into web/public/engine, so the app
// carries it and downloads nothing when it runs:
//
//   * Pyodide itself (the WebAssembly Python), from node_modules/pyodide;
//   * every package in engine/pyodide-lock.json, each checked against the
//     SHA-256 in the lock (a mismatch stops the build);
//   * tastydesk.tar: the Mac app's Python (src/tastydesk) and the phone glue
//     (engine/py), with the commit it was built from.
//
// Usage: npm run bundle   (from ios/; then build the web app as usual)
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");
const pyodideDir = path.join(here, "../node_modules/pyodide");
const out = path.join(repo, "web/public/engine");

const lock = JSON.parse(readFileSync(path.join(here, "pyodide-lock.json"), "utf8"));
const pyodideVersion = JSON.parse(readFileSync(path.join(pyodideDir, "package.json"), "utf8")).version;
const pyodideRelease = `https://cdn.jsdelivr.net/pyodide/v${pyodideVersion}/full/`;

const sha256 = (data) => createHash("sha256").update(data).digest("hex");

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

// 1. Pyodide itself.
for (const file of ["pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip"]) {
  copyFileSync(path.join(pyodideDir, file), path.join(out, file));
}

// 2. The locked packages.
const phoneLock = { info: lock.info, packages: {} };
for (const [key, pkg] of Object.entries(lock.packages)) {
  const fromPyPI = pkg.file_name.startsWith("https://");
  const name = fromPyPI ? decodeURIComponent(new URL(pkg.file_name).pathname.split("/").pop()) : pkg.file_name;
  const cached = path.join(pyodideDir, name);
  let data;
  if (!fromPyPI && existsSync(cached)) {
    data = readFileSync(cached);
  } else {
    const url = fromPyPI ? pkg.file_name : pyodideRelease + name;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
    data = Buffer.from(await res.arrayBuffer());
  }
  const actual = sha256(data);
  if (actual !== pkg.sha256) {
    throw new Error(`${name}: SHA-256 ${actual} does not match the lock (${pkg.sha256}). Refusing to bundle it.`);
  }
  writeFileSync(path.join(out, name), data);
  phoneLock.packages[key] = { ...pkg, file_name: name };
}
writeFileSync(path.join(out, "pyodide-lock.json"), JSON.stringify(phoneLock));

// 3. The Python sources, as a plain tar (Pyodide unpacks it on start).
// tastydesk.mcp is the Mac's Claude connector; it needs the mcp package,
// which the phone does not carry, and nothing on the phone imports it.
const SKIP = new Set(["__pycache__", "mcp"]);
const files = [];
function collect(dir, prefix) {
  for (const entry of readdirSync(dir).sort()) {
    if (SKIP.has(entry) || entry.startsWith(".")) continue;
    const full = path.join(dir, entry);
    const name = prefix ? `${prefix}/${entry}` : entry;
    if (statSync(full).isDirectory()) collect(full, name);
    else if (entry.endsWith(".py")) files.push([name, readFileSync(full)]);
  }
}
collect(path.join(repo, "src/tastydesk"), "tastydesk");
collect(path.join(here, "py"), "");

let commit = "unknown";
try {
  commit = execFileSync("git", ["rev-parse", "--short", "HEAD"], { cwd: repo }).toString().trim();
  const dirty = execFileSync("git", ["status", "--porcelain", "src", "ios/engine"], { cwd: repo }).toString().trim();
  if (dirty) commit += "+changes";
} catch {
  // Not a git checkout; the build still works.
}
files.push(["tastydesk_build.py", Buffer.from(`COMMIT = ${JSON.stringify(commit)}\n`)]);
writeFileSync(path.join(out, "tastydesk.tar"), tar(files));

const size = readdirSync(out).reduce((sum, f) => sum + statSync(path.join(out, f)).size, 0);
console.log(
  `Bundled Pyodide ${pyodideVersion}, ${Object.keys(phoneLock.packages).length} packages and ` +
    `${files.length} Python files (commit ${commit}): ${(size / 1e6).toFixed(1)} MB in web/public/engine`,
);

/** A minimal ustar archive: regular files only, fixed timestamps. */
function tar(entries) {
  const blocks = [];
  for (const [name, data] of entries) {
    if (Buffer.byteLength(name) > 100) throw new Error(`Path too long for tar: ${name}`);
    const header = Buffer.alloc(512);
    header.write(name, 0);
    header.write("0000644\0", 100); // mode
    header.write("0000000\0", 108); // uid
    header.write("0000000\0", 116); // gid
    header.write(data.length.toString(8).padStart(11, "0") + "\0", 124);
    header.write("00000000000\0", 136); // mtime: fixed, so builds are repeatable
    header.write("        ", 148); // checksum placeholder
    header.write("0", 156); // regular file
    header.write("ustar\0", 257);
    header.write("00", 263);
    let sum = 0;
    for (const byte of header) sum += byte;
    header.write(sum.toString(8).padStart(6, "0") + "\0 ", 148);
    blocks.push(header, data, Buffer.alloc((512 - (data.length % 512)) % 512));
  }
  blocks.push(Buffer.alloc(1024));
  return Buffer.concat(blocks);
}
