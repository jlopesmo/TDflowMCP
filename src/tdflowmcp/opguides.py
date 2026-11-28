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
