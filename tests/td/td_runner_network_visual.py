"""Disposable TouchDesigner fixtures for network diagrams."""

import td

import td_runner_gate as gate


def _node(graph, cls, name, x, y):
    op = graph.create(cls, name)
    op.viewer = False
    op.nodeX, op.nodeY = x, y
    return op


def _pane_state():
    return [(pane.id, pane.owner.path if pane.owner else None, pane.x, pane.y, pane.zoom)
            for pane in td.ui.panes if pane.open and hasattr(pane, "zoom")]


def run(comp, ext, T, frames, until, checkpoint, R):
    fx = td.op("/project1").create(td.baseCOMP, "fx")
    fx.allowCooking = False
    graph = _node(fx, td.baseCOMP, "vision", 0, 0)
    source = _node(graph, td.constantTOP, "aa_source", 410, 210)
    stage = _node(graph, td.levelTOP, "bb_stage", -340, 210)
    wrong = _node(graph, td.constantTOP, "cc_wrong", 10, -230)
    output = _node(graph, td.nullTOP, "dd_output", 350, -55)
    stage.inputConnectors[0].connect(source.outputConnectors[0])
    output.inputConnectors[0].connect(wrong.outputConnectors[0])
    control = _node(graph, td.noiseCHOP, "ee_control", -220, -80)
    gain = _node(graph, td.mathCHOP, "ff_gain", 125, -180)
    gain.inputConnectors[0].connect(control.outputConnectors[0])
    group = _node(graph, td.baseCOMP, "gg_group", -210, -100)
    group.create(td.textDAT, "nested_fixture_text").text = "synthetic child comment"
    script = _node(graph, td.scriptTOP, "hh_passive", -110, 90)
    factory = next(dat for dat in script.docked if dat.OPType == "textDAT")
    factory.viewer = False
    factory.text = "def onCook(op):\n    op.store('diagram_calls', op.fetch('diagram_calls', 0) + 1)\n"
    callback = _node(graph, td.parameterexecuteDAT, "ii_callback", 170, 45)
    callback.text = ("def onValueChange(par, previous):\n"
                     "    me.store('diagram_calls', me.fetch('diagram_calls', 0) + 1)\n")
    callback.par.op, callback.par.pars = source.path, "colorr"
    callback.par.valuechange, callback.par.active = True, True
    target = _node(graph, td.nullTOP, "jj_idle", 220, 160)
    for i in range(10):
        cls = (td.nullTOP, td.nullCHOP, td.textDAT)[i % 3]
        _node(graph, cls, f"node_{i:02d}", (i * 118) % 740 - 360, (i * 173) % 410 - 210)
    hidden = _node(graph, td.constantTOP, "zz_private_fixture", 9000, 9000)
    protected = _node(graph, td.baseCOMP, "zz_protected_fixture", -9000, -9000)
    protected.tags.add("tdflowProject")
    protected.create(td.textDAT, "reserved_fixture_content").text = "synthetic restricted code"
    empty = _node(fx, td.baseCOMP, "vision_empty", 0, 0)
    large = _node(fx, td.baseCOMP, "vision_large", 0, 0)
    for i in range(68):
        _node(large, td.nullTOP, f"node_{i:02d}", (i % 12) * 65, (i // 12) * 45)
    stale = _node(fx, td.baseCOMP, "vision_stale", 0, 0)

    yield from gate.open_and_pair(comp, ext, T, frames, until, checkpoint)
    yield from gate.enable(comp, until, "network_visual_enable")
    yield frames(8)
    watched = (source, stage, wrong, output, script, factory, callback, target)
    before = ([node.totalCooks for node in watched], script.fetch("diagram_calls", 0),
              callback.fetch("diagram_calls", 0), _pane_state())
    yield checkpoint("vision_inspect", {"path": graph.path, "hidden": hidden.path, "factory": factory.path,
                                        "protected": protected.path, "empty": empty.path, "large": large.path,
                                        "stale": stale.path})
    after = ([node.totalCooks for node in watched], script.fetch("diagram_calls", 0),
             callback.fetch("diagram_calls", 0), _pane_state())
    T("vision.snapshot_passive", before == after, cook_before=before[0], cook_after=after[0],
      callback_before=list(before[1:3]), callback_after=list(after[1:3]), pane_unchanged=before[3] == after[3])

    hidden.nodeX, hidden.nodeY = -6000, 7200
    protected.nodeX, protected.nodeY = 7000, -8200
    target.inputConnectors[0].connect(hidden.outputConnectors[0])
    stale_path = stale.path
    stale.destroy()
    yield checkpoint("vision_hidden_changed", {"path": graph.path, "stale": stale_path})
    result = yield checkpoint("vision_client", {"path": graph.path})
    if result.get("repair_performed"):
        T("vision.client_corrected_only_fixture_wire", output.inputs == [stage] and stage.inputs == [source]
          and gain.inputs == [control] and target.inputs == [hidden])
