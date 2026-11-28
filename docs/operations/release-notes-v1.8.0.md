# TDflowMCP v1.8.0 release notes draft

## Network intelligence

- Use optional compact pages in `tdflow_describe_network` to inspect nested readable components and find operators by name, type, curated purpose or recorded diagnostic state. Existing detailed inspection remains available.
- Request exact known paths to skip sibling enumeration. Read native wires with port indexes between returned operators and trace named literal parameter or CHOP references. Confirm the current value with a named parameter read.
- The opt-in operator catalog now covers 33 TOP, CHOP, SOP, DAT and COMP types with 85 parameter entries checked against the installed runtime. Skill 1.16 and Copy Agent Prompt explain inspection bounds, pagination and exact paths. [Inspection guide](../architecture/tool-surface.md#compact-network-inspection)

## Measured performance

The matched benchmark compared v1.7.0 and v1.8 dev2 on the same disposable 24-, 64- and 216-operator networks. The four scripted workflows went from 12 calls to 5, 5 and 6, respectively. They returned 78–85% fewer HTTP response bytes. Real Claude Code and Codex each verified all nine facts at each size and used less time in MCP tool requests. Total elapsed time varied, and the largest Codex run returned more bytes. The benchmark separates tool request time from client elapsed time. [Detailed results](../icm/validation-index.md#v180dev2-evaluation)

## Compatibility and inspection limits

Verified runtime scope is **Windows with TouchDesigner 2025.32440**. Compact inspection runs on the TouchDesigner main thread under a cooperative work limit. Native enumeration can make broad requests fail or take longer on complex hidden networks. Exact paths avoid sibling enumeration, but denied and authorized missing targets can take different amounts of time. Missing and unreadable paths are omitted from successful responses. [ADR-0021](../decisions/0021-compact-network-inspection.md#residual-timing-and-availability-side-channel)
