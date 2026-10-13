import yaml
from django.test import SimpleTestCase

from builds.yaml_builder import build_yaml_preview


class BuildYamlPreviewTests(SimpleTestCase):
    def test_renders_expected_fields_for_rcd09(self):
        post_data = {
            "vsphere_datastore": "infra-01",
            "vsphere_compute_cluster": "rcd09-infrastructure",
            "vsphere_resource_pool": "utilities",
            "vsphere_vm_folder": "infrastructure",
            "vsphere_portgroup_name": "/rcd09/network/rcd09-infra-vm-sw/rcd09-infra-servers",
            "vsphere_vm_primary_vnic": "ens192",
            "vsphere_vm_ipv4_address": "10.192.5.5",
            "vsphere_vm_ipv4_prefix_length": "24",
            "vsphere_vm_template": "rcd09-c1-vmt-ubuntu2404-260721-1206",
            "vsphere_vm_name": "RCD09-D1-TESTVM01",
            "vsphere_vm_num_cpus": "2",
            "vsphere_vm_memory": "8",
        }
        data = yaml.safe_load(build_yaml_preview(post_data, "rcd09"))

        self.assertEqual(data["vsphere_server"], "rcd09-c1-vm-vcenter-01.cll.cloud")
        self.assertEqual(data["vsphere_datacenter"], "rcd09")
        self.assertEqual(
            data["vsphere_vm_dns_server_list"], ["10.192.1.111", "10.192.1.112"]
        )
        # Name is normalized the same way forms.clean_vsphere_vm_name normalizes it.
        self.assertEqual(data["vsphere_vm_name"], "rcd09-d1-testvm01")
        # GB in, MB out.
        self.assertEqual(data["vsphere_vm_memory"], 8192)

    def test_renders_expected_fields_for_sjc17(self):
        data = yaml.safe_load(build_yaml_preview({}, "sjc17"))
        self.assertEqual(data["vsphere_server"], "sjc17-c1-vm-vcenter-01.cll.cloud")
        self.assertEqual(
            data["vsphere_vm_dns_server_list"], ["10.196.1.111", "10.196.1.112"]
        )

    def test_missing_fields_default_to_empty_strings(self):
        data = yaml.safe_load(build_yaml_preview({}, "rcd09"))
        self.assertEqual(data["vsphere_datastore"], "")
        self.assertEqual(data["vsphere_vm_name"], "")

    def test_non_numeric_memory_does_not_raise(self):
        data = yaml.safe_load(
            build_yaml_preview({"vsphere_vm_memory": "not-a-number"}, "rcd09")
        )
        self.assertEqual(data["vsphere_vm_memory"], "")

    def test_unknown_site_yields_empty_server_and_dns(self):
        data = yaml.safe_load(build_yaml_preview({}, "made-up-site"))
        self.assertEqual(data["vsphere_server"], "")
        self.assertEqual(data["vsphere_vm_dns_server_list"], [])
