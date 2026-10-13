from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from builds.forms import DEFAULT_PORTGROUP, STAGING_DEFAULTS, ServerBuildForm

VCENTER_INVENTORY = {
    "datastores": ["internal-01", "infra-01"],
    "clusters": ["rcd09-infrastructure"],
    "resource_pools": ["utilities"],
    "folders": ["infrastructure"],
    "portgroups": ["/rcd09/network/rcd09-infra-vm-sw/rcd09-infra-servers"],
}
LATEST_TEMPLATE = "rcd09-c1-vmt-ubuntu2404-260721-1206"
TENANT_GROUPS = ["HTD", "Engineering"]
TENANT_OTHER_NAMES = ["Acme Corp"]


class ServerBuildFormTestCase(SimpleTestCase):
    def setUp(self):
        self.enterContext(
            patch("builds.forms.get_vcenter_inventory", return_value=VCENTER_INVENTORY)
        )
        self.enterContext(
            patch(
                "builds.forms.get_latest_ubuntu_template", return_value=LATEST_TEMPLATE
            )
        )
        self.enterContext(
            patch("builds.forms.get_all_tenant_group_names", return_value=TENANT_GROUPS)
        )
        self.enterContext(
            patch("builds.forms.get_all_tenant_names", return_value=TENANT_OTHER_NAMES)
        )
        self.mock_vm_name_exists = self.enterContext(
            patch("builds.forms.find_vm_url", return_value=None)
        )
        self.mock_build_repo_name_exists = self.enterContext(
            patch("builds.forms.find_build_repo_url", return_value=None)
        )
        self.mock_local_build_repo_exists = self.enterContext(
            patch("builds.forms.local_build_repo_path", return_value=None)
        )
        self.mock_ip_address_exists = self.enterContext(
            patch("builds.forms.ip_address_exists", return_value=False)
        )

    def valid_data(self, **overrides):
        data = {
            "vsphere_vm_name": "rcd09-d1-testvm01",
            "vsphere_vm_ipv4_address": "10.192.5.5",
            "vsphere_vm_ipv4_prefix_length": "24",
            "vsphere_vm_primary_vnic": "ens192",
            "vsphere_vm_num_cpus": "2",
            "vsphere_vm_memory": "8",
            "tenant_group": "HTD",
            "tenant": "VMCloud Prod",
            "tenant_other": "",
            "vsphere_datastore": "infra-01",
            "vsphere_compute_cluster": "rcd09-infrastructure",
            "vsphere_resource_pool": "utilities",
            "vsphere_vm_folder": "infrastructure",
            "vsphere_portgroup_name": "/rcd09/network/rcd09-infra-vm-sw/rcd09-infra-servers",
            "vsphere_vm_template": LATEST_TEMPLATE,
            "firewall_ticket_requested": "yes",
            "firewall_ticket_reason": "standard build",
        }
        data.update(overrides)
        return data


class DeferredLookupsTests(ServerBuildFormTestCase):
    def test_placeholder_choices_used_and_no_external_calls_made(self):
        with patch("builds.forms.get_vcenter_inventory") as mock_inventory:
            form = ServerBuildForm(site="rcd09", defer_lookups=True)
            mock_inventory.assert_not_called()

        self.assertEqual(form.fields["tenant_group"].choices, [("HTD", "HTD")])
        self.assertEqual(form.fields["tenant_other"].choices, [("", "---------")])
        self.assertEqual(
            form.fields["vsphere_datastore"].choices, [("", "Loading\u2026")]
        )


class RealLookupsTests(ServerBuildFormTestCase):
    def test_choices_populated_from_vcenter_and_netbox(self):
        form = ServerBuildForm(site="rcd09")

        self.assertEqual(
            [c[0] for c in form.fields["vsphere_datastore"].choices],
            ["infra-01", "internal-01"],
        )
        self.assertEqual(
            form.fields["vsphere_compute_cluster"].choices,
            [("rcd09-infrastructure", "rcd09-infrastructure")],
        )
        self.assertEqual(
            form.fields["tenant_group"].choices,
            [("Engineering", "Engineering"), ("HTD", "HTD")],
        )
        self.assertEqual(
            form.fields["tenant_other"].choices,
            [("", "---------"), ("Acme Corp", "Acme Corp")],
        )

    def test_template_and_default_portgroup_initial_set_for_unbound_form(self):
        form = ServerBuildForm(site="rcd09")
        self.assertEqual(form.fields["vsphere_vm_template"].initial, LATEST_TEMPLATE)
        self.assertEqual(
            form.fields["vsphere_portgroup_name"].initial, DEFAULT_PORTGROUP["rcd09"]
        )

    def test_staging_defaults_override_normal_defaults(self):
        form = ServerBuildForm(site="rcd09", staging=True)
        for field_name, value in STAGING_DEFAULTS.items():
            self.assertEqual(form.fields[field_name].initial, value)

    def test_staging_restricts_datastore_to_infra_dash_prefix_only(self):
        inventory = dict(
            VCENTER_INVENTORY,
            datastores=["infra-01", "infrastructure-02", "internal-01"],
        )
        with patch("builds.forms.get_vcenter_inventory", return_value=inventory):
            form = ServerBuildForm(site="rcd09", staging=True)
        self.assertEqual(
            [c[0] for c in form.fields["vsphere_datastore"].choices], ["infra-01"]
        )

    def test_non_staging_keeps_broader_datastore_prefixes(self):
        inventory = dict(
            VCENTER_INVENTORY,
            datastores=["infra-01", "infrastructure-02", "internal-01"],
        )
        with patch("builds.forms.get_vcenter_inventory", return_value=inventory):
            form = ServerBuildForm(site="rcd09")
        self.assertEqual(
            [c[0] for c in form.fields["vsphere_datastore"].choices],
            ["infra-01", "infrastructure-02", "internal-01"],
        )

    def test_staging_restricts_resource_pool_to_single_choice(self):
        form = ServerBuildForm(site="rcd09", staging=True)
        self.assertEqual(
            form.fields["vsphere_resource_pool"].choices,
            [("vm_staging", "vm_staging")],
        )

    def test_non_staging_resource_pool_keeps_full_choice_list(self):
        form = ServerBuildForm(site="rcd09")
        self.assertEqual(
            [c[0] for c in form.fields["vsphere_resource_pool"].choices],
            ["", "utilities"],
        )

    def test_bound_form_does_not_set_initial_overrides(self):
        form = ServerBuildForm(self.valid_data(), site="rcd09")
        self.assertIsNone(form.fields["vsphere_vm_template"].initial)

    def test_hx_attrs_point_at_site_specific_validation_urls(self):
        form = ServerBuildForm(site="rcd09")
        self.assertEqual(
            form.fields["vsphere_vm_name"].widget.attrs["hx-get"],
            reverse("validate_vm_name", kwargs={"site": "rcd09"}),
        )
        self.assertEqual(
            form.fields["vsphere_vm_ipv4_address"].widget.attrs["hx-get"],
            reverse("validate_ip", kwargs={"site": "rcd09"}),
        )


class ValidSubmissionTests(ServerBuildFormTestCase):
    def test_valid_data_passes(self):
        form = ServerBuildForm(self.valid_data(), site="rcd09")
        self.assertTrue(form.is_valid(), form.errors)

    def test_name_is_lowercased_and_stripped(self):
        form = ServerBuildForm(
            self.valid_data(vsphere_vm_name="  RCD09-D1-TESTVM01  "), site="rcd09"
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["vsphere_vm_name"], "rcd09-d1-testvm01")


class VmNameValidationTests(ServerBuildFormTestCase):
    def test_rejects_name_not_matching_site_prefix(self):
        form = ServerBuildForm(
            self.valid_data(vsphere_vm_name="sjc17-d1-testvm01"), site="rcd09"
        )
        self.assertFalse(form.is_valid())
        self.assertIn("vsphere_vm_name", form.errors)

    def test_rejects_when_netbox_reports_name_taken(self):
        self.mock_vm_name_exists.return_value = "https://netbox.example/virtualization/virtual-machines/?name=rcd09-d1-testvm01"
        form = ServerBuildForm(self.valid_data(), site="rcd09")
        self.assertFalse(form.is_valid())
        self.assertIn("already exists", form.errors["vsphere_vm_name"][0])

    def test_rejects_when_gitlab_reports_repo_taken(self):
        self.mock_build_repo_name_exists.return_value = (
            "https://gitlab.example/vms/rcd09-d1-testvm01"
        )
        form = ServerBuildForm(self.valid_data(), site="rcd09")
        self.assertFalse(form.is_valid())
        self.assertIn("GitLab build repo", form.errors["vsphere_vm_name"][0])

    def test_rejects_when_local_build_copy_already_exists(self):
        self.mock_local_build_repo_exists.return_value = "/builds/rcd09-d1-testvm01"
        form = ServerBuildForm(self.valid_data(), site="rcd09")
        self.assertFalse(form.is_valid())
        self.assertIn("local build copy", form.errors["vsphere_vm_name"][0])

    def test_unknown_availability_does_not_block_submission(self):
        self.mock_vm_name_exists.return_value = None
        self.mock_build_repo_name_exists.return_value = None
        form = ServerBuildForm(self.valid_data(), site="rcd09")
        self.assertTrue(form.is_valid(), form.errors)

    def test_site_specific_pattern_for_sjc17(self):
        form = ServerBuildForm(
            self.valid_data(vsphere_vm_name="sjc17-d1-testvm01"), site="sjc17"
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_rejects_path_traversal_suffix(self):
        # VM_NAME_RE must fully anchor (not just check the prefix) -- vm_name
        # becomes a literal filename later (gitlab_client.create_build_repo), so a
        # trailing "../../etc/..." must be rejected, not silently accepted.
        form = ServerBuildForm(
            self.valid_data(vsphere_vm_name="rcd09-d1-../../../../etc/cron.d/evil"),
            site="rcd09",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("vsphere_vm_name", form.errors)

    def test_rejects_embedded_whitespace_or_special_characters(self):
        form = ServerBuildForm(
            self.valid_data(vsphere_vm_name="rcd09-d1-test vm; rm -rf"),
            site="rcd09",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("vsphere_vm_name", form.errors)


class IpAddressValidationTests(ServerBuildFormTestCase):
    def test_rejects_when_netbox_reports_ip_in_use(self):
        self.mock_ip_address_exists.return_value = True
        form = ServerBuildForm(self.valid_data(), site="rcd09")
        self.assertFalse(form.is_valid())
        self.assertIn("already in use", form.errors["vsphere_vm_ipv4_address"][0])

    def test_unknown_availability_does_not_block_submission(self):
        self.mock_ip_address_exists.return_value = None
        form = ServerBuildForm(self.valid_data(), site="rcd09")
        self.assertTrue(form.is_valid(), form.errors)


class CleanMethodTests(ServerBuildFormTestCase):
    def test_tenant_other_overrides_primary_tenant_radio(self):
        form = ServerBuildForm(
            self.valid_data(tenant="VMCloud Prod", tenant_other="Acme Corp"),
            site="rcd09",
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["tenant"], "Acme Corp")

    def test_primary_tenant_used_when_other_not_selected(self):
        form = ServerBuildForm(self.valid_data(tenant="VMCloud Prod"), site="rcd09")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["tenant"], "VMCloud Prod")

    def test_firewall_ticket_yes_requires_reason(self):
        form = ServerBuildForm(
            self.valid_data(firewall_ticket_requested="yes", firewall_ticket_reason=""),
            site="rcd09",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("firewall_ticket_reason", form.errors)

    def test_firewall_ticket_no_does_not_require_reason(self):
        form = ServerBuildForm(
            self.valid_data(firewall_ticket_requested="no", firewall_ticket_reason=""),
            site="rcd09",
        )
        self.assertTrue(form.is_valid(), form.errors)
