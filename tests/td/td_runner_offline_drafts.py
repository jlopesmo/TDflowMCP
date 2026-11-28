"""Native observation for the offline draft workspace in a disposable project."""

import os
from pathlib import Path

import td
import td_runner_gate as gate


def surface(fx, probe):
    return {"children": [(n.path, n.OPType, int(n.id)) for n in fx.children],
            "cooks": probe.totalCooks, "callback": probe.fetch("draft_callback", 0),
            "panes": [str(getattr(p, "owner", "")) for p in td.ui.panes]}


def run(mode, comp, ext, T, frames, until, checkpoint, R):
    project = td.op("/project1")
    fresh = mode == "offline_drafts"
    if fresh:
        fx = project.create(td.baseCOMP, "fx")
        fx.allowCooking = False
        fx.viewer = False
        probe = fx.create(td.constantTOP, "existing")
        probe.viewer = False
        protected = fx.create(td.baseCOMP, "protected")
        protected.tags.add("tdflowProject")
    else:
        fx, probe = project.op("fx"), project.op("fx/existing")
    yield from gate.open_and_pair(comp, ext, T, frames, until, checkpoint)
    yield from gate.enable(comp, until, "offline_drafts_enable")
    T("drafts.verified_identity", bool(ext.engine.draft_project) and ext.storage.project_id == ext.engine.draft_project)
    before = surface(fx, probe)
    if fresh:
        yield checkpoint("drafts_workflow", {}, seconds=480)
    else:
        yield checkpoint("drafts_restarted", {}, seconds=120)
    T("drafts.native_scope_unchanged", surface(fx, probe) == before)
    T("drafts.no_script_or_text_dat", not any(n.OPType in ("scriptCHOP", "scriptSOP", "scriptTOP", "textDAT")
                                            for n in fx.children))
    marker = Path(os.environ["TDFLOW_RUN_DIR"]) / "candidate-side-effect.txt"
    T("drafts.no_candidate_marker", not marker.exists())
    T("drafts.audit_chain_verifies", (yield from gate.store_op(ext, until, "verify"))["ok"])
    if fresh:
        original = ext.worker._store.append

        def unavailable(kind, **fields):
            if fields.get("tool") == "tdflow_script_draft_create" and kind == "intent":
                raise ext._m.store.AuditUnavailable("offline test intent unavailable")
            return original(kind, **fields)

        try:
            ext.worker._store.append = unavailable
            yield checkpoint("drafts_failed_audit", {})
        finally:
            ext.worker._store.append = original
        T("drafts.audit_fault_suspends", ext.engine.suspended())
        comp.par.Suspendagents = False
    comp.par.Enablemcp = False
    yield frames(10)
    if fresh:
        coin = td.op("/project1/test_driver/drive")
        coin.par.active = False
        td.project.save()
        coin.par.active = True
        R["drafts_toe"] = str(Path(td.project.folder) / td.project.name)
    else:
        original = ext.engine.draft_project
        run_dir = Path(os.environ["TDFLOW_RUN_DIR"]).resolve()
        current = (Path(td.project.folder) / td.project.name).resolve()
        if not current.is_relative_to(run_dir.parent / "project"):
            raise RuntimeError("The move fixture requires the generated scratch project.")
        # Keep older incremental saves outside the project folder so this tests a move.
        backups = run_dir / "prior-save-files"
        backups.mkdir()
        for sibling in current.parent.glob("*.toe"):
            candidate = sibling.resolve()
            if (candidate.parent == current.parent and candidate != current
                    and ext._m.storage.same_project_file(str(candidate), str(current))):
                candidate.rename(backups / candidate.name)
        ext = yield from gate.relocation(comp, ext, T, frames, until, checkpoint, R)
        claimed = ext.engine.draft_project
        owner_view = yield from gate.store_op(ext, until, "draft_request", claimed, "owner_list", {},
                                                {"tool": "offline_draft_fixture_claim"},
                                                ext.engine.policy, {}, lambda: True)
        T("drafts.copied_project_claim_has_no_inherited_drafts_or_grants",
          bool(claimed) and claimed != original and owner_view.get("drafts") == [] and owner_view.get("grants") == [])
    R["component_version"] = str(comp.par.Version.eval())
