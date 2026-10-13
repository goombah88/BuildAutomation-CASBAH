"""vCenter template lookup — ported from CASBAH-PrepareNewBuild's build_prep_common.py
(get_newest_ubuntu_template et al.), which is treated as the source of truth for this
logic (see ai_prompt_revised.txt SCOPE section). Falls back to builds.vcenter_stub's
static value if vCenter is unreachable or credentials aren't configured locally, so the
form still renders during local dev without VPN/vCenter access.
"""

from __future__ import annotations

import logging
import ssl

from django.core.cache import cache

from .vault_client import get_vcenter_credentials
from .vcenter_stub import STUB_TEMPLATE, get_vcenter_options as _stub_vcenter_options

logger = logging.getLogger(__name__)

# Short-lived cache so hover-prefetch (landing.html) and the lazy-loaded build form
# (builds/views.py build_form_options) don't both open a new vCenter connection on
# every page view.
VCENTER_CACHE_TTL = 180

# Sentinel to distinguish "not cached yet" from "cached, and the value is None" (a
# capacity check with no warning legitimately caches to None).
_NOT_CACHED = object()


def vcenter_host_for_site(site: str) -> str:
    return f"{site}-c1-vm-vcenter-01.cll.cloud"


def template_prefix_for_site(site: str) -> str:
    return f"{site}-c1-vmt-ubuntu2404-"


def _connect_vcenter(host: str, username: str, password: str):
    from pyVim.connect import SmartConnect

    # Internal vCenters use self-signed certs, same as PrepareNewBuild's connect_vcenter.
    context = ssl._create_unverified_context()
    return SmartConnect(host=host, user=username, pwd=password, sslContext=context)


def _get_newest_ubuntu_template(
    host: str, username: str, password: str, template_prefix: str
) -> str:
    from pyVim.connect import Disconnect
    from pyVmomi import vim, vmodl

    si = _connect_vcenter(host, username, password)
    try:
        content = si.RetrieveContent()
        container = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )
        try:
            traversal_spec = vmodl.query.PropertyCollector.TraversalSpec(
                name="traverseEntities", path="view", skip=False, type=type(container)
            )
            obj_spec = vmodl.query.PropertyCollector.ObjectSpec(
                obj=container, skip=True, selectSet=[traversal_spec]
            )
            property_spec = vmodl.query.PropertyCollector.PropertySpec(
                type=vim.VirtualMachine, pathSet=["name", "config.template"]
            )
            filter_spec = vmodl.query.PropertyCollector.FilterSpec(
                objectSet=[obj_spec], propSet=[property_spec]
            )
            results = content.propertyCollector.RetrieveContents([filter_spec])
        finally:
            container.Destroy()

        # Trailing YYMMDD-HHMM timestamp is fixed-width, so newest also sorts last lexically.
        names = [
            props["name"]
            for obj_content in results
            for props in [{p.name: p.val for p in obj_content.propSet}]
            if props.get("config.template")
            and props.get("name", "").startswith(template_prefix)
        ]
    finally:
        Disconnect(si)

    if not names:
        raise RuntimeError(
            f"No VM templates found on '{host}' matching prefix '{template_prefix}'."
        )
    return max(names)


def get_latest_ubuntu_template(site: str) -> str:
    """Real vCenter lookup (briefly cached, see VCENTER_CACHE_TTL) with a stub fallback
    for local dev without Vault/VPN access."""
    cache_key = f"vcenter_template_{site}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    username, password = get_vcenter_credentials(site)
    if not username or not password:
        template = STUB_TEMPLATE.get(site, "")
    else:
        try:
            template = _get_newest_ubuntu_template(
                vcenter_host_for_site(site),
                username,
                password,
                template_prefix_for_site(site),
            )
        except Exception:
            logger.warning(
                "vCenter template lookup failed for site '%s'; falling back to stub value.",
                site,
                exc_info=True,
            )
            template = STUB_TEMPLATE.get(site, "")

    cache.set(cache_key, template, VCENTER_CACHE_TTL)
    return template


LOW_FREE_SPACE_WARNING_THRESHOLD = 0.15  # warn below 15% free


def _get_datastore_free_ratio(
    host: str, username: str, password: str, datastore_name: str
):
    from pyVim.connect import Disconnect
    from pyVmomi import vim

    si = _connect_vcenter(host, username, password)
    try:
        content = si.RetrieveContent()
        container = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.Datastore], True
        )
        try:
            datastore = next(
                (d for d in container.view if d.name == datastore_name), None
            )
            # Read properties while the session is still open — pyVmomi objects are
            # lazy proxies that fetch over the (soon-to-be-closed) connection on access.
            if datastore is None or not datastore.summary.capacity:
                return None
            return datastore.summary.freeSpace / datastore.summary.capacity
        finally:
            container.Destroy()
    finally:
        Disconnect(si)


def check_datastore_capacity_warning(site: str, datastore_name: str) -> str | None:
    """Non-blocking pre-flight check — returns a warning string if the target datastore
    looks low on free space, or None if it looks fine, is unreachable, or credentials
    aren't configured. Never raises: this is advisory only, it must never block a build.
    Briefly cached (see VCENTER_CACHE_TTL) — capacity doesn't swing enough between builds
    to need a fresh check every time the confirm modal opens, and HA pairs are expected
    to land on separate datastores anyway (team convention, not enforced here).
    """
    if not datastore_name:
        return None

    cache_key = f"vcenter_capacity_warning_{site}_{datastore_name}"
    cached = cache.get(cache_key, _NOT_CACHED)
    if cached is not _NOT_CACHED:
        return cached

    username, password = get_vcenter_credentials(site)
    if not username or not password:
        return None

    try:
        ratio = _get_datastore_free_ratio(
            vcenter_host_for_site(site), username, password, datastore_name
        )
        if ratio is not None and ratio < LOW_FREE_SPACE_WARNING_THRESHOLD:
            warning = (
                f"Datastore '{datastore_name}' is at {ratio:.0%} free space "
                "— consider a different datastore if this build needs headroom."
            )
        else:
            warning = None
    except Exception:
        logger.warning(
            "Datastore capacity check failed for '%s' on site '%s'.",
            datastore_name,
            site,
            exc_info=True,
        )
        warning = None

    cache.set(cache_key, warning, VCENTER_CACHE_TTL)
    return warning


def _fetch_vcenter_inventory(
    host: str, username: str, password: str, site: str
) -> dict:
    from pyVim.connect import Disconnect
    from pyVmomi import vim

    si = _connect_vcenter(host, username, password)
    try:
        content = si.RetrieveContent()

        def _names(vimtype, predicate=None) -> list[str]:
            container = content.viewManager.CreateContainerView(
                content.rootFolder, [vimtype], True
            )
            try:
                # Extract names while the session is still open (see the lazy-proxy
                # note on check_datastore_capacity_warning) — never return the raw
                # objects themselves past Disconnect().
                return [
                    obj.name
                    for obj in container.view
                    if predicate is None or predicate(obj)
                ]
            finally:
                container.Destroy()

        def _portgroup_paths() -> list[str]:
            # Portgroups here are DVS-backed (confirmed against real vCenter data) —
            # the build process expects the full inventory path
            # "/<site>/network/<dvs name>/<portgroup name>", not just the plain name.
            container = content.viewManager.CreateContainerView(
                content.rootFolder, [vim.dvs.DistributedVirtualPortgroup], True
            )
            try:
                return [
                    f"/{site}/network/{pg.config.distributedVirtualSwitch.name}/{pg.name}"
                    for pg in container.view
                ]
            finally:
                container.Destroy()

        datastores = _names(vim.Datastore)
        clusters = _names(vim.ClusterComputeResource)
        # "Resources" is the implicit root resource pool every cluster has — not a
        # real choice, exclude it so only actual named sub-pools show up.
        resource_pools = [n for n in _names(vim.ResourcePool) if n != "Resources"]
        # Restrict to VM/template folders (excludes host/network/datastore folders),
        # and drop "vm" — the hidden root VM folder every datacenter has.
        folders = _names(
            vim.Folder,
            predicate=lambda f: "VirtualMachine" in (f.childType or [])
            and f.name != "vm",
        )
        portgroups = _portgroup_paths()

        return {
            "datastores": sorted(set(datastores)),
            "clusters": sorted(set(clusters)),
            "resource_pools": sorted(set(resource_pools)),
            "folders": sorted(set(folders)),
            "portgroups": sorted(set(portgroups)),
        }
    finally:
        Disconnect(si)


def get_vcenter_inventory(site: str) -> dict:
    """Live vCenter dropdown options for datastore/compute_cluster/resource_pool/
    vm_folder/portgroup (briefly cached, see VCENTER_CACHE_TTL). Falls back to
    builds.vcenter_stub's static data if vCenter is unreachable or credentials aren't
    configured, so the form still renders."""
    cache_key = f"vcenter_inventory_{site}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    username, password = get_vcenter_credentials(site)
    if not username or not password:
        inventory = _stub_vcenter_options(site)
    else:
        try:
            inventory = _fetch_vcenter_inventory(
                vcenter_host_for_site(site), username, password, site
            )
        except Exception:
            logger.warning(
                "vCenter inventory lookup failed for site '%s'; falling back to stub data.",
                site,
                exc_info=True,
            )
            inventory = _stub_vcenter_options(site)

    cache.set(cache_key, inventory, VCENTER_CACHE_TTL)
    return inventory
