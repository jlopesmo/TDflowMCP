"""Data-only transfer and receiving-state bindings for the Issue #24 fixture."""

from dataclasses import dataclass
import hashlib
import json
import re

if __package__:
    from .script_intent import PATH, SCRIPT_TYPES, digest, source_preview
else:
    from script_intent import PATH, SCRIPT_TYPES, digest, source_preview


MAX_MANIFEST_BYTES = 128 * 1024
NAME = re.compile(r"[A-Za-z0-9_]{1,48}\Z")
HOST_WARNING = (
    "TouchDesigner Python can affect host files, network and native resources outside the agent scope. "
    "Restoring the graph cannot reverse every effect."
)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest field.")
        result[key] = value
    return result


def _valid_wire(wire):
    return (type(wire) is list and len(wire) == 3 and type(wire[0]) is str and NAME.fullmatch(wire[0])
            and all(type(index) is int and 0 <= index < 16 for index in wire[1:]))


def read_manifest(raw):
    """Validate bounded source text and declarative wiring. This function never loads a graph."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_MANIFEST_BYTES:
        raise ValueError("Manifest size outside the research limit.")
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    if (type(data) is not dict or set(data) != {"schema", "operators"} or type(data["schema"]) is not int
            or data["schema"] != 1 or type(data["operators"]) is not list
            or len(data["operators"]) != len(SCRIPT_TYPES)):
        raise ValueError("Unsupported transfer manifest.")
    families = set()
    names = set()
    for row in data["operators"]:
        if type(row) is not dict or set(row) != {"family", "operator", "callback", "source", "wires"}:
            raise ValueError("Unsupported operator declaration.")
        family = row["family"]
        if type(family) is not str or family not in SCRIPT_TYPES or family in families:
            raise ValueError("Expected each Script family once.")
        families.add(family)
        for name in (row["operator"], row["callback"]):
            if type(name) is not str or not NAME.fullmatch(name) or name in names:
                raise ValueError("Expected distinct local names.")
            names.add(name)
        wires = row["wires"]
        if type(wires) is not list or len(wires) > 16 or not all(_valid_wire(wire) for wire in wires):
            raise ValueError("Expected bounded declarative input port references.")
        if len({wire[2] for wire in wires}) != len(wires):
            raise ValueError("Expected one source for each input port.")
        if type(row["source"]) is not str:
            raise ValueError("Expected source text.")
        row["source_facts"] = source_preview(row["source"])
    return data, digest(data)


def verify_transfer(raw, expected_bytes, expected_manifest):
    if hashlib.sha256(raw).hexdigest() != expected_bytes:
        raise ValueError("Transferred bytes changed since review.")
    data, manifest_hash = read_manifest(raw)
    if manifest_hash != expected_manifest:
        raise ValueError("Declarative manifest changed since review.")
    return data, manifest_hash


@dataclass(frozen=True)
class Lease:
    lease_id: str
    actor: str
    project: str
    scope: str
    action: str
    expires_at: int


def _in_scope(path, scope):
    return PATH.fullmatch(path) and (path == scope or path.startswith(scope + "/"))


def binding(action, actor, lease, manifest_hash, live, *, now, policy, revoked=False, suspended=False):
    """Bind a review to server-observed fixture facts. A digest confers no permission."""
    if (revoked or suspended or action not in ("author", "activate") or lease.action != action
            or lease.actor != actor or live["project"] != lease.project or now >= lease.expires_at
            or live["policy"] != policy or not PATH.fullmatch(lease.scope)
            or live["build"] != "2025.32440"):
        raise ValueError("Current authority does not permit fixture review.")
    for row in live["operators"]:
        paths = [row["path"], row["callback_path"], row["parent"], *row["dependencies"],
                 *(wire[0] for wire in row["outputs"])]
        if (row["protected"] or row["callback_mode"] != "CONSTANT" or row["source_kind"] != "authored_text"
                or any(not _in_scope(path, lease.scope) for path in paths)):
            raise ValueError("Selected callback or dependency is outside the fixture policy.")
    return digest({"action": action, "lease": vars(lease), "manifest": manifest_hash, "live": live,
                   "warning": HOST_WARNING})
