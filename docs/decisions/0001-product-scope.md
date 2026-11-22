# ADR-0001 — Product scope and pillars

**Status:** Mission **Accepted** (owner brief, 2026-11-22). Pillar phasing, non-goals and success criteria **Proposed**.

## Context
The owner brief defines TDflowMCP as a source-first, AI-powered TouchDesigner development environment combining MCP, Code Mode, a local browser dashboard, enforceable permissions, audit logging and agent workflows, with eight architectural requirements.

## Decision
Adopt the eight requirements as pillars P1–P8 in the [product vision](../product/vision.md). The proposed phasing delivers enforcement and audit **before** convenience features (Code Mode, experiments, extensions). An insecure-but-capable server would duplicate existing projects and erase the differentiator identified in [research §5](../research/2026-11-22-landscape-and-comparison.md#5-key-conclusions-i).

## Alternatives
- Feature parity first (match TDMCP/Envoy tool counts), security later. Not proposed: retrofitting enforcement onto dozens of tools is error-prone.

## Consequences
Phase 1 has fewer tools than upstream projects. Every tool is born with a target resolver and audit.

## Owner approval needed
Confirm phasing, non-goals (local-only, no cloud) and success criteria.
