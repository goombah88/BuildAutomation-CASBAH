"""NetBox tenant lookup, for the Tenant "Other" dropdown, plus the VM/interface/IP
creation pipeline (ai_prompt_revised.txt section 5). Token comes from Vault
(global/service-accounts/service_netbox). Falls back to an empty list if Vault is
unreachable or NetBox itself is unreachable.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache

from .vault_client import get_netbox_api_token

logger = logging.getLogger(__name__)

# Short-lived cache so hover-prefetch (landing.html) and the lazy-loaded build form
# (builds/views.py build_form_options) don't both hit NetBox on every page view.
NETBOX_CACHE_TTL = 300

# Fixed values for every CASBAH-created VM (confirmed against the live NetBox schema,
# not user-editable — see ai_prompt_revised.txt "NetBox field UX note").
VM_ROLE_SLUG = "general-infrastructure-vm"
VM_PLATFORM_SLUG = "ubuntu-24-04"
VM_DISK_MB = 65536
VM_INTERFACE_NAME = "ens192"
IP_VRF_NAME = "cll-global"


class NetBoxBuildError(Exception):
    """Raised when the automated NetBox VM/interface/IP creation can't complete a step."""


def _client():
    """Returns an authenticated pynetbox API client, or None if NetBox/Vault isn't
    configured/reachable."""
    token = get_netbox_api_token()
    if not token or not settings.NETBOX_URL:
        return None

    import urllib3
    import pynetbox

    nb = pynetbox.api(settings.NETBOX_URL, token=token)
    # Internal cll.cloud hosts use self-signed certs (same reason vcenter_client.py
    # skips cert verification for vCenter) — trust the connection rather than
    # requiring a locally-installed internal CA cert just to run this in dev.
    nb.http_session.verify = False
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return nb


# The 3 primary tenants are static radio buttons (see builds/forms.py PRIMARY_TENANT_CHOICES)
# and don't need a live lookup; this module only feeds the "Other" catch-all dropdown.


def get_all_tenant_names() -> list[str]:
    cache_key = "netbox_tenant_names"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    nb = _client()
    if nb is None:
        names = []
    else:
        try:
            names = sorted({t.name for t in nb.tenancy.tenants.all()})
        except Exception:
            logger.warning(
                "NetBox tenant lookup failed; 'Other' dropdown will be empty.",
                exc_info=True,
            )
            names = []

    cache.set(cache_key, names, NETBOX_CACHE_TTL)
    return names


def get_all_tenant_group_names() -> list[str]:
    """Tenant Group dropdown options. "HTD" is always included even if NetBox is
    unreachable, so the field's required default always remains selectable."""
    cache_key = "netbox_tenant_group_names"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    cache_key = "netbox_tenant_group_names"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    nb = _client()
    if nb is None:
        groups = ["HTD"]
    else:
        try:
            names = {g.name for g in nb.tenancy.tenant_groups.all()}
            names.add("HTD")
            groups = sorted(names)
        except Exception:
            logger.warning(
                "NetBox tenant group lookup failed; falling back to ['HTD'].",
                exc_info=True,
            )
            groups = ["HTD"]

    cache.set(cache_key, groups, NETBOX_CACHE_TTL)
    return groups


def vm_name_exists(vm_name: str) -> bool | None:
    """True/False if NetBox could answer definitively, None if NetBox/Vault is
    unreachable — callers must treat None as "couldn't verify", not "name is free"."""
    nb = _client()
    if nb is None:
        return None
    try:
        return bool(list(nb.virtualization.virtual_machines.filter(name=vm_name)))
    except Exception:
        logger.warning(
            "NetBox VM-name uniqueness check failed for '%s'.", vm_name, exc_info=True
        )
        return None


def find_vm_url(vm_name: str) -> str | None:
    """Direct link to the existing NetBox VM's own detail page, if a matching VM
    already exists — None if it doesn't exist or NetBox/Vault is unreachable (same
    can't-verify-so-don't-block fallback as vm_name_exists)."""
    nb = _client()
    if nb is None:
        return None
    try:
        vm = next(iter(nb.virtualization.virtual_machines.filter(name=vm_name)), None)
    except Exception:
        logger.warning("NetBox VM lookup failed for '%s'.", vm_name, exc_info=True)
        return None
    if vm is None:
        return None
    return f"{settings.NETBOX_URL}/virtualization/virtual-machines/{vm.id}/"


def ip_address_exists(ipv4_address: str) -> bool | None:
    """True/False if NetBox could answer definitively, None if NetBox/Vault is
    unreachable — callers must treat None as "couldn't verify", not "address is free".
    Scoped to IP_VRF_NAME since that's the only VRF CASBAH-created IPs ever land in
    (see create_build_vm) — an address could exist elsewhere in a different VRF without
    conflicting with a new build."""
    nb = _client()
    if nb is None:
        return None
    try:
        vrf_id = _resolve_vrf_id(nb)
        return bool(
            list(nb.ipam.ip_addresses.filter(address=ipv4_address, vrf_id=vrf_id))
        )
    except Exception:
        logger.warning(
            "NetBox IP-address availability check failed for '%s'.",
            ipv4_address,
            exc_info=True,
        )
        return None


def _resolve_tenant(nb, tenant_group_name: str, tenant_name: str):
    """Resolves the exact Tenant record for (tenant_group_name, tenant_name) — tenant
    names aren't globally unique across groups in this NetBox (confirmed: two separate
    'VMCloud Dev' tenants exist, in different groups), so both must be given or the
    wrong tenant could get picked."""
    group = nb.tenancy.tenant_groups.get(name=tenant_group_name)
    if group is None:
        raise NetBoxBuildError(f"NetBox tenant group '{tenant_group_name}' not found.")

    matches = list(nb.tenancy.tenants.filter(name=tenant_name, group_id=group.id))
    if not matches:
        raise NetBoxBuildError(
            f"No NetBox tenant named '{tenant_name}' in group '{tenant_group_name}'."
        )
    if len(matches) > 1:
        raise NetBoxBuildError(
            f"Multiple NetBox tenants named '{tenant_name}' in group "
            f"'{tenant_group_name}' — can't disambiguate."
        )
    return matches[0]


def _resolve_cluster(nb, cluster_name: str, site: str):
    matches = list(nb.virtualization.clusters.filter(name=cluster_name))
    if not matches:
        raise NetBoxBuildError(
            f"NetBox cluster '{cluster_name}' not found (site '{site}')."
        )
    if len(matches) > 1:
        raise NetBoxBuildError(
            f"Multiple NetBox clusters named '{cluster_name}' — can't disambiguate."
        )
    return matches[0]


def _resolve_role_id(nb) -> int:
    role = nb.dcim.device_roles.get(slug=VM_ROLE_SLUG)
    if role is None:
        raise NetBoxBuildError(f"NetBox role '{VM_ROLE_SLUG}' not found.")
    return role.id


def _resolve_platform_id(nb) -> int:
    platform = nb.dcim.platforms.get(slug=VM_PLATFORM_SLUG)
    if platform is None:
        raise NetBoxBuildError(f"NetBox platform '{VM_PLATFORM_SLUG}' not found.")
    return platform.id


def _resolve_vrf_id(nb) -> int:
    vrf = nb.ipam.vrfs.get(name=IP_VRF_NAME)
    if vrf is None:
        raise NetBoxBuildError(f"NetBox VRF '{IP_VRF_NAME}' not found.")
    return vrf.id


def create_build_vm(
    *,
    vm_name: str,
    site: str,
    cluster_name: str,
    tenant_group: str,
    tenant: str,
    num_cpus: int,
    memory_gb: int,
    ipv4_address: str,
    ipv4_prefix_length: int,
) -> dict:
    """Creates the NetBox VM + interface + primary IP for a build (ai_prompt_revised.txt
    section 5: Role/Platform/Disk/interface-name/VRF are fixed values, not user input).
    Raises NetBoxBuildError on any failure — callers must surface build_request.error_detail
    and never attempt to silently roll back whatever step already succeeded (same policy
    as builds/gitlab_client.py)."""
    nb = _client()
    if nb is None:
        raise NetBoxBuildError("No NetBox API token available (Vault unreachable?).")

    try:
        site_obj = nb.dcim.sites.get(slug=site)
        if site_obj is None:
            raise NetBoxBuildError(f"NetBox site '{site}' not found.")
        cluster = _resolve_cluster(nb, cluster_name, site)
        tenant_obj = _resolve_tenant(nb, tenant_group, tenant)
        role_id = _resolve_role_id(nb)
        platform_id = _resolve_platform_id(nb)
        vrf_id = _resolve_vrf_id(nb)
    except NetBoxBuildError:
        raise
    except Exception as exc:
        raise NetBoxBuildError(
            f"Failed to resolve NetBox reference data for '{vm_name}' "
            f"(nothing created yet): {exc}"
        ) from exc

    try:
        vm = nb.virtualization.virtual_machines.create(
            name=vm_name,
            site=site_obj.id,
            cluster=cluster.id,
            role=role_id,
            tenant=tenant_obj.id,
            platform=platform_id,
            vcpus=num_cpus,
            memory=memory_gb * 1024,
            disk=VM_DISK_MB,
        )
    except Exception as exc:
        raise NetBoxBuildError(
            f"Failed to create NetBox VM '{vm_name}': {exc}"
        ) from exc

    try:
        interface = nb.virtualization.interfaces.create(
            virtual_machine=vm.id,
            name=VM_INTERFACE_NAME,
        )
    except Exception as exc:
        raise NetBoxBuildError(
            f"NetBox VM '{vm_name}' (id={vm.id}) was created, but adding interface "
            f"'{VM_INTERFACE_NAME}' failed: {exc}"
        ) from exc

    try:
        ip = nb.ipam.ip_addresses.create(
            address=f"{ipv4_address}/{ipv4_prefix_length}",
            vrf=vrf_id,
            tenant=tenant_obj.id,
            dns_name=vm_name,
            assigned_object_type="virtualization.vminterface",
            assigned_object_id=interface.id,
            status="active",
        )
    except Exception as exc:
        raise NetBoxBuildError(
            f"NetBox VM '{vm_name}' (id={vm.id}) and its interface were created, but "
            f"creating IP '{ipv4_address}/{ipv4_prefix_length}' failed: {exc}"
        ) from exc

    try:
        vm.primary_ip4 = ip.id
        vm.save()
    except Exception as exc:
        raise NetBoxBuildError(
            f"NetBox VM '{vm_name}' (id={vm.id}), interface, and IP {ip.address} were "
            f"created, but setting the primary IP failed: {exc}"
        ) from exc

    return {"vm_url": f"{settings.NETBOX_URL}/virtualization/virtual-machines/{vm.id}/"}
