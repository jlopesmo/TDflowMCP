"""Owner-side fixtures and outcome checks for the component workflow suite."""
import td

import td_runner_gate as gate


def consume(network, component):
    consumer = network.create(td.nullTOP, component.name + "_consumer")
    consumer.inputConnectors[0].connect(component.outputConnectors[0])
    return consumer


def run(comp, ext, T, frames, until, checkpoint, R):
    project = td.op("/project1")
    network = project.create(td.baseCOMP, "fx")
    broken = network.create(td.baseCOMP, "repair")
    source = broken.create(td.constantTOP, "source")
    source.par.colorr = 0.6
    level = broken.create(td.levelTOP, "out")
    level.par.opacity.expr = "1 / 0"
    try:
        level.cook(force=True)
    except Exception:
        pass
    reusable = network.create(td.baseCOMP, "reusable")
    reusable.appendCustomPage("Owner").appendStr("Keep", label="Owner control", replace=False)[0].val = "keep"
    definition_probe = network.create(td.baseCOMP, "definitions")
    replacement = network.create(td.baseCOMP, "replacement")

    yield from gate.open_and_pair(comp, ext, T, frames, until, checkpoint)
    yield from gate.enable(comp, until, "component_workflow_enable")
    requested = yield checkpoint("component_workflows", {})
    baseline = requested.get("baseline") is True

    visual = network.op("visual")
    generated = visual.op("out")
    generated.cook(force=True)
    T("A.live_texture", generated.totalCooks > 0 and not generated.errors() and generated.inputs[0] is not None)
    T("A.component_output", bool(visual.op("output") and visual.outputConnectors) == (not baseline))
    T("A.controls", bool(visual.customPars) == (not baseline), controls=[p.name for p in visual.customPars])
    if not baseline:
        consumer = consume(network, visual)
        consumer.cook(force=True)
        noise, tint = visual.op("noise"), visual.op("tint")
        before = consumer.numpyArray().copy()
        visual.par.Intensity, visual.par.Scale, visual.par.Speed = .3, 3, 0
        visual.par.Red, visual.par.Green, visual.par.Blue = .1, .2, .3
        consumer.cook(force=True)
        after = consumer.numpyArray()
        T("A.controls_drive_internal_parameters", abs(float(noise.par.amp.eval()) - .3) < 1e-6
          and noise.par.period.eval() == 3 and noise.par.tx.eval() == 0
          and all(abs(p.eval() - expected) < 1e-6
                  for p, expected in zip((tint.par.colorr, tint.par.colorg, tint.par.colorb), (.1, .2, .3))))
        T("A.reused_output_changes_pixels", before.shape == after.shape and bool((abs(before - after) > .01).any()))

    procedural = network.op("procedural")
    result = procedural.op("out")
    result.cook(force=True)
    T("B.approved_logic_drives_output", result.totalCooks > 0 and not result.errors()
      and abs(float(result.par.opacity.eval()) - 0.4) < 1e-6)
    if not baseline:
        procedural.par.Amount = .25
        result.cook(force=True)
        T("B.control_changes_python_result", abs(float(result.par.opacity.eval()) - .2) < 1e-6 and not result.errors())

    level.cook(force=True)
    T("C.repair_cooks_without_errors", level.totalCooks > 0 and not level.errors() and level.inputs[0] == source
      and abs(float(level.par.opacity.eval()) - 0.5) < 1e-6)
    snapshot = broken.create(td.nullTOP, "owner_snapshot")
    snapshot.inputConnectors[0].connect(level.outputConnectors[0])
    snapshot.viewer = False
    snapshot.cook(force=True)
    snapshot.lock = True

    T("D.preserve_owner_control", reusable.par.Keep.eval() == "keep")
    names = [p.name for p in reusable.customPars]
    T("D.controls", ("Amount" in names) == (not baseline), controls=names)
    T("D.component_output", bool(reusable.op("output") and reusable.outputConnectors) == (not baseline))
    if not baseline:
        consumer = network.create(td.nullTOP, reusable.name + "_consumer")
        observed = reusable.op("output"), consumer
        for node in observed:
            node.viewer = False
        yield frames(3)
        before_cooks = [node.totalCooks for node in observed]
        yield checkpoint("component_external_wire", {"source": reusable.path, "consumer": consumer.path})
        T("D.refused_wire_leaves_input_empty", not consumer.inputConnectors[0].connections)
        T("D.refused_wire_leaves_cook_counts", before_cooks == [node.totalCooks for node in observed])
        consumer.inputConnectors[0].connect(reusable.outputConnectors[0])
        before_cooks = [node.totalCooks for node in observed]
        if requested.get("verify_connectors"):
            yield checkpoint("component_external_wire_verified", {"source": reusable.path, "consumer": consumer.path})
            T("D.connector_reads_are_passive", before_cooks == [node.totalCooks for node in observed])
        consumer.cook(force=True)
        before = float(consumer.numpyArray()[0, 0, 0])
        reusable.par.Amount = .75
        consumer.cook(force=True)
        after = float(consumer.numpyArray()[0, 0, 0])
        T("D.reused_component_control_changes_output", abs(before - .5) < .02 and abs(after - .75) < .02,
          before=before, after=after)
    yield checkpoint("component_diagnostics", {"output": level.path, "snapshot": snapshot.path})
    if not baseline:
        yield checkpoint("component_definition_tests", {})
        T("definitions.recovery_leaves_original_page", [p.name for p in definition_probe.customPars]
          == ["Owner", "Gain", "Count", "Enabled", "Mode", "Title", "Reset"])
        definition_probe.par.Gain.label = "Owner changed"
        replacement.destroy()
        replacement = network.create(td.baseCOMP, "replacement")
        yield checkpoint("component_definition_stale", {})
        T("definitions.stale_approval_left_owner_label", definition_probe.par.Gain.label == "Owner changed")
        T("definitions.replaced_target_kept_empty", not replacement.customPars)
        adapter = ext._m.tdops.custompars
        apply = adapter.apply

        def refuse_after_first_write(op, name, state):
            adapter.apply = apply
            apply(op, name, state)
            raise ValueError("disposable fault injection")

        try:
            adapter.apply = refuse_after_first_write
            yield checkpoint("component_definition_rollback", {})
        finally:
            adapter.apply = apply
        T("definitions.rollback_removed_injected_page", all(p.name != "Rollback" for p in definition_probe.customPages))
        yield from control_review_tests(network, ext, T, checkpoint)
        yield checkpoint("component_definition_revocation", {})
        T("definitions.revoked_owner_approval_left_no_page", all(p.name != "Revoked" for p in definition_probe.customPages))
        yield from script_probe(network, T, frames)
    R["component_version"] = str(comp.par.Version.eval())
    comp.par.Enablemcp = False
    yield frames(5)


def control_review_tests(network, ext, T, checkpoint):
    from dataclasses import replace

    probe = network.create(td.baseCOMP, "grant_controls")
    page = probe.appendCustomPage("Controls")
    page.appendFloat("Locked")
    page.appendFloat("Open")
    data = {"path": probe.path}
    yield checkpoint("component_grant_prepare", data)
    identity = ext.identity
    agent = next(a for a in identity.agents.values() if a.display_name.startswith("Agent E"))
    # The setup UI has scope presets. Install a constrained Grant on this disposable identity only.
    identity.agents[agent.id] = replace(agent, grants=tuple(
        replace(grant, param_denylist=("Locked",)) for grant in agent.grants))
    try:
        yield checkpoint("component_grant_denials", data)
        T("definitions.denials_preserve_metadata_and_order",
          probe.par.Locked.label == "Approved change" and probe.par.Open.label == "Open"
          and [par.name for par in page.pars] == ["Locked", "Open"])
        yield checkpoint("component_grant_allow", data)
        T("definitions.allowed_edit_applied", probe.par.Open.label == "Allowed change")
        yield checkpoint("component_grant_undo", data)
        T("definitions.allowed_undo_restored", probe.par.Open.label == "Open" and probe.par.Locked.label == "Approved change")
    finally:
        identity.agents[agent.id] = agent

    probe = network.create(td.baseCOMP, "full_page")
    page = probe.appendCustomPage("Controls")
    for i in range(1, 80):
        page.appendFloat(f"Slot{i}")
    yield checkpoint("component_page_limit", {"path": probe.path})
    expected = [f"Slot{i}" for i in range(1, 81)]
    T("definitions.capacity_and_reorder_verified",
      [par.name for par in page.pars] == expected[::-1] and probe.par.Slot1.default == 2
      and probe.par.Slot80.label == "Last slot")
    yield checkpoint("component_page_undo", {})
    T("definitions.undo_restores_full_page_order", [par.name for par in page.pars] == expected)


def script_probe(network, T, frames):
    """Measure owner-driven staging only. The MCP server never creates or activates these nodes."""
    stage = network.create(td.baseCOMP, "script_lifecycle_probe")
    stage.allowCooking = False
    nodes = []
    try:
        for cls in (td.scriptCHOP, td.scriptSOP, td.scriptTOP):
            node = stage.create(cls)
            node.viewer = False
            callback = next(child for child in node.docked if child.isDAT)
            callback.text = ("def onCook(scriptOp):\n"
                             "    scriptOp.store('runs', scriptOp.fetch('runs', 0) + 1)\n")
            nodes.append(node)
        yield frames(5)
        staged = [(node.totalCooks, node.fetch("runs", 0)) for node in nodes]
        T("script_probe.inactive_staging", staged == [(0, 0)] * len(nodes), staged=staged)
        stage.allowCooking = True
        for node in nodes:
            node.cook(force=True)
        active = [(node.totalCooks, node.fetch("runs", 0)) for node in nodes]
        T("script_probe.owner_activation_runs_callbacks", all(cook > 0 and run > 0 for cook, run in active), active=active)
    finally:
        stage.allowCooking = False
        stage.destroy()
