# Capabilities page, parameter order, sign-in codes, feature freeze (owner D25, 1.0.0rc6)

**Owner direction:** [D25](../icm/roadmap.md) (2026-11-25). **Baseline:** `1.0.0rc5` (`b2580e8`). **Decision:** [ADR-0016](../decisions/0016-capabilities-page-and-permission-editing.md).

## 1. Capabilities page
**Code:**
- `src/tdflowmcp/capabilities.py`: the catalog, the five statuses, change planning, preview and the escalation rules. Pure Python, unit-tested.
- `src/td_adapter/extension.py`: `GET /api/capabilities`, `POST /api/permissions/preview|apply`.
- `src/tdflowmcp/store.py` (`permissions_apply`) and `src/tdflowmcp/identity.py` (`apply_permissions`).
- The dashboard's **Capabilities** tab (`src/dashboard/*`).

**The matrix.** Rows are grouped:
- Inspection;
- Editing;
- Operator creation;
- Deleting;
- Python and code;
- Experiments;
- Undo;
- Project saving;
- Project memory;
- Multi-agent coordination.

The columns are:
- **Supported:** what TDflowMCP supports.
- **Read Only / Builder / Operator:** the most each role can be given.
- **This agent now:** the selected agent's effective status, with its scopes, or the reason it is denied (role, no scope, Code Mode off, a dedicated permission, the agent is disabled).

Below the matrix, **Always protected** lists the hard denies H1–H7, the parameter deny list, the creation allowlist and self-administration. **Not implemented** lists raw Python outside Code Mode, Save As, and remote access.

The legend defines each status, and a banner says when MCP is off or agents are suspended. Selecting a row (pointer, or Enter / arrow keys) names its tools, what it needs and how it could be allowed.

**Editing** reuses the one permission backend: the same store, identity, audit and approval cancellation as the Agents tab and TouchDesigner's native Agents page.
1. The owner sets the role, adds or removes scopes (actions, allow or deny), and the repository-memory permission.
2. **Preview** shows:
   - every capability whose status changes (also drawn into the matrix as "before → after");
   - the scopes after the change, with added or dropped actions;
   - notes (a grant removed by a downgrade, a renamed agent);
   - whether the change gives more access.
3. **Apply** sends the previewed change and its digest. More access needs the "I confirm" box.
4. Protected and not-implemented capabilities have no control. Code Mode stays a project setting.

**Agent self-report.** `tdflow_capabilities` (read-only, always listed) returns the caller's own capabilities:
- `your_capabilities`: available, and approval required;
- `not_available_to_you`: denied, not implemented and protected, each with its reason;
- a statement that these are its own effective permissions, not TDflowMCP's feature set;
- `dashboard`: how the user opens the page (Open Dashboard in TouchDesigner), with the address for reference.

`tdflow_whoami` names the tool and the address. The Skill (1.6, §1a), the full instructions and the Copy Agent Prompt tell agents to use it when asked what they can do.

## 2. Main parameter order
On the TDflowMCP page, the first actionable controls are **Run Diagnostics**, **Enable MCP** and **Suspend Agents** (emergency), then Status and Next Step. The connection controls (Coding Agent, Agent Access, Allowed Scope, Connect Agent) follow.

Enable MCP with no agent is refused as before (the preconditions are unchanged). Next Step now says `No agent: Connect Agent`, which fits the parameter window. Other failed preconditions say `Not ready: see Diagnostics`; both refusals are audited.

## 3. Browser sign-in codes
TouchDesigner's own pop-up dialog is a fixed 260 px. Three 7-character codes plus Deny did not fit as buttons: the visual review of rc5 showed the codes clipped and **Deny pushed out of view**. The codes are now numbered lines of the dialog text (`Button 1:  ABC-DEF`), and the buttons are `1`, `2`, `3` and `Deny`.

Number matching is unchanged: the same three choices (the real code and two random decoys), a wrong pick denies, and Deny, Esc and Enter reject. The web pages tell the user to press the button listed next to the code, and the code never wraps or clips (`white-space: nowrap`, responsive size).

Found on the way: author `display` rules overrode the `hidden` attribute on the dashboard. The "TouchDesigner is not running frames" warning and both Suspend and Resume always showed. Fixed with `[hidden] { display: none !important; }` on both pages.

## Independent review 22 (security)
An independent agent reviewed the changes against the security model, ADR-0009 and the policy code, and ran its scenarios against the code. It found **0 High, 2 Medium and 4 Low**.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | The matrix counted a scope only when one grant held all needed actions, unlike the policy, which checks each action across all grants. Adding `execute` on a sub-scope to an operator was shown as no change, so it applied unconfirmed, while the engine would accept approval requests there | Scopes are computed per action across all grants (`_scopes`). Escalation is also judged from the raw change (higher role, added allow grant, added actions, removed deny, added permission). Unit test with split grants |
| 2 | Medium | When the agent was disabled (or Code Mode off, or a tool inactive), the effective matrix was "denied" before and after, so a widening applied unconfirmed and took effect when the agent was enabled | Escalation is also computed as if the agent were enabled, Code Mode on and every tool active, plus the raw-change rule. Unit test |
| 3 | Low | A failed reload after the commit was reported as "nothing changed" | `StoredNotLoaded`: the agent's tokens are blocked in memory (fail closed, like `revoke`). The answer says the change was stored; the outcome is audited as `unknown` |
| 4 | Low | Refused applies were not audited | Every refused apply is audited (`permission_change`, `refused`, with the code) |
| 5 | Low | `/api/grants/add` skipped the new confirmation | From the web, an allow grant or the removal of a deny needs `confirm_escalation`; refusals are audited. TouchDesigner's native pulses are the confirmation there. Live check `caps.WEB_GRANT_ADD_NEEDS_CONFIRMATION` |
| 6 | Low | An agent-supplied link leads to the pairing fallback (Web page > Approve Pairing), where a local process could have created the request | The tool, whoami, the Skill and the guidance tell the user to pulse Open Dashboard in TouchDesigner, so the Browser sign-in dialog appears. The address is for reference only, and agents never ask the user to approve a sign-in TouchDesigner did not open |

**Confirmed sound by the review:**
- the gate covers the new endpoints: admin session and CSRF;
- the digest binds the stored state and the change;
- open approvals are cancelled in the same transaction;
- the role ceiling, root, hard denies, deny list and allowlist cannot be edited;
- the dialog's button-to-code mapping;
- `textContent`-only rendering;
- no token, CSRF value, cookie or path in the new outputs.

## Independent review 22 (usability)
**0 High, 7 Medium and 7 Low.**

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | "Denied" meant three things; the legend had no definitions | The legend defines each status. The role columns are headed "Most each role can be given" and the agent column "This agent now". Denied agent cells name the reason |
| 2 | Medium | Code Mode suggested only a role change | "It needs the Operator role and Code Mode on (Settings)", with the reason "role + Code Mode" |
| 3 | Medium | After Preview, the result was below the fold | The result and the status are at the top of the editor, and Apply scrolls into view. A browser check confirms it is on screen |
| 4 | Medium | Grant rewrites were hidden | "Scopes after this change", with `(+ execute)` / `(- …)` |
| 5 | Medium | The role in the agent's name went stale | A trailing role label is updated with the role (noted in the preview) |
| 6 | Medium | At 1366–1600 px the editor was far below the matrix | Tried two columns from 1200 px: rows wrapped to 48 px and the matrix grew to 1.5 screens (browser check), so reverted. Two columns from 1500 px; narrower windows get a **Change permissions** button that jumps to the editor. The detail line is sticky at the bottom of the window |
| 7 | Medium | Suspended or MCP off showed only in grey text | A banner above the matrix |
| 8 | Medium | A scope typed but not added was silently ignored | Enter in the scope box adds it. Preview says "Click Add scope to include …" |
| 9 | Low | Memory rows show Available without project memory | Accepted for 1.0 (feature freeze): the note says "When the project has project memory", and the memory tools answer `MEMORY_UNAVAILABLE` with the next step |
| 10 | Low | Wording | "Supported" column; "Scopes (a deny overrides an allow)"; role options without the action list (shown under the select); "(off)" next to Code Mode |
| 11 | Low | 29 tab stops; Protected looked like a toggle; agent change discarded the draft | A roving tabindex (one stop, arrow keys); a neutral Protected chip with ⊘; a confirmation before discarding a draft |
| 12 | Low | The other refusal was too long for the parameter window | `Not ready: see Diagnostics` |
| 13 | Low | Choice numbers next to codes containing digits | Lines read `Button 1:  ABC-DEF`; the last line says a wrong button denies |
| 14 | Low | The code wrapped inside the hint sentence | The sentence no longer contains the code ("the code below") |

## Validation
- **Unit:** `tests/unit/test_capabilities.py`. Every matrix status is checked against `Policy.evaluate` for 7 agent fixtures. Also covered:
  - protected rows are hard denies even for a full operator with approval;
  - every active tool is in the catalog;
  - plan, escalation, refusals, digest and rename;
  - an atomic apply that cancels approvals;
  - `tdflow_capabilities` and whoami without secrets.
- **TouchDesigner 1B suite** (`caps.*`):
  - the API against live behavior (builder denied/available and observer denied, as shown);
  - the agent's own view and page address, without secrets;
  - refusals (unsupported change, scope outside the root, stale preview, unconfirmed escalation, the older grant endpoint);
  - a confirmed escalation taking effect at once, and a narrowing without confirmation;
  - approved-but-unused requests cancelled and not runnable;
  - every change audited;
  - **native/web parity in both directions** (a web role change shows on the Agents page; a scope added there shows on the page).

  Also covered: `order.*` (Run Diagnostics, Enable MCP, Suspend Agents first; the refusal fits) and `web.SIGN_IN_*` (numbered codes, short buttons).
- **Native suite:** a real Claude Code session asked "What can you do…?" (`caps.*`).
- **Browser** (`tests/web/run_web_screens.py`, headless Edge, real page files, stub API built by the real module):
  - the pairing code at 360–1920 px on both pages, with three representative codes (no clipping, readable size, clickable, keyboard focus visible);
  - the matrix at 1280–1920 px (no clipped status, no horizontal scroll, compact rows, layout);
  - the edit flow (preview stores nothing, escalation warning, Apply only after confirming, refresh, unsupported change refused, the Agents tab using the same preview and confirmation, keyboard).
- **Visual review:** the real Browser sign-in dialog in TouchDesigner (`signin.*`) and the reordered main page.

Observed results: [validation index](../icm/validation-index.md).

## Feature freeze
Once rc6's acceptance gates passed, the 1.0 feature set was **frozen** (owner D25). From here only release-blocking defect corrections, documentation corrections and required compatibility fixes are made before 1.0. Outside the verified Windows 1.0 scope, and left open:
- macOS;
- an observed CI run (B3);
- newer TouchDesigner builds;
- the features listed as not implemented.
