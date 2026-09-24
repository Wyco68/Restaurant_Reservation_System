// Entry point for the root npm scripts: finds a Python the pinned requirements install on
// (3.11-3.13) and hands off to scripts/dev.py, which does the real work.
// Falls back to uv-managed 3.12 when the system Python is outside that range.

import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const SUPPORTED = ["3.11", "3.12", "3.13"];
const isWindows = process.platform === "win32";

function pythonVersion(cmd, args) {
  const probe = "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')";
  const r = spawnSync(cmd, [...args, "-c", probe], { encoding: "utf8" });
  return r.status === 0 ? r.stdout.trim() : null;
}

function findUv() {
  if (spawnSync("uv", ["--version"]).status === 0) return "uv";
  // The uv installer puts it here, which may not be on PATH in this shell yet.
  const local = join(homedir(), ".local", "bin", isWindows ? "uv.exe" : "uv");
  return existsSync(local) ? local : null;
}

function pickPython() {
  const candidates = isWindows
    ? [["py", ["-3.13"]], ["py", ["-3.12"]], ["py", ["-3.11"]], ["python", []]]
    : [["python3", []], ["python", []], ["python3.13", []], ["python3.12", []], ["python3.11", []]];
  for (const [cmd, args] of candidates) {
    if (SUPPORTED.includes(pythonVersion(cmd, args))) return [cmd, args];
  }
  const uv = findUv();
  if (uv) return [uv, ["run", "--python", "3.12", "--no-project", "python"]];
  return null;
}

const python = pickPython();
if (!python) {
  console.error(
    `No Python ${SUPPORTED.join("/")} found and uv is not installed. Install uv:\n` +
      "  https://docs.astral.sh/uv/getting-started/installation/",
  );
  process.exit(1);
}

const [cmd, args] = python;
const child = spawn(cmd, [...args, "scripts/dev.py", ...process.argv.slice(2)], {
  stdio: "inherit",
});
// Ctrl+C reaches the child directly; dev.py shuts the servers down itself.
process.on("SIGINT", () => {});
child.on("exit", (code) => process.exit(code ?? 1));
