# Publication checklist

1. Keep both private VM ZIPs out of Git and retain them separately. This release is the selected public candidate, not a complete image of the VM.
2. Review RIGHTS.md and choose the intended license for original contributions; retain upstream Apache-2.0 notice. Confirm that the historical implementation source is authorized for publication.
3. Review repository files and API-token references. No live credentials were found in the basic pattern scan; `fake_key_not_used_in_mock` is a test fixture. This is not an exhaustive secret audit.
4. From an existing local checkout of the old repository, copy the contents of this directory to the repository root (not the outer folder), keeping `.git/`. Replace stale public-note root files; original v0.4 remains under history/. Use a new review branch and inspect `git diff` before commit/push. Do not force-push or discard untracked work.
5. Run archive verification and native replay. GitHub Actions performs these offline/model-free checks. After review, merge and optionally tag `v1.0`.
