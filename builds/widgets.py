"""Custom form widgets for builds/forms.py."""

from __future__ import annotations

from django import forms


class ComboboxWidget(forms.TextInput):
    """A single <input list=...> + <datalist> — the browser shows live-filtered
    suggestions while typing (no custom JS, no click-vs-blur race to get wrong), and it's
    still one focusable control rather than a text box paired with a separate <select>.
    Requires choices where value == label (see builds/forms.py ServerBuildForm), since the
    field's submitted value is whatever text ends up in the input."""

    template_name = "builds/widgets/combobox_widget.html"

    def __init__(self, attrs=None, choices=()):
        default_attrs = {"autocomplete": "off"}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)
        self.choices = choices

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        widget_id = context["widget"]["attrs"].get("id", name)
        context["widget"]["datalist_id"] = f"{widget_id}_datalist"
        # Blank placeholder choices (e.g. ("", "---------")) don't make sense as a typed
        # suggestion — an empty text box already means "nothing selected".
        context["widget"]["datalist_options"] = [
            (v, l) for v, l in self.choices if v != ""
        ]
        return context
