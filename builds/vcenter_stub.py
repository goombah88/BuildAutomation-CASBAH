"""Placeholder vCenter data so the build form renders end-to-end locally without a live
vCenter connection. Replace with real pyvmomi-backed queries (see ai_prompt_revised.txt,
"Net-new work for CASBAH") once vCenter access is wired up."""

from __future__ import annotations

STUB_CLUSTERS = {
    "rcd09": ["rcd09-infrastructure", "rcd09-general"],
    "sjc17": ["sjc17-infrastructure", "sjc17-general"],
}

STUB_RESOURCE_POOLS = {
    "rcd09": ["backup", "utilities", "vm_staging"],
    "sjc17": ["backup", "utilities"],
}

STUB_FOLDERS = {
    "rcd09": ["infrastructure", "staging"],
    "sjc17": ["infrastructure"],
}

STUB_PORTGROUPS = {
    "rcd09": [
        "/rcd09/network/rcd09-infra-vm-sw/rcd09-infra-servers",
        "/rcd09/network/rcd09-infra-vm-sw/rcd09-infra-vm-staging",
    ],
    "sjc17": ["/sjc17/network/sjc17-infra-vm-sw/sjc17-infra-servers"],
}

STUB_DATASTORES = {
    "rcd09": ["infra-01", "infra-02", "internal-01", "external-01"],
    "sjc17": ["infra-01", "infra-02"],
}

STUB_TEMPLATE = {
    "rcd09": "rcd09-c1-vmt-ubuntu2404-260721-1206",
    "sjc17": "sjc17-c1-vmt-ubuntu2404-260505-1037",
}


def get_vcenter_options(site: str) -> dict:
    """Return the dropdown options + template guess for a given site (stubbed for now)."""
    return {
        "clusters": STUB_CLUSTERS.get(site, []),
        "resource_pools": STUB_RESOURCE_POOLS.get(site, []),
        "folders": STUB_FOLDERS.get(site, []),
        "portgroups": STUB_PORTGROUPS.get(site, []),
        "datastores": STUB_DATASTORES.get(site, []),
        "template": STUB_TEMPLATE.get(site, ""),
    }
