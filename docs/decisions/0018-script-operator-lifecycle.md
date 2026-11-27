# ADR-0018 — Script operator lifecycle

**Status:** Proposed. [Issue #14](https://github.com/jlopesmo/TDflowMCP/issues/14) authorizes an inert prototype and requires owner approval before changing the execution policy.

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
