# TODOS

## CLI Module Split
**What:** Split `cli.py` (2,309 lines, 10 command functions) into per-command modules under `cli/`.
**Why:** Each new feature (digest, --program, --compare) adds ~100-200 lines. At ~3,000 lines, merge conflicts and slow imports become friction.
**Pros:** Cleaner separation of concerns, easier to review PRs that touch one command.
**Cons:** Refactor touches every import site. Not blocking anything today.
**Context:** Flagged during /plan-eng-review (2026-05-03). The monolith works fine at current scale. Address when cli.py hits a pain threshold (merge conflicts, slow IDE, multi-person contributions).
**Depends on:** Nothing. Can be done anytime.

## CI/CD Pipeline for PyPI Publishing
**What:** Set up GitHub Actions workflow for automated PyPI publishing on tagged releases.
**Why:** Manual publishing risks version bumps forgotten, publishing from wrong branch, or stale packages on PyPI while the repo has moved on. Multiple blog posts will drive installs.
**Pros:** One `git tag v0.3.0 && git push --tags` deploys everywhere. Consistent, auditable.
**Cons:** ~30 min setup. Requires PyPI API token in GitHub secrets.
**Context:** Flagged during /plan-eng-review (2026-05-03). Low priority until project gets traction (post-launch).
**Depends on:** Nothing. Can be done anytime.
