"""Research-only Script draft approval checks. No TouchDesigner or execution entry point."""

import ast
from dataclasses import dataclass
import hashlib
import json
import re


SCRIPT_TYPES = frozenset({"scriptCHOP", "scriptSOP", "scriptTOP"})
PATH = re.compile(r"/[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)*\Z")
MAX_DRAFT_BYTES = 64 * 1024


@dataclass(frozen=True)
class Grant:
    actor: str
    project: str
    scope: str
    action: str
    expires_at: int


def digest(value):
    data = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(data).hexdigest()


def source_preview(text):
    """Parse inert text for diagnostics. Import names are information for the owner."""
    raw = text.encode("utf-8")
    if len(raw) > MAX_DRAFT_BYTES:
        raise ValueError("Draft exceeds the probe's byte limit.")
    tree = ast.parse(text, filename="<script draft>", mode="exec")
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(name.name for name in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "imports": sorted(imports), "bytes": len(raw)}


def eligibility(grant, facts, planned_digest, *, actor, action, now, suspended=False, revoked=False,
                audit_ready=True, consumed=False):
    """Check a candidate intent. Eligibility here does not grant any runtime action."""
    if suspended or revoked or not audit_ready or consumed:
        return "unavailable"
    if (grant.actor != actor or grant.project != facts["project"] or grant.action != action
            or action not in ("draft", "activate") or now >= grant.expires_at
            or facts["type"] not in SCRIPT_TYPES or not PATH.fullmatch(grant.scope)):
        return "denied"
    paths = [facts["target"], facts["selected_dat"], facts["parent"], *facts["inputs"]]
    if facts["protected"] or any(not PATH.fullmatch(path) or
                                 (path != grant.scope and not path.startswith(grant.scope + "/"))
                                 for path in paths):
        return "denied"
    if facts["callback_mode"] != "CONSTANT" or facts["callback_source_kind"] != "authored_text":
        return "denied"
    return "eligible_for_review" if digest({"action": action, "facts": facts}) == planned_digest else "changed"
