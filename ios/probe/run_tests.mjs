// Runs the Mac app's Python test suite inside Pyodide, the Python engine the
// iPhone app would use. Usage: npm install && npm run tests [-- pytest args]
import { loadPyodide } from "pyodide";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");
const py = await loadPyodide({ stdout: (s) => console.log(s), stderr: (s) => console.log(s) });
py.FS.mkdirTree("/repo");
py.FS.mkdirTree("/shim");
py.FS.mkdirTree("/engine");
py.FS.mount(py.FS.filesystems.NODEFS, { root: repo }, "/repo");
py.FS.mount(py.FS.filesystems.NODEFS, { root: path.join(here, "shim") }, "/shim");
py.FS.mount(py.FS.filesystems.NODEFS, { root: path.join(repo, "ios/engine/py") }, "/engine");
await py.loadPackage("micropip");
await py.pyimport("micropip").install(
  ["pydantic", "tastytrade", "pydantic-settings", "pytest", "pytest-asyncio", "fastapi"],
);
py.globals.set("pytest_args", py.toPy(process.argv.slice(2)));
py.runPython(`
import asyncio, os, sys, time
import asyncio.runners
import pyodide.webloop

# The phone engine's stand-ins for aiosqlite and keyring, as the app uses them.
sys.path[:0] = ["/shim", "/engine", "/repo/src"]

# Pyodide's own event loop can't be driven synchronously, which pytest-asyncio
# needs; give the tests a plain loop (they use fakes, not the network).
import simpleloop
asyncio.run = asyncio.runners.run
time.sleep = pyodide.webloop._orig_sleep
asyncio.set_event_loop_policy(simpleloop.SimplePolicy())
asyncio.events._set_running_loop(None)

os.chdir("/repo")
import pytest
rc = pytest.main(["-q", "-rfEs", "-p", "no:cacheprovider", "-m", "not slow",
                  "-W", "ignore::DeprecationWarning", *pytest_args])
print("pytest exit code", int(rc))
`);
