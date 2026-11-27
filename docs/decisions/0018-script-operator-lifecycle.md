# ADR-0018 — Script operator lifecycle

**Status:** Proposed. [Issue #14](https://github.com/jlopesmo/TDflowMCP/issues/14) and [Issue #20](https://github.com/jlopesmo/TDflowMCP/issues/20) authorize disposable research. A changed production execution policy requires explicit owner acceptance of a proven design.

## Context

Script CHOP, SOP and TOP select a callback DAT. Their normal workflow includes cooking, setup, and parameter callbacks. The selected DAT can differ from the factory DAT that appears docked in the network. An approval must bind the source and the runtime effect the owner is authorizing. [Permission model](0009-permission-model.md) · [Script CHOP](https://docs.derivative.ca/Script_CHOP) · [Script SOP](https://docs.derivative.ca/Script_SOP) · [Script TOP](https://docs.derivative.ca/Script_TOP)

## Proposed decision

Require a staging boundary that has independent controls for construction, configuration, source capture, and activation. A candidate policy must verify the Script operator and authored DAT identities and their exact association. It must bind approval to the source hash, runtime identity, cook and bypass state, input and parent dependencies, and the intended activation. Recheck current grants, protected paths, leases, kill switch and revocation immediately before dispatch. Store intent first and require a durable outcome. An unverified result suspends agent operations and requires owner recovery.

The isolated probe did not satisfy the no-cook gate. The v1.5 release path retains the existing creation allowlist and adds guidance for supported authoring. The active Script lifecycle remains deferred under the existing execution policy.

## Alternatives

- Parent cooking control. The disposable probe observed a cook after the normal Setup Parameters action even with parent cooking disabled.
- Infer ownership from docking. Changing the callback selection left the factory DAT docked. The selected identity requires a separate check.
- Separate isolated staging with explicit transfer. It needs evidence that creation, inspection, transfer, and restart preserve the activation boundary. It remains a candidate for a future owner decision.

## Consequences

Approved expressions and Execute DATs remain the supported procedural execution path. Approved TouchDesigner Python has host access. The operator guidance uses a bounded set of authoring facts verified from disposable runtime instances and carries no activation permissions.

## Validation gates

1. Verify inert creation, source inspection, parameter and topology changes, stale identities and source, controlled activation, and recovery after interruption or restart.
2. Verify both ends of each scoped reference, current authorization, exact replay, audit failures, concurrent editing, and callback effects.
3. Report independent security findings and obtain owner acceptance of the validated implementation boundary before enabling Script operator creation or activation.

The first probe results are recorded in the [v1.5 milestone](../milestones/v1.5-procedural-authoring.md).

The v1.5.0 tagged binary refused creation of Script CHOP, SOP and TOP through MCP in disposable TouchDesigner projects. The operator guidance and the approved procedural workflow passed on Windows with TouchDesigner 2025.32440. [Tagged verification](../icm/validation-index.md#v150-tagged-release)

## v1.7 research amendment

An off-by-default Advanced Script Authoring capability would separate inert source authoring from execution. It would use distinct owner-issued, per-agent, project- and path-scoped, expiring permissions and approval for each exact action. The draft preview is text and structured evidence. A production activation surface would require a separately verified boundary and owner decision. Existing Script creation denies, protected resources, mandatory audit and suspension remain in effect.

The disposable transfer and restart probe observed zero callbacks for a controlled cold fixture. Exact dry-run approvals failed after import or restart and after changes to inputs, callback identity or source. Explicit fixture activation ran in the destination TouchDesigner process, and a graph rollback could not erase the faulting callback's observed effect. Active Script authoring stays deferred. [Threat model, state transitions and owner options](../milestones/v1.7-script-access.md) · [Observed verification](../icm/validation-index.md#v17-script-access-research)
