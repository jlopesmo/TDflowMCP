# Phase 3 increment 2: experiments and approval-only project save (2026-11-23)

**Owner decision D20:** complete Phase 3, including safe experimentation ([roadmap D20](../icm/roadmap.md#owner-decisions-2026-11-22)). Tools: [tool surface](../architecture/tool-surface.md).

## Delivered
- **`tdflow_experiment_create {path, name}`**: copies a COMP next to itself to try changes on, leaving the original untouched. The copy is tagged as an experiment by TDflowMCP and remembers its origin; agents cannot set tags or storage. It is classified like:
  - reading every operator in the source, so read denies and protection apply inside it;
  - creating every operator in the copy, so the creation allowlist applies;
  - `execute` for anything that runs code once copied: executable types, parameters in expression, bind or export mode, and parameters that name operators, DATs or files. Parameters still at TouchDesigner's default are not counted;
  - a G3 denial when a denylisted parameter is set (external `.tox`, clone, extension) in any mode;
  - a refusal for TDflowMCP and project-memory components inside the source.

  Anything that cannot be inspected fails closed. The approval binds a hash of the code the copy carries, so code changed after the approval is refused (`APPROVAL_TARGETS_CHANGED`). Copied executable operators are switched off, as with `tdflow_create_operator`. The copy is undoable. Copies are limited by the 50-operation limit per call.
- **`tdflow_experiment_promote {path}`**: replaces the original with the experiment in one TouchDesigner undo block. The experiment takes the original's name, position and operator wiring, so references by path keep working.
  - It counts as deleting the original (A2 above 50 operators).
  - Every rewired downstream operator counts as a wiring change, as with `tdflow_connect`.
  - It is undoable. TouchDesigner's undo does not restore tags, so the undo marks the restored copy as an experiment again, after checking its type.
- **`tdflow_experiment_discard {path}`**: deletes an experiment and nothing else. Undoable.
- **`tdflow_save_project`**: `execute` on the project root, so it needs a grant on the whole project, and it always needs approval. It saves like TouchDesigner's own save, which may add a version number to the file name, and reports the name TouchDesigner gives. It is not reversible, and every approval request says so.

## Independent review 16
| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | Medium | A denylisted parameter set as an expression made the copy approvable (A1) instead of G3-denied | The denylist is checked in every mode |
| 2 | Medium | No read or protection check inside the source: a read-denied subtree could be copied into readable space, and a `tdflowProject` copy blocked project memory | Read target per source operator; reserved components refuse the copy |
| 3 | Medium | Copied Execute DATs kept running; the approval did not bind the code copied | Copies switched off; approval binds a hash of the copied code |
| 4 | Low | Reference-style parameters not flagged; inspection errors skipped (fail open) | Flagged as execute; errors refuse the copy |
| 5 | Low | Promote rewired downstream operators without policy checks | Wiring targets per downstream operator (update, plus execute for DATs and executable types) |
| 6 | Low | Failures detected after the change committed | Checks moved inside the undo block |
| 7 | Low | Promote's undo verification proved nothing (it set the tag it checked) | The experiment's type is recorded and checked |
| Notes | | Save binds no project content; the copy size limit was undocumented | Recorded below; tool description states the limit |

## Evidence
See the [validation index](../icm/validation-index.md):
- unit tests `test_experiments.py`;
- in-TD checks `experiment.*` (copy, original untouched, discard only experiments, promote, promote undone, discard, builders cannot copy code, code in a copy needs approval) and `save.*` (scoped agent denied, approval, saved, grant removed again).

## Limits
- **Clones and references to the original:** components elsewhere that clone the original, or that reference it by path, follow the promoted experiment. That is the intent of a promote, but it is not policy-checked per referencing operator.
- **Wiring:** COMP-to-COMP connectors are not restored on promote; `connections_lost` counts only failed operator wires.
- **Grant parameter denylists** (`Grant.param_denylist`) do not apply to copies. They cannot be set in this version: grants are edited by scope, actions and effect only.
- **What the user approves:** the user sees the source path and the reasons, but not the copied code as text. The approval binds that code's hash, and the user can inspect the source in TouchDesigner.
- **Save content:** the user approves saving, not a particular project state. Changes made in between are saved too.
- **Not built yet:** file snapshots and restore. An experiment is the in-project way to keep the original while trying changes.
- **Skill:** still 1.4. The new tools describe themselves; the Skill will describe them in the next increment.
