"""Selected local operator guidance verified on Windows with TouchDesigner 2025.32440."""

VERIFIED_BUILD = "2025.32440"

# Names, labels and styles come from operator instances in the disposable metadata probe.
# The explanations cover the supported procedural fixture rather than a reference for every parameter.
_GUIDES = {
    "constantTOP": (
        "Generate a solid texture. Use this as a known input when verifying a control's effect.",
        (
            ("colorr", "Color", "RGBA", "Red channel of the solid color."),
            ("colorg", "Color", "RGBA", "Green channel of the solid color."),
            ("colorb", "Color", "RGBA", "Blue channel of the solid color."),
            ("alpha", "Alpha", "Float", "Opacity channel of the solid color."),
        ),
    ),
    "noiseTOP": (
        "Generate a texture from noise. Translate the noise coordinates to animate the pattern.",
        (
            ("amp", "Amplitude", "Float", "Scale the noise variation."),
            ("period", "Period", "Float", "Adjust the spatial scale of the noise."),
            ("seed", "Seed", "Float", "Choose a repeatable noise pattern."),
            ("tx", "Translate", "XYZW", "Translate the sampling coordinates along X."),
            ("ty", "Translate", "XYZW", "Translate the sampling coordinates along Y."),
        ),
    ),
    "levelTOP": (
        "Adjust an existing texture. Use a connected TOP and verify the cooked result after changing a control.",
        (
            ("opacity", "Opacity", "Float", "Adjust the output's opacity."),
            ("contrast", "Contrast", "Float", "Adjust the separation between light and dark values."),
            ("blacklevel", "Black Level", "Float", "Adjust the level treated as black."),
        ),
    ),
    "outTOP": (
        "Expose a texture output from a Base COMP. Verify the native connector and let the owner attach the external consumer.",
        (),
    ),
    "noiseCHOP": (
        "Generate a named noise channel to drive a control.",
        (
            ("channelname", "Channel Names", "Str", "Set the generated channel name before referencing the channel."),
            ("amp", "Amplitude", "Float", "Scale the channel's noise variation."),
            ("period", "Period", "Float", "Adjust the scale of the channel's noise over time or samples."),
            ("offset", "Offset", "Float", "Shift the generated noise values."),
            ("rate", "Sample Rate", "Float", "Set the sample rate of the generated channel."),
        ),
    ),
    "mathCHOP": (
        "Scale or remap an incoming channel. Verify the resulting range before using it as a control.",
        (
            ("gain", "Multiply", "Float", "Multiply the channel values."),
            ("fromrange1", "From Range", "Float", "Lower end of the source range."),
            ("fromrange2", "From Range", "Float", "Upper end of the source range."),
            ("torange1", "To Range", "Float", "Lower end of the mapped range."),
            ("torange2", "To Range", "Float", "Upper end of the mapped range."),
        ),
    ),
    "parameterexecuteDAT": (
        "React to parameter events with an approved callback. Verify the selected operator, parameters and trigger before activation.",
        (
            ("op", "OPs", "OP", "Select the watched operators through the approved reference path."),
            ("pars", "Parameters", "Str", "Select the watched parameter names or patterns."),
            ("valuechange", "Value Change", "Toggle", "Enable the value-change trigger."),
            ("onpulse", "On Pulse", "Toggle", "Enable the pulse trigger."),
            ("active", "Active", "Toggle", "Activate the staged callback through separate user approval."),
        ),
    ),
    "nullTOP": ("Name a stable texture output at the end of a processing chain.", ()),
    "rampTOP": (
        "Generate a color gradient for shading, masks or lookup textures.",
        (
            ("type", "Type", "Menu", "Choose the gradient shape."),
            ("phase", "Phase", "Float", "Shift the gradient phase."),
            ("period", "Period", "Float", "Set the gradient repeat period."),
        ),
    ),
    "blurTOP": (
        "Soften an image or mask with a spatial filter.",
        (
            ("type", "Type", "Menu", "Select a blur filter."),
            ("size", "Filter Size", "Int", "Set the blur filter size."),
        ),
    ),
    "transformTOP": (
        "Move or scale a texture in image space.",
        (
            ("tx", "Translate", "XYZW", "Translate the image horizontally."),
            ("ty", "Translate", "XYZW", "Translate the image vertically."),
            ("sx", "Scale", "XYZW", "Set horizontal scale."),
            ("sy", "Scale", "XYZW", "Set vertical scale."),
        ),
    ),
    "compositeTOP": (
        "Combine multiple wired textures with a compositing operation.",
        (
            ("operand", "Operation", "Menu", "Select the blending operation."),
            ("swaporder", "Swap Operation Order", "Toggle", "Change the order for directional operations."),
        ),
    ),
    "switchTOP": (
        "Choose a texture from the wired inputs.",
        (
            ("index", "Index", "Float", "Select the source by input index. Verify the input order first."),
        ),
    ),
    "resolutionTOP": (
        "Resize a texture to the resolution needed downstream.",
        (
            ("outputresolution", "Output Resolution", "Menu", "Choose how the output dimensions are determined."),
            ("resolutionw", "Resolution", "Int", "Set a custom width."),
            ("resolutionh", "Resolution", "Int", "Set a custom height."),
        ),
    ),
    "thresholdTOP": (
        "Turn contrast or brightness into a threshold mask.",
        (
            ("comparator", "Comparator", "Menu", "Select how samples are compared."),
            ("threshold", "Threshold", "Float", "Set the threshold level."),
            ("soften", "Soften", "Float", "Soften the transition around the threshold."),
        ),
    ),
    "constantCHOP": (
        "Create named control channels with constant sample values.",
        (
            ("const0name", "Name", "Str", "Name the first constant channel."),
            ("const0value", "", "Float", "Set its sample value."),
        ),
    ),
    "lfoCHOP": (
        "Generate a periodic control signal or oscillator.",
        (
            ("wavetype", "Type", "Menu", "Select the waveform."),
            ("frequency", "Frequency", "Float", "Set the oscillation frequency."),
            ("amp", "Amplitude", "Float", "Set the amplitude."),
            ("bias", "Bias", "Float", "Offset the output values."),
        ),
    ),
    "filterCHOP": (
        "Smooth or filter incoming control channels.",
        (
            ("type", "Type", "Menu", "Choose the filter type."),
            ("width", "Filter Width", "Float", "Set the filter window width."),
            ("widthunit", "Filter Width Unit", "Menu", "Choose the width units."),
        ),
    ),
    "lagCHOP": (
        "Smooth changes in control channels over time.",
        (
            ("lagmethod", "Method", "Menu", "Select the lag method."),
            ("lag1", "Lag", "Float", "Set the first lag amount."),
            ("lagunit", "Lag Unit", "Menu", "Choose the lag units."),
        ),
    ),
    "mergeCHOP": (
        "Combine channels from multiple wired inputs.",
        (
            ("align", "Align", "Menu", "Choose how to align the sample ranges."),
            ("duplicate", "Duplicate Names", "Menu", "Choose how to handle duplicate channel names."),
        ),
    ),
    "nullCHOP": (
        "Name a stable channel output for downstream controls.",
        (
            ("cooktype", "Cook Type", "Menu", "Choose the Null CHOP cook behavior."),
        ),
    ),
    "boxSOP": (
        "Generate box geometry for a mesh or a template.",
        (
            ("sizex", "Size", "XYZW", "Set width on the X axis."),
            ("sizey", "Size", "XYZW", "Set height on the Y axis."),
            ("sizez", "Size", "XYZW", "Set depth on the Z axis."),
        ),
    ),
    "sphereSOP": (
        "Generate spherical geometry.",
        (
            ("type", "Primitive Type", "Menu", "Choose the primitive type."),
            ("radx", "Radius", "XYZW", "Set the radius on X."),
            ("rady", "Radius", "XYZW", "Set the radius on Y."),
            ("radz", "Radius", "XYZW", "Set the radius on Z."),
        ),
    ),
    "gridSOP": (
        "Generate a regular grid of geometry points or polygons.",
        (
            ("orient", "Orientation", "Menu", "Choose the plane of the grid."),
            ("rows", "Rows", "Int", "Set the row count."),
            ("cols", "Columns", "Int", "Set the column count."),
        ),
    ),
    "transformSOP": (
        "Translate, rotate or scale geometry before it reaches a Geometry COMP.",
        (
            ("tx", "Translate", "XYZW", "Translate the points on X."),
            ("ry", "Rotate", "XYZW", "Rotate the points about Y."),
            ("scale", "Uniform Scale", "Float", "Scale the geometry uniformly."),
        ),
    ),
    "nullSOP": ("Name a stable geometry output at the end of a SOP chain.", ()),
    "tableDAT": (
        "Store tabular data for lookup or configuration.",
        (
            ("rows", "Rows", "Int", "Set the number of rows."),
            ("cols", "Columns", "Int", "Set the number of columns."),
        ),
    ),
    "textDAT": ("Hold authored text. Verify its classification and permissions before changing source.", ()),
    "nullDAT": ("Name a stable tabular or text data output.", ()),
    "baseCOMP": ("Organize a reusable operator network and its controls.", ()),
    "containerCOMP": (
        "Group a panel interface and configure its local dimensions.",
        (
            ("w", "Width", "Int", "Set the panel width."),
            ("h", "Height", "Int", "Set the panel height."),
            ("display", "Display", "Toggle", "Control whether the panel is displayed."),
        ),
    ),
    "geometryCOMP": (
        "Place renderable geometry in a scene and apply object transforms.",
        (
            ("tx", "Translate", "XYZW", "Translate the scene object on X."),
            ("scale", "Uniform Scale", "Float", "Scale the object uniformly."),
            ("render", "Render", "Toggle", "Control whether the object participates in rendering."),
        ),
    ),
    "cameraCOMP": (
        "Frame a scene and choose how it is projected.",
        (
            ("projection", "Projection", "Menu", "Select the camera projection."),
            ("fov", "FOV Angle", "Float", "Set the field of view where applicable."),
            ("near", "Near", "Float", "Set the near clipping plane."),
            ("far", "Far", "Float", "Set the far clipping plane."),
        ),
    ),
}

GUIDED_TYPES = frozenset(_GUIDES)


def search_text(name):
    purpose, parameters = _GUIDES.get(name, ("", ()))
    return " ".join([purpose, *(f"{par} {label} {note}" for par, label, _, note in parameters)])


def guidance_for(name):
    """Return an independent plain-data guide for a covered operator type."""
    entry = _GUIDES.get(name)
    if entry is None:
        return None
    purpose, parameters = entry
    return {"use": purpose, "parameters": [
        {"name": par, "label": label, "style": style, "use": note}
        for par, label, style, note in parameters
    ]}
