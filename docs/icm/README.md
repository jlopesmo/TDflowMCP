# TDflowMCP — ICM index

ICM (the owner's project-context convention, shared with nexOS, Morphora and FlowShow) is the compact, durable context layer for this repository. It lets humans and agents recover current truth without replaying chat history or re-auditing the repo.

## Read order

1. [context.md](context.md): purpose, terminology, system boundaries.
2. [invariants.md](invariants.md): rules implementation must not violate (accepted vs proposed are labelled).
3. [current-state.md](current-state.md): what **verifiably** exists now, known gaps, next action.
4. [project-map.md](project-map.md): what is in each directory and which document governs it.
5. The relevant [decision records](../decisions/README.md), [architecture](../architecture/README.md) or [milestone](../milestones/README.md).
6. [roadmap.md](roadmap.md) when planning or sequencing.
7. [validation-index.md](validation-index.md) before claiming anything is tested.
8. [RepoInspire register](../../repoinspire.md) before evaluating external repos/tools.

## Sources of truth

| Question | Authority |
|---|---|
| What are we building and why? | [Product vision](../product/vision.md) + ADR-0001 |
| What must never be violated? | [invariants.md](invariants.md) |
| Why was option X chosen? | [docs/decisions/](../decisions/README.md) (ADR status governs) |
| How should it work? | [docs/architecture/](../architecture/README.md) (**proposal** unless an accepted ADR backs it) |
| What exists and works today? | [current-state.md](current-state.md) only, with evidence |
| What was tested and how? | [validation-index.md](validation-index.md) |
| What's next and when is it done? | [roadmap.md](roadmap.md) + [milestones](../milestones/README.md) |
| What did we learn from other projects? | [docs/research/](../research/2026-11-22-landscape-and-comparison.md) + [repoinspire.md](../../repoinspire.md) |
| Where does source live vs built artifacts? | [source-of-truth.md](../architecture/source-of-truth.md) |
| Where does a *host project's* memory live? | [continuity-and-memory.md](../architecture/continuity-and-memory.md) (not this folder: this folder is TDflowMCP's own development memory) |

ADRs remain authoritative for the decisions they record. ICM files summarize and link; they don't duplicate rationale.

## Statement status vocabulary

**Accepted** (owner-approved; *Conditionally Accepted* = subject to the listed validation gates) · **Proposed** (awaiting approval) · **Hypothesis** (untested) · **Verified** (evidenced by an executed and observed check). See [AGENTS.md §2](../../AGENTS.md#2-status-discipline).

## Maintenance rules

- Update `current-state.md` only from implementation/validation evidence.
- Update `validation-index.md` whenever checks are added, removed or run; unrun checks are marked **not executed**.
- Update `project-map.md` in the same commit as any directory/module change.
- Update `roadmap.md` when sequencing is approved or revised.
- Change `invariants.md` and `context.md` only together with an ADR.
- Keep ICM compact. Runtime history, audit events and logs belong in their runtime stores, never in Git.
- `python tools/check_docs.py` enforces links, required files and ADR status headers.
