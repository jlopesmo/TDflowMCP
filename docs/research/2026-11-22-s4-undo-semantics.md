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
5. **Not yet tested; open for Phase 1B:**
   - undo after save/reload, which TouchDesigner does not persist, so it is assumed lost;
   - interleaving with user edits;
   - very large blocks;
   - edits inside `/local` or other special COMPs;
   - `ui.undo` behaviour with `globalState` off.

No mutation tools were implemented (Phase 1B scope).
