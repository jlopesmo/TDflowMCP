# TDflowMCP v1.8.0 release notes draft

## Network intelligence

- Use optional compact pages in direct `tdflow_describe_network` MCP calls to inspect nested readable components and find operators by name, type, curated purpose or recorded diagnostic state. Code Mode continues to use the existing detailed and diagnostic forms.
- Request exact known paths to skip sibling enumeration. Read native wires with port indexes between returned operators and trace named literal parameter or CHOP references. Confirm the current value with a named parameter read.
- The opt-in operator catalog covers 33 TOP, CHOP, SOP, DAT and COMP types. The runtime verified the names, labels and styles of 85 selected parameters. A catalog query with `guidance: true` can also match curated guide text on the verified runtime. Skill 1.16 and Copy Agent Prompt explain inspection bounds, pagination and exact paths. [Inspection guide](../architecture/tool-surface.md#compact-network-inspection)

## Measured performance

The matched benchmark compared v1.7.0 and v1.8 dev2 on the same disposable 24-, 64- and 216-operator networks. The four scripted workflows went from 12 calls to 5, 5 and 6, respectively. They returned 78–85% fewer HTTP response bytes. Real Claude Code and Codex each verified all nine facts at each size and spent less time in MCP tool requests. Claude Code finished sooner on the small and medium fixtures and took longer on the large one. Codex took longer end to end at all three sizes, and its largest run returned more bytes. [Detailed results](../icm/validation-index.md#v180dev2-evaluation)

## Compatibility and inspection limits

Verified runtime scope is **Windows with TouchDesigner 2025.32440**. Compact inspection runs on the TouchDesigner main thread under a cooperative work limit. Native enumeration can make broad requests fail or take longer on complex hidden networks. Exact paths avoid sibling enumeration. Denied and authorized missing lookups can still take different amounts of time, such as when reading a named parameter reference. Native wire inspection can also spend time on links that the response omits. [ADR-0021](../decisions/0021-compact-network-inspection.md#residual-timing-and-availability-side-channel)
