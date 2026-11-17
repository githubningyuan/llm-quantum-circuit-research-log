// REVIEW STAGE ONLY: root-owned OpenCode tool; do not enable before acceptance.
import { tool } from "@opencode-ai/plugin";
import { spawn } from "node:child_process";

const CLIENT = "/run/hhl-task2/p1-review-code/clients/p5_eval_client.py";
const PART = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/;
export default tool({
  description: "Submit an existing RAW HHL op stream from HHL Scratch to the independent evaluator. " +
    "No host Python execution of submitted programs, shell, sudo or Podman access.",
  args: {
    ops: tool.schema.string().describe("Scratch-relative raw .ops/.bin path"),
    // Optional P1 binding: both fields MUST travel to the evaluator service.
    // They are untrusted claims until root audit of both independently owned spools.
    broker_run_id: tool.schema.string().describe("Broker receipt id (32 hex chars)"),
    broker_artifact: tool.schema.string().describe("Broker output artifact name, e.g. candidate.ops"),
  },
  async execute({ ops, broker_run_id, broker_artifact }) {
    if (typeof ops !== "string" || !ops || ops.length > 240 || ops.includes("\\") ||
        ops.split("/").length > 8 || ops.split("/").some(p => !PART.test(p) || p.startsWith(".")) ||
        !(ops.endsWith(".ops") || ops.endsWith(".bin"))) {
      throw new Error("invalid raw HHL stream path");
    }
    const isBound = true; // P5 refuses unbound evaluations
    if (isBound && (typeof broker_run_id !== "string" || !/^[a-f0-9]{32}$/.test(broker_run_id) ||
        typeof broker_artifact !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,74}\.ops$/.test(broker_artifact))) {
      throw new Error("invalid Broker provenance claim");
    }
    const argv = ["-I", "-B", CLIENT, "--ops", ops];
    if (isBound) argv.push("--broker-run-id", broker_run_id, "--broker-artifact", broker_artifact);
    return await new Promise((resolve, reject) => {
      const child = spawn("/usr/bin/python3", argv, {
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
