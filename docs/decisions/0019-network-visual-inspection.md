# ADR-0019 — Network visual inspection

**Status:** Proposed. [Issue #19](https://github.com/jlopesmo/TDflowMCP/issues/19) authorizes the scope-filtered diagram if pane capture cannot satisfy the privacy boundary.

## Context

Agents need an image to see the arrangement of operators they can read and structured facts to verify paths, positions and wires. The pane APIs expose owner, bounds and view state. A disposable probe of Windows with TouchDesigner 2025.32440 exposed no pane image member. A Network Editor may display selected parameters, data links and image backdrops. [Pane API](https://derivative.ca/UserGuide/Pane_Class) · [Network Editor API](https://derivative.ca/UserGuide/NetworkEditor_Class) · [Pane screenshot request](https://forum.derivative.ca/t/ui-panes-screenshot-path/986628)

## Proposed decision

Expose a read-only diagram for one exact COMP, with a PNG and the same authorized direct-child positions and wires as structured data. Snapshot metadata on the main thread after resolving the exact COMP identity and authorizing the parent and children under the current grants. Build the image from that plain data. Reuse the existing audited MCP image response. Recheck authority if rendering continues outside the main-thread dispatch.

Fit the canvas to authorized nodes and draw wires whose two endpoints are in the returned set. Use image identifiers and a structured legend. Omit backgrounds, thumbnails, comments, parameter values, DAT text and child contents. No hidden endpoint, node count or hidden geometry belongs in the result. A partial flag describes only output limits applied to readable facts. A generic resource refusal provides no counts. Bound child and connector work, node and edge counts, image dimensions and byte size, and the image rate.

The diagram represents a point-in-time observation. Use structured verification and the existing scoped tools and approvals before making a supported correction. Node layout editing is outside this change.

## Alternatives

- **Native pane capture.** Existing view metadata identifies the pane owner and its extent. The tested runtime exposed no supported pane image method.
- **Owner-triggered OS-region capture.** Identifying a region would leave its pixels untrusted. Other panes, overlays, confidential backdrops or unreadable nodes can share the region. A verified scope-preserving crop and proof of safe content would need a separate decision. No OS pixels were read in the spike.
- **Render authorized metadata.** An image can convey relative arrangement and visible wiring independently of view state, monitor and DPI. It omits native thumbnails, operator chrome and view annotations.

## Validation gates

Verify equivalent image bytes and structured facts when only unreadable nodes or wires change within the resource limits. Check scope, protected nodes, stale identities, revocation, audit failure, rate and size limits, passive cook and callback counters, and the fit of an intentionally disorganized network. Exercise real MCP clients and separately record whether a real model used the image. [Milestone](../milestones/v1.6-network-vision.md)
