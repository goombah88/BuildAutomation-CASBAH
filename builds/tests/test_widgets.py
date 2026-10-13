from django.test import SimpleTestCase

from builds.widgets import ComboboxWidget


class ComboboxWidgetTests(SimpleTestCase):
    def test_defaults_autocomplete_off(self):
        widget = ComboboxWidget()
        self.assertEqual(widget.attrs["autocomplete"], "off")

    def test_custom_attrs_override_default_but_keep_autocomplete(self):
        widget = ComboboxWidget(attrs={"placeholder": "Type to search…"})
        self.assertEqual(widget.attrs["placeholder"], "Type to search…")
        self.assertEqual(widget.attrs["autocomplete"], "off")

    def test_get_context_builds_datalist_id_and_excludes_blank_choices(self):
        widget = ComboboxWidget(
            choices=[("", "---------"), ("HTD", "HTD"), ("Eng", "Eng")]
        )
        context = widget.get_context("tenant_group", "HTD", {"id": "id_tenant_group"})
        self.assertEqual(context["widget"]["datalist_id"], "id_tenant_group_datalist")
        self.assertEqual(
            context["widget"]["datalist_options"], [("HTD", "HTD"), ("Eng", "Eng")]
        )

    def test_get_context_falls_back_to_name_when_no_id_attr(self):
        widget = ComboboxWidget(choices=[("A", "A")])
        context = widget.get_context("portgroup", "A", {})
        self.assertEqual(context["widget"]["datalist_id"], "portgroup_datalist")
