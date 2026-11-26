"""Disposable owner-side diagnostic fixtures and independent outcome checks."""
import td

import td_runner_gate as gate


def constant(parent, name, red):
    top = parent.create(td.constantTOP, name)
    top.par.colorr = red
    return top


def value(top):
    top.cook(force=True)
    return float(top.numpyArray()[0, 0, 0])


def counts(ops):
    return {op.name: op.totalCooks for op in ops}


def run(comp, ext, T, frames, until, checkpoint, R):
    fx = td.op("/project1").create(td.baseCOMP, "fx")
    broken = fx.create(td.baseCOMP, "broken")
    source = constant(broken, "source", .6)
    output = broken.create(td.levelTOP, "output")
    output.par.opacity.expr = "1 / 0"
    try:
        output.cook(force=True)
    except td.Error:
        pass

    cost = fx.create(td.baseCOMP, "cost")
    cheap = constant(cost, "cheap", .5)
    expensive = cost.create(td.scriptTOP, "expensive")
    expensive.viewer = False
    callback = next(child for child in expensive.docked if child.isDAT)
    callback.text = ("import time\n"
                     "import numpy\n"
                     "def onCook(scriptOp):\n"
                     "    time.sleep(.012)\n"
                     "    scriptOp.copyNumpyArray(numpy.ones((8, 8, 4), dtype=numpy.float32))\n"
                     "    scriptOp.store('calls', scriptOp.fetch('calls', 0) + 1)\n")
    cheap.cook(force=True)
    expensive.cook(force=True)

    visual = fx.create(td.baseCOMP, "visual")
    left = constant(visual, "left", .25)
    right = constant(visual, "right", .75)
    for top in (left, right):
        top.viewer = False
        top.cook(force=True)
        top.lock = True
    unlocked = constant(visual, "unlocked", .5)
    oversized = constant(visual, "oversized", .5)
    oversized.viewer = False
    oversized.par.outputresolution = "custom"
    oversized.par.resolutionw = 1024
    oversized.par.resolutionh = 512
    oversized.cook(force=True)
    oversized.lock = True
    outside = constant(td.op("/project1"), "outside_diagnostics", .5)
    bypassed = visual.create(td.baseCOMP, "bypassed")
    bypassed_top = constant(bypassed, "locked", .5)
    for top in (outside, bypassed_top):
        top.viewer = False
        top.cook(force=True)
        top.lock = True
    bypassed.bypass = True
    # Exercise a locked cached read over executable upstream work in this owner fixture.
    observed = cost.create(td.nullTOP, "observed")
    observed.inputConnectors[0].connect(expensive.outputConnectors[0])
    observed.viewer = False
    observed.cook(force=True)
    observed.lock = True
    calls = expensive.fetch("calls", 0)
    probe_before = counts((expensive, observed))
    pixels = observed.numpyArray(delayed=False)
    T("visual.owner_probe_has_cached_pixels", pixels is not None and len(pixels.shape) == 3)
    T("visual.owner_probe_preserves_cook_and_callback_counts",
      counts((expensive, observed)) == probe_before and expensive.fetch("calls", 0) == calls)

    restore = fx.create(td.baseCOMP, "restore")
    original = constant(restore, "original", .45)
    final = restore.create(td.levelTOP, "final")
    untouched = constant(restore, "untouched", .8)
    final.inputConnectors[0].connect(original.outputConnectors[0])
    expected = value(final)
    T("restore.initial_output", abs(expected - .45) < .02)
    final.inputConnectors[0].disconnect()
    final.cook(force=True)

    yield from gate.open_and_pair(comp, ext, T, frames, until, checkpoint)
    yield from gate.enable(comp, until, "diagnostic_workflow_enable")
    fields = (source, output, cheap, expensive, observed, left, right, unlocked, oversized, original, final, untouched)
    seen = counts(fields)
    calls = expensive.fetch("calls", 0)
    flags = yield checkpoint("diagnostic_inspection", {})
    T("inspection.cook_counts_unchanged", counts(fields) == seen, before=seen, after=counts(fields))
    T("inspection.callback_count_unchanged", expensive.fetch("calls", 0) == calls)
    if flags.get("candidate"):
        tdops = ext._m.tdops
        verified = tdops._top_reads_verified
        tdops._top_reads_verified = lambda: False
        try:
            yield checkpoint("diagnostic_unsupported_runtime", {})
        finally:
            tdops._top_reads_verified = verified
    before_live = counts(fields)
    calls = expensive.fetch("calls", 0)
    yield checkpoint("diagnostic_live", {})
    T("live_inspection.counters_unchanged", counts(fields) == before_live and expensive.fetch("calls", 0) == calls)
    yield checkpoint("diagnostic_repairs", {})
    T("repair.output_restored", bool(output.inputs) and output.inputs[0] == source and abs(value(output) - .6) < .02
      and not output.errors())
    T("restore.unrelated_operator_intact", abs(untouched.par.colorr.eval() - .8) < .001 and not untouched.inputs)
    T("restore.output_restored", bool(final.inputs) and final.inputs[0] == original and abs(value(final) - expected) < .02)
    yield checkpoint("diagnostic_verification", {})
    R["component_version"] = str(comp.par.Version.eval())
    comp.par.Enablemcp = False
    yield frames(5)
