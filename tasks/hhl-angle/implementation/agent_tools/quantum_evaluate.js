// REVIEW STAGE ONLY: root-owned OpenCode tool; do not enable before acceptance.
import { tool } from "@opencode-ai/plugin";
import { spawn } from "node:child_process";

const CLIENT = "/srv/hhl-task2/bridge-first-session/agent_client.py";
const PART = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/;
export default tool({
  description: "Submit an existing RAW HHL op stream from HHL Scratch to the independent evaluator. " +
    "No host Python execution of submitted programs, shell, sudo or Podman access.",
  args: { ops: tool.schema.string().describe("Scratch-relative raw .ops/.bin path") },
  async execute({ ops }) {
    if (typeof ops !== "string" || !ops || ops.length > 240 || ops.includes("\\") ||
        ops.split("/").length > 8 || ops.split("/").some(p => !PART.test(p) || p.startsWith(".")) ||
        !(ops.endsWith(".ops") || ops.endsWith(".bin"))) {
      throw new Error("invalid raw HHL stream path");
    }
    return await new Promise((resolve, reject) => {
      const child = spawn("/usr/bin/python3", ["-I", "-B", CLIENT, "--ops", ops], {
        cwd: "/", shell: false, stdio: ["ignore", "pipe", "pipe"],
        env: { PATH: "/usr/bin:/bin", LANG: "C", PYTHONDONTWRITEBYTECODE: "1" },
      });
      let stdout = "", stderr = "", failed = false;
      const timeout = setTimeout(() => { failed = true; child.kill("SIGKILL"); }, 115000);
      const collect = (key) => chunk => {
        if (key === "stdout") stdout += chunk.toString("utf8");
        else stderr += chunk.toString("utf8");
        if (stdout.length + stderr.length > 16384) { failed = true; child.kill("SIGKILL"); }
      };
      child.stdout.on("data", collect("stdout"));
      child.stderr.on("data", collect("stderr"));
      child.on("error", err => { clearTimeout(timeout); reject(err); });
      child.on("close", code => {
        clearTimeout(timeout);
        if (failed || code !== 0) return reject(new Error("HHL evaluator client refused"));
        try {
          const obj = JSON.parse(stdout);
          if (typeof obj.valid !== "boolean") throw new Error("bad reply");
          resolve(JSON.stringify(obj));
        } catch { reject(new Error("malformed HHL evaluator response")); }
      });
    });
  },
});
