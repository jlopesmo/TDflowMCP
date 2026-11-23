# ADR-0014 — Candidate Development Mode (A/B self-development)

**Status:** Proposed (2026-11-22). The owner described the A/B workflow on 2026-11-22 and asked for it to be recorded as a proposed architectural feature, with the smallest safe proof of concept identified. It is not to be implemented yet.

## Context
The owner wants TDflowMCP used for meaningful development of TDflowMCP itself, not only synthetic tests:
- A protected active instance A serves a coding agent, which inspects, develops and tests an inactive candidate B.
- B is validated, synchronized with the repository source and exported as a versioned `.tox`.
- After explicit owner approval, B replaces A, with rollback available.

Existing constraints:
- the H3 hard deny on active instances;
- no unauthorized self-modification;
- source-first builds (ADR-0007);
- the audit trail (ADR-0006);
- approval gates.

## Decision (proposed)
Adopt [self-development.md](../architecture/self-development.md):
1. B never executes inside A's TouchDesigner process. Any in-project mirror of B (stage 2) is an allowlisted, inert, protected workspace (tag `TDflowMCPCandidate`, raw tools hard-denied), with no references from outside it. A enforces this; the TD facts are hypotheses for spike S8.
2. B runs only in separate disposable TouchDesigner processes, built source-first and tested with the existing in-TD suites; results are bound to the `.tox` SHA-256.
3. Candidate access is a separate grant kind (`candidate:read`, later `candidate:update`), never implied by builder/observer roles. **H3 is unchanged, and execute classification is never relaxed.**
4. The repository stays authoritative. Workspace edits leave only as a previewed patch; there is no automatic two-way sync.
5. Promotion and rollback are owner-approved handoffs that keep `Projectid`, storage and the append-only audit log, and record old and new hashes. Agents cannot trigger them.
6. **Smallest safe PoC (revised after review 8):** no in-project workspace. Isolated build/test of B bound to the `.tox` hash, plus a manual, owner-performed promotion and rollback that the new A verifies and records (previous hash → new hash) with audit continuity.

## Alternatives
- **B as a live but "disabled" component next to A:** rejected. Its extension can initialise on load, so its code would run in A's process.
- **Letting A update itself in place:** rejected. That is uncontrolled self-modification.
- **Editing B only inside TD with two-way sync to the repository:** rejected. It creates two authorities (compare ADR-0013).

## Validation gates (when accepted)
- **S8 spike (before stage 2):** on 2025.32440, verify extension/callback behaviour, that inert DATs cannot execute or be imported, name shadowing, and reverse schema compatibility for rollback.
- **PoC:**
  - H3 tests are unchanged and passing;
  - a candidate package passes all in-TD suites;
  - promotion and rollback each keep the audit chain verifying, and are recorded with hashes;
  - an agent cannot reach A's subtree or trigger promotion.
- **Stage 2:** the protected workspace is proven inert, and no outside operator references it.
