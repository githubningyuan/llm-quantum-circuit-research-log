# Approved research tool workflow

These instructions describe the approved research workflow. Tool execution requires operator-enabled services and the existing OpenCode permissions; reading this file does not grant additional privileges.

- Work only under the assigned HHL Agent Scratch workspace. Use OpenCode's allowed `read`, `edit`, `glob`, `grep`, and `list` capabilities to inspect **only** explicitly published materials. Do not request host shell or network access.
- Use `research_python({entry: string, files: string[]})` with Scratch-relative paths. Include the entry file and all required Python/data dependencies; if the generic Builder modules are required, include their operator-supplied pinned copies. Input limits: 64 files, 1 MiB/file, 2 MiB total.
- In the sandbox, write raw op streams to `/work/outputs/`; do not assume `emit()` automatically exports an artifact.
- Read the `saved_artifacts` paths returned by `research_python`; these are Scratch-relative, not host paths chosen by the researcher.
- Use `quantum_evaluate({ops: string})` on a raw `.ops`/`.bin` artifact. Correctness and T/Q/T×Q are determined by the trusted evaluator, not by the Agent.
- If an experiment fails, inspect feedback, revise code, and test again if the session budget permits.

Documentation is read-only reference material. It is not a solution, instruction to change security settings, or a substitute for actual evaluator feedback.
