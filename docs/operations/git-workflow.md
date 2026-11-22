# Workspace and Git workflow

TDflowMCP follows the owner's cross-project [Workspace and Git working agreement](https://github.com/jlopesmo/nexOS/blob/main/docs/operations/workspace-and-git-working-agreement.md) (owner decision 2026-10-25). It is summarized here; the linked agreement wins on conflict.

- **Canonical checkout:** `F:\dev\TDflowMCP` on branch `dev`. No permanent sibling `*-dev`/`*-review` directories.
- **Branches:** `dev` (active development) and `main` (owner-approved release state). Promotion `dev → main` only with owner approval after required checks. No force pushes, history rewrites or automatic deployments.
- **Temporary worktrees/branches** are explicit exceptions with a recorded purpose, location, owner and removal condition. Prefer `.tmp/` (git-ignored).
- Before Git topology changes, inspect `git status --short --branch`, `git worktree list`, `git branch -vv`.
- **This repository is on a filesystem that does not record ownership.** Git reports "dubious ownership". Use `git -c safe.directory=F:/dev/TDflowMCP …` per command, or have the owner add the exception globally. Agents must not change global Git config without approval.

## Commits
- Each commit updates the affected ICM files ([AGENTS.md §3](../../AGENTS.md#3-icm-maintenance-is-part-of-every-change-mandatory)) and passes `python tools/check_docs.py`.
- Never commit secrets, tokens, audit DBs, user-profile paths or personal details, user media, or built `.tox` files. Documenting the canonical checkout path and the TouchDesigner install path is allowed.
