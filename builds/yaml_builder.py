"""Builds a preview of the build yaml from raw form POST data — best-effort, informational
only. This does NOT run full form validation (that still happens exclusively in the real
build_form view) — it exists purely so the confirmation step can show the user what will
be generated before they commit, matching the shape of example_files/*-build-file-example.yaml.
"""

from __future__ import annotations

import yaml


def build_yaml_preview(post_data, site: str) -> str:
    def _get(key, default=""):
        return post_data.get(key, default) or default

    try:
        memory_mb = int(_get("vsphere_vm_memory", 0) or 0) * 1024
    except (TypeError, ValueError):
        memory_mb = ""

    vsphere_server = {
        "rcd09": "rcd09-c1-vm-vcenter-01.cll.cloud",
        "sjc17": "sjc17-c1-vm-vcenter-01.cll.cloud",
    }.get(site, "")
    dns_servers = {
        "rcd09": ["10.192.1.111", "10.192.1.112"],
        "sjc17": ["10.196.1.111", "10.196.1.112"],
    }.get(site, [])

    data = {
        "vsphere_server": vsphere_server,
        "vsphere_datacenter": site,
        "vsphere_datastore": _get("vsphere_datastore"),
        "vsphere_compute_cluster": _get("vsphere_compute_cluster"),
        "vsphere_resource_pool": _get("vsphere_resource_pool"),
        "vsphere_vm_folder": _get("vsphere_vm_folder"),
        "vsphere_portgroup_name": _get("vsphere_portgroup_name"),
        "vsphere_vm_primary_vnic": _get("vsphere_vm_primary_vnic"),
        "vsphere_vm_ipv4_address": _get("vsphere_vm_ipv4_address"),
        "vsphere_vm_ipv4_prefix_length": _get("vsphere_vm_ipv4_prefix_length"),
        "vsphere_vm_dns_server_list": dns_servers,
        "vsphere_vm_template": _get("vsphere_vm_template"),
        "vsphere_vm_name": _get("vsphere_vm_name").strip().lower(),
        "vsphere_vm_num_cpus": _get("vsphere_vm_num_cpus"),
        "vsphere_vm_memory": memory_mb,
    }
    return yaml.dump(data, sort_keys=False, default_flow_style=False)
