# HHL angle: independent circuit evolution

See [task specification](docs/HHL_TASK_SPEC.md) and native `contract.json` / `grader.py`.

- Best: T=2,839, Q=168, TQ=476,952 (A, Gen4, step 17), 10.36× vs baseline.
- A: 23 new candidates; B: 14 new candidates; both reach Gen4. Two historical seeds are separate, not included in the new count.
- `data/candidates.csv` is the full normalized archive; `data/sessions.json` describes sessions; `data/reproduced-native-report.json` is fresh native replay.
- `candidates/` contains event-time code and original OPS for each of the 37 new accepted candidates; `source.json` links all source bytes to original bundle identity.
- `implementation/deployment_sources/` contains historical P11 and dependencies, not a standalone deployable stack.

Run from repo root: `python tools/reproduce.py hhl-angle`.
