"""Owner-built, disposable network for passive context and search benchmarks."""
import td

import td_runner_gate as gate


def node(parent, cls, name, upstream=None):
    child = parent.create(cls, name)
    child.viewer = False
    if upstream is not None:
        child.inputConnectors[0].connect(upstream.outputConnectors[0])
    return child


def run(comp, ext, T, frames, until, checkpoint, R):
    R["component_version"] = comp.par.Version.eval()
    root = node(td.op("/project1"), td.baseCOMP, "fx")
    signals = node(root, td.baseCOMP, "signals")
    oscillator = node(signals, td.noiseCHOP, "oscillator")
    motion = node(signals, td.mathCHOP, "u_motion", oscillator)
    controls = node(signals, td.baseCOMP, "rack")
    fade = controls.appendCustomPage("Controls").appendFloat("Fade", label="Fade")[0]
    fade.val = .65
    picture = node(root, td.baseCOMP, "picture")
    group = node(picture, td.baseCOMP, "grade")
    plate = node(group, td.constantTOP, "plate")
    level = node(group, td.levelTOP, "exposure", plate)
    level.par.opacity.expr = "op('/project1/fx/signals/rack').par.Fade"
    output = node(group, td.nullTOP, "display", level)
    fault = node(group, td.levelTOP, "readback")
    fault.par.opacity.expr = "1 / 0"
    private = node(signals, td.baseCOMP, "restricted_canary")
    private.appendCustomPage("Private").appendFloat("Secret")[0].val = .2
    hidden_user = node(group, td.levelTOP, "aux_grade", plate)
    hidden_user.par.opacity.expr = "op('/project1/fx/signals/restricted_canary').par.Secret"
    dynamic = node(group, td.levelTOP, "mixed_math", plate)
    dynamic.par.opacity.expr = "op(''.join(['exposure'])).par.opacity"
    protected = node(root, td.baseCOMP, "protected_canary")
    protected.tags.add("tdflowProject")
    protected_leaf = node(protected, td.constantTOP, "protected_child")
    for parent in (signals, group):
        for i in range(24):
            node(parent, td.nullTOP, f"spare_{i:02d}")
    # Unrelated passive callback fixture. Its callback counter is inspected from the owner side.
    watcher = node(group, td.parameterexecuteDAT, "passive_watch")
    watcher.text = ("def onValueChange(par, prev):\n"
                    "    me.store('v18_callbacks', me.fetch('v18_callbacks', 0) + 1)\n")
    watcher.par.op, watcher.par.pars = plate.path, "colorr"
    watcher.par.valuechange, watcher.par.active = True, True
    # Create the actual memory store before the protected canary is recognized as a second memory COMP.
    protected.tags.discard("tdflowProject")
    yield from gate.open_and_pair(comp, ext, T, frames, until, checkpoint)
    comp.par.Createprojectmemory.pulse()
    yield until(lambda: td.op("/project1/tdflowProject") is not None, 10, "memory_created")
    yield from gate.enable(comp, until, "network_intelligence_enable")
    fields = {"root": root.path, "signals": signals.path, "group": group.path, "picture": picture.path,
              "rack": controls.path, "motion": motion.path, "oscillator": oscillator.path, "plate": plate.path,
              "level": level.path, "output": output.path, "fault": fault.path, "private": private.path,
              "protected": protected.path, "hidden_user": hidden_user.path, "dynamic": dynamic.path}
    yield checkpoint("intelligence_seed", fields)
    # Owner edit between the stored task checkpoint and model resume.
    fade.val = 0
    watched = [oscillator, motion, plate, level, output, fault, hidden_user, dynamic, protected_leaf]
    yield frames(6)
    try:
        fault.cook(force=True)
    except td.Error:
        pass
    T("intelligence.owner_observed_fault", bool(fault.errors()), cooks=fault.totalCooks)
    def observation():
        return {"cooks": {op.name: op.totalCooks for op in watched}, "callbacks": watcher.fetch("v18_callbacks", 0)}

    before = observation()
    settings = yield checkpoint("intelligence_benchmark", fields)
    after = observation()
    T("intelligence.scripted_passive", before == after, before=before, after=after)
    yield checkpoint("intelligence_clients", fields, seconds=730)
    live = observation()
    T("intelligence.live_passive", after == live, before=after, after=live)
    if not settings.get("candidate"):
        return

    backup = controls.appendCustomPage("Bindings").appendFloat("Backup")[0]
    backup.bindExpr = "op('rack').par.Fade"
    level.par.contrast.expr = "op('/project1/fx/signals/u_motion')['chan1']"
    restricted_selector = node(group, td.selectTOP, "restricted_selector")
    restricted_selector.par.top.expr = "op('/project1/fx/signals/restricted_canary')"
    hidden_wire = node(group, td.constantTOP, "restricted_wire")
    hidden_sink = node(group, td.nullTOP, "neutral_sink")
    watched.extend((restricted_selector, hidden_wire, hidden_sink))
    fields.update(hidden_wire=hidden_wire.path, hidden_sink=hidden_sink.path, selector=restricted_selector.path)
    protected.tags.add("tdflowProject")
    ext.tdops.refresh_protected()
    passive_before = observation()
    yield checkpoint("intelligence_security", fields)
    passive_after = observation()
    T("intelligence.compact_reads_passive", passive_before == passive_after, before=passive_before, after=passive_after)
    node(private, td.constantTOP, "silent_unreadable_child")
    hidden_sink.inputConnectors[0].connect(hidden_wire.outputConnectors[0])
    hidden_user.par.opacity.expr = "op('/project1/fx/signals/absent_canary').par.Secret"
    yield checkpoint("intelligence_hidden_changed", fields)
    mesh = node(root, td.baseCOMP, "mesh")
    channels = [node(mesh, td.nullCHOP, f"channel_{i:02d}") for i in range(15)]
    for i in range(9):
        merge = node(mesh, td.mergeCHOP, f"merge_{i:02d}")
        for index, channel in enumerate(channels):
            merge.inputConnectors[index].connect(channel.outputConnectors[0])
    yield checkpoint("intelligence_truncated", {"path": mesh.path})
    unshown = node(mesh, td.nullCHOP, "zz_hidden_feed")
    mesh.op("merge_00").inputConnectors[15].connect(unshown.outputConnectors[0])
    yield checkpoint("intelligence_truncated_after_hidden", {"path": mesh.path})
    large = node(root, td.baseCOMP, "bounded")
    for i in range(300):
        node(large, td.nullTOP, f"item_{i:03d}")
    yield checkpoint("intelligence_before_hidden_sibling", {"path": large.path})
    node(large, td.nullTOP, "hidden_extra")
    yield checkpoint("intelligence_after_hidden_sibling", {"path": large.path})
    node(large, td.nullTOP, "item_300")
    yield checkpoint("intelligence_bounded", {"path": large.path})
    comp.par.Suspendagents = True
    yield until(ext.engine.suspended, 10, "inspection_suspended")
    yield checkpoint("intelligence_suspended", fields)
    comp.par.Suspendagents = False
    yield until(lambda: not ext.engine.suspended(), 10, "inspection_resumed")
    yield checkpoint("intelligence_minimized", fields, seconds=45)
    yield checkpoint("intelligence_revoke", fields)
