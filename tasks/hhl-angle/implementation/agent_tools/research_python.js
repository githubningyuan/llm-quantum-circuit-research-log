import { tool } from "@opencode-ai/plugin";
import { spawn } from "node:child_process";

// This file must be root-owned in a non-agent-writable directory.
// It only invokes the root-owned, constrained Python client; no shell or arbitrary executable.
const CLIENT = "/srv/hhl-task2/rootless/service_v44/agent_client.py";
const FILE_PART = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/;
const MAX_FILES = 64;
const MAX_OUTPUT = 256 * 1024;
const TIMEOUT_MS = 180_000;

function validatePath(value) {
  if (typeof value !== "string" || !value || value.length > 240 || value.includes("\\")) {
    throw new Error("invalid relative path");
  }
  const parts = value.split("/");
  if (parts.length > 8 || parts.some((p) => p === "." || p === ".." || !FILE_PART.test(p))) {
    throw new Error("invalid relative path component");
  }
  return value;
}

export default tool({
  description: "Run Agent-authored Python research code inside the restricted HHL Rootless Sandbox. " +
    "Entry and files are relative to the HHL Scratch mount. Never executes host shell or Docker/Podman commands. " +
    "The Broker must already be running; this tool cannot start it.",
  args: {
    entry: tool.schema.string().describe("Scratch-relative Python entry point, e.g. experiment.py"),
    files: tool.schema.array(tool.schema.string()).min(1).max(MAX_FILES).describe(
      "All Scratch-relative input files needed to run (including entry); may contain multiple Python/data files"),
  },
  async execute({ entry, files }) {
    validatePath(entry);
    if (!entry.endsWith(".py") || !Array.isArray(files) || files.length > MAX_FILES || !files.length) {
      throw new Error("invalid Python entry/files");
    }
    const paths = files.map(validatePath);
    if (!paths.includes(entry) || new Set(paths).size !== paths.length) {
      throw new Error("entry missing or duplicate input paths");
    }
    const argv = ["-I", "-B", CLIENT, "--entry", entry, ...paths.flatMap((p) => ["--file", p])];
    return await new Promise((resolve, reject) => {
      const child = spawn("/usr/bin/python3", argv, {
        cwd: "/",
        shell: false,
        stdio: ["ignore", "pipe", "pipe"],
        env: { PATH: "/usr/bin:/bin", LANG: "C", PYTHONDONTWRITEBYTECODE: "1" },
      });
      let stdout = "";
      let stderr = "";
      let failed = false;
      const timeout = setTimeout(() => { failed = true; child.kill("SIGKILL"); }, TIMEOUT_MS);
      const collect = (which) => (chunk) => {
        if (which === "stdout") stdout += chunk.toString("utf8");
        else stderr += chunk.toString("utf8");
        if (stdout.length + stderr.length > MAX_OUTPUT) { failed = true; child.kill("SIGKILL"); }
      };
      child.stdout.on("data", collect("stdout"));
      child.stderr.on("data", collect("stderr"));
      child.on("error", (err) => { clearTimeout(timeout); reject(err); });
      child.on("close", (code) => {
        clearTimeout(timeout);
        if (failed) return reject(new Error("research_python output/time budget exceeded"));
        if (code !== 0) return reject(new Error(`research_python client failed (${code}): ${stderr.slice(0, 2000)}`));
        // The client emits a small JSON response with saved artifact paths, not raw artifact bytes.
        let result;
        try { result = JSON.parse(stdout); } catch { return reject(new Error("malformed broker response")); }
        resolve(JSON.stringify(result));
      });
    });
  },
});
