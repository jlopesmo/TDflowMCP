# Spike S4: undo semantics of Python-driven edits (TouchDesigner 2025.32440)

**Date:** 2026-11-22 · **Status:** Executed (2 runs; the second added DAT-text capture). Disposable, text-generated project only. TouchDesigner exited with code 0; the TouchDesigner processes that were already running were untouched.
**Code:** [spikes/s4/](../../spikes/s4/) (`run_s4.py` outside TD, `s4_runtime.py` inside). Raw results stay git-ignored in `.tmp/s4/s4_results.json`.

**Method:** each case runs in a fresh sandbox COMP, after `ui.undo.clear()`, with one step per frame and two settle frames between steps:
1. prepare;
2. edit, inside `ui.undo.startBlock()`/`endBlock()` or, for the `noblock` cases, without a block;
3. `ui.undo.undo()`;
4. `ui.undo.redo()`;
5. `ui.undo.undo()` again.

After each step the runner records the sandbox's children, wiring, parameter value, mode and expression, DAT text, names and custom parameters.

**`ui.undo` API on 2025.32440:** `addCallback`, `clear`, `endBlock`, `globalState` (initially `True`), `redo`, `redoStack`, `startBlock`, `state`, `undo`, `undoStack`.

## Results

| Edit (Python) | In an undo block | Without a block |
|---|---|---|
| Create operator (`create`) | **undo + redo work** | **not recorded**: undo does nothing |
| Destroy operator (`destroy`) | **undo restores it; redo deletes it again** | not tested |
| Parameter value (`par.x = v`) | **undo + redo work** | **not recorded** |
| Parameter expression (`par.expr = …`, mode → EXPRESSION) | **undo restores the constant mode; redo restores the expression** | not tested |
| Wire (`outputConnectors[0].connect`) | **undo + redo work** | not tested |
| Unwire (`inputConnectors[0].disconnect`) | **undo + redo work** | not tested |
| Rename (`op.name = …`) | **undo + redo work** | not tested |
| Append custom parameter page/parameter | **undo + redo work** | not tested |
| **DAT text (`dat.text = …`)** | **NOT undoable**: the text stays "edited" after undo | not undoable |
| Several edits in one block (parameter + DAT text + create) | One undo reverts the parameter and the create together; **the DAT text stays changed** | — |

## Guarantees for Phase 1B (what `tdflow_undo` may rely on)

1. **Every mutation must run inside its own `ui.undo` block.** Python edits outside a block are not recorded, so they cannot be undone through TouchDesigner. One tool call is one block.
2. **TouchDesigner's undo is reliable** for operator create and destroy, parameter values and expressions, wiring and unwiring, renames and custom-parameter changes. Undo and redo both restore the recorded state.
3. **DAT text is not covered.** Phase 1B must snapshot a DAT's text before editing it and restore it itself (a TDflowMCP-managed inverse), or refuse to offer undo for DAT-text edits. The same applies to any other edit class not listed above until it is tested.
4. **Shared stack.** The undo stack is TouchDesigner's own and shared with the user. `tdflow_undo` may only undo its block while that block is on top of the stack. Otherwise it returns `UNDO_CONFLICT` (security model §9), and `ui.undo.undoStack` is the evidence source.
5. **Interleaving and save/reload:** answered by S4b below.
6. **Still untested:**
   - very large blocks;
   - `ui.undo` behaviour with `globalState` off.
   - Edits inside `/local` and other system COMPs are hard-denied, so they are not needed.

The mutation tools that rely on these guarantees were implemented in Phase 1B (`src/td_adapter/tdops.py`).

## S4b: undo stack, interleaving, identity and save/reload (executed 2026-11-22)

**Code:** `spikes/s4/run_s4b.py` and `s4b_runtime.py`. Two TouchDesigner launches, both disposable: the second opens the file the first one saved, under `.tmp/s4b/`. Both exited with code 0, and the processes that were already running were untouched.

| Question | Observed on 2025.32440 |
|---|---|
| What `ui.undo.undoStack` exposes | A list of entry names, **newest first**. Our block names are visible (`tdflow:101`). `redoStack` likewise |
| Human edit after an agent change | Both blocks are on the stack (`["user edit", "tdflow:101"]`). A plain `undo()` reverts the **human** edit first. An agent's undo must therefore check what is on top |
| Delete inside a named block | The entry is named **`Delete Node`**, not the block name. Names alone cannot identify an agent's delete |
| Identity after undoing a delete | The operator comes back at the same path with a **new `op.id`**. Verification must use the path and state |
| Save | Saving the project keeps the undo stack; undo after save works |
| Reload | After reopening the saved file the undo stack is **empty**; nothing recorded before can be undone |

**Design consequence (implemented in `src/td_adapter/tdops.py`):**
- Right after each change, TDflowMCP records the complete undo-stack name list.
- `tdflow_undo` runs only if the current stack is **identical** to that recording. Otherwise it returns `UNDO_CONFLICT`. That covers a human edit, another agent's change, a reload or TouchDesigner restart, and the relabelled `Delete Node` case.
- After undoing, TDflowMCP **verifies** the result by path and state: the created operator is gone; the deleted operator is back with its type; parameters are back to their previous mode and value; the wiring is back.
- DAT text uses a durable TDflowMCP snapshot, restored only if the DAT's text is unchanged since the agent's edit.
