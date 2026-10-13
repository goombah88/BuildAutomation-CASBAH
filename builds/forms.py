"""Server Build Form — see ai_prompt_revised.txt section "2. SERVER BUILD FORM" for the
full field spec. Dropdown *options* for compute_cluster/resource_pool/folder/portgroup/
datastore come from live vCenter (builds.vcenter_client), and Tenant Group/Tenant Other
come from live NetBox (builds.netbox_client) — both fall back to static data if those
systems are unreachable, so the form still renders during local dev."""

from __future__ import annotations

import re

from django import forms
from django.urls import reverse

from .gitlab_client import find_build_repo_url, local_build_repo_path
from .netbox_client import (
    find_vm_url,
    get_all_tenant_group_names,
    get_all_tenant_names,
    ip_address_exists,
)
from .vcenter_client import get_latest_ubuntu_template, get_vcenter_inventory
from .widgets import ComboboxWidget

# Fully anchored (not just a prefix check) and restricted to a hyphen-separated
# lowercase-alnum hostname shape -- vsphere_vm_name ends up as a literal filename
# (gitlab_client.create_build_repo writes "{vm_name}.yaml"), so anything letting
# '/' or '..' through here would be a path-traversal vulnerability, not just a
# cosmetic naming-convention check.
VM_NAME_RE = {
    "rcd09": re.compile(r"^rcd09-[a-z][0-9]-[a-z0-9]+(?:-[a-z0-9]+)*$"),
    "sjc17": re.compile(r"^sjc17-[a-z][0-9]-[a-z0-9]+(?:-[a-z0-9]+)*$"),
}

PRIMARY_TENANT_CHOICES = [
    ("VMCloud Prod", "VMCloud Prod"),
    ("VMCloud Dev", "VMCloud Dev"),
    ("VMCloud Stage", "VMCloud Stage"),
]

DNS_SERVERS = {
    "rcd09": ["10.192.1.111", "10.192.1.112"],
    "sjc17": ["10.196.1.111", "10.196.1.112"],
}

DEFAULT_PORTGROUP = {
    "rcd09": "/rcd09/network/rcd09-infra-vm-sw/rcd09-infra-servers",
    "sjc17": "/sjc17/network/sjc17-infra-vm-sw/sjc17-infra-servers",
}

STAGING_DEFAULTS = {
    "vsphere_compute_cluster": "rcd09-infrastructure",
    "vsphere_resource_pool": "vm_staging",
    # Full inventory path — confirmed against real vCenter (DVS-backed portgroup) that
    # this is the format the build process actually expects, not a plain name.
    "vsphere_portgroup_name": "/rcd09/network/rcd09-infra-vm-sw/rcd09-infra-vm-staging",
}

# Datastore names shown in the dropdown must start with one of these prefixes.
DATASTORE_NAME_PREFIXES = ("internal", "external", "infra")
# Staging narrows this further to only "infra-*" datastores (cuts down on failed
# builds picking a datastore that isn't actually available to the staging environment).
STAGING_DATASTORE_NAME_PREFIXES = ("infra-",)
# Staging only has one usable resource pool -- restrict the choice entirely rather
# than just defaulting to it, so it can't be changed to something invalid.
STAGING_RESOURCE_POOL = "vm_staging"

FIREWALL_TICKET_CHOICES = [
    ("yes", "Yes"),
    ("no", "No"),
]


class ServerBuildForm(forms.Form):
    vsphere_vm_name = forms.CharField(max_length=255, label="Hostname")
    vsphere_vm_ipv4_address = forms.GenericIPAddressField(
        protocol="IPv4", label="IP Address"
    )
    vsphere_vm_ipv4_prefix_length = forms.IntegerField(
        initial=24, min_value=1, max_value=32, label="Prefix Length"
    )
    vsphere_vm_primary_vnic = forms.CharField(initial="ens192", label="Primary vNIC")
    vsphere_vm_num_cpus = forms.IntegerField(
        initial=2, min_value=2, max_value=24, label="Number of CPUs"
    )
    vsphere_vm_memory = forms.IntegerField(
        initial=8,
        min_value=4,
        max_value=64,
        help_text="GB; converted to MB on save",
        label="Memory (GB)",
    )
    tenant_group = forms.ChoiceField(
        initial="HTD",
        label="Tenant Group",
        widget=ComboboxWidget(attrs={"placeholder": "Type to search\u2026"}),
    )
    tenant = forms.ChoiceField(
        choices=PRIMARY_TENANT_CHOICES,
        initial="VMCloud Prod",
        widget=forms.RadioSelect,
        label="Tenant",
    )
    tenant_other = forms.ChoiceField(
        required=False,
        label="Other Tenant",
        widget=ComboboxWidget(attrs={"placeholder": "Type to search\u2026"}),
    )
    vsphere_datastore = forms.ChoiceField(label="Datastore")
    vsphere_compute_cluster = forms.ChoiceField(label="Compute Cluster")
    vsphere_resource_pool = forms.ChoiceField(label="Resource Pool")
    vsphere_vm_folder = forms.ChoiceField(label="VM Folder")
    vsphere_portgroup_name = forms.ChoiceField(
        label="Portgroup Name",
        widget=ComboboxWidget(attrs={"placeholder": "Type to search\u2026"}),
    )
    vsphere_vm_template = forms.CharField(label="VM Template")

    # Firewall ticket capture (see ai_prompt_revised.txt "Firewall Ticket Request" section).
    # Filed automatically in Jira on submit (see builds/jira_client.py) using the
    # requester's own personal Jira credentials (~/.api_variables_env EMAIL_ADDRESS/
    # JIRA_API_TOKEN).
    firewall_ticket_requested = forms.ChoiceField(
        choices=FIREWALL_TICKET_CHOICES,
        initial="yes",
        label="Create a standard-build firewall ticket for this VM?",
        widget=forms.RadioSelect(attrs={"x-model": "firewallTicketRequested"}),
    )
    firewall_ticket_reason = forms.CharField(
        required=False,
        label="Reason",
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                ":disabled": "firewallTicketRequested !== 'yes'",
            }
        ),
    )

    def __init__(
        self,
        *args,
        site: str,
        staging: bool = False,
        defer_lookups: bool = False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.site = site
        self.staging = staging

        # On-blur live validation (see builds/views.py validate_vm_name/validate_ip) —
        # matches the spec's "validate the vm_name when leaving the text box" requirement.
        self.fields["vsphere_vm_name"].widget.attrs.update(
            {
                "hx-get": reverse("validate_vm_name", kwargs={"site": site}),
                "hx-trigger": "blur",
                "hx-target": "#vm_name_feedback",
                "hx-include": "this",
            }
        )
        self.fields["vsphere_vm_ipv4_address"].widget.attrs.update(
            {
                "hx-get": reverse("validate_ip", kwargs={"site": site}),
                "hx-trigger": "blur",
                "hx-target": "#ip_feedback",
                "hx-include": "this",
            }
        )

        if defer_lookups:
            # Initial GET render: skip the blocking NetBox/vCenter calls so the page
            # shows immediately — views.build_form_options fills these fields in for
            # real via htmx right after load (see build_form.html hx-trigger="load").
            self.fields["tenant_group"].choices = [("HTD", "HTD")]
            self.fields["tenant_other"].choices = [("", "---------")]
            self.fields["vsphere_resource_pool"].choices = [("", "---------")]
            for name in (
                "vsphere_datastore",
                "vsphere_compute_cluster",
                "vsphere_vm_folder",
                "vsphere_portgroup_name",
            ):
                self.fields[name].choices = [("", "Loading\u2026")]
            return

        options = get_vcenter_inventory(site)
        self.fields["tenant_group"].choices = [
            (g, g) for g in sorted(get_all_tenant_group_names(), key=str.lower)
        ]

        datastore_prefixes = (
            STAGING_DATASTORE_NAME_PREFIXES if staging else DATASTORE_NAME_PREFIXES
        )
        self.fields["vsphere_datastore"].choices = [
            (d, d)
            for d in sorted(options["datastores"], key=str.lower)
            if d.lower().startswith(datastore_prefixes)
        ]
        self.fields["vsphere_compute_cluster"].choices = [
            (c, c) for c in sorted(options["clusters"], key=str.lower)
        ]
        if staging:
            self.fields["vsphere_resource_pool"].choices = [
                (STAGING_RESOURCE_POOL, STAGING_RESOURCE_POOL)
            ]
        else:
            self.fields["vsphere_resource_pool"].choices = [("", "---------")] + [
                (p, p) for p in sorted(options["resource_pools"], key=str.lower)
            ]
        self.fields["vsphere_vm_folder"].choices = [
            (f, f) for f in sorted(options["folders"], key=str.lower)
        ]
        self.fields["vsphere_portgroup_name"].choices = [
            (p, p) for p in sorted(options["portgroups"], key=str.lower)
        ]
        self.fields["tenant_other"].choices = [("", "---------")] + [
            (t, t) for t in sorted(get_all_tenant_names(), key=str.lower)
        ]

        if not self.is_bound:
            self.fields["vsphere_vm_template"].initial = get_latest_ubuntu_template(
                site
            )
            self.fields["vsphere_portgroup_name"].initial = DEFAULT_PORTGROUP.get(
                site, ""
            )
            if staging:
                for field_name, value in STAGING_DEFAULTS.items():
                    if field_name in self.fields:
                        self.fields[field_name].initial = value

    def clean_vsphere_vm_name(self) -> str:
        name = self.cleaned_data["vsphere_vm_name"].strip().lower()
        pattern = VM_NAME_RE.get(self.site)
        if pattern and not pattern.fullmatch(name):
            raise forms.ValidationError(
                f"Name must look like '{self.site}-<letter><digit>-<name>' "
                f"(e.g. '{self.site}-d1-...'), using only lowercase letters, "
                "digits, and hyphens."
            )
        # None means NetBox/GitLab was unreachable — can't verify, so don't block the
        # submission over it (same fallback-to-permissive policy as the dropdown data).
        netbox_url = find_vm_url(name)
        if netbox_url:
            raise forms.ValidationError(
                f"A NetBox VM named '{name}' already exists: {netbox_url}"
            )
        gitlab_url = find_build_repo_url(name)
        if gitlab_url:
            raise forms.ValidationError(
                f"A GitLab build repo named '{name}' already exists: {gitlab_url}"
            )
        local_path = local_build_repo_path(name)
        if local_path:
            raise forms.ValidationError(
                f"A local build copy for '{name}' already exists at {local_path} "
                "-- remove it manually before retrying."
            )
        return name

    def clean_vsphere_vm_ipv4_address(self) -> str:
        address = self.cleaned_data["vsphere_vm_ipv4_address"]
        # None means NetBox was unreachable — can't verify, so don't block the
        # submission over it (same fallback-to-permissive policy as clean_vsphere_vm_name).
        if ip_address_exists(address) is True:
            raise forms.ValidationError(f"IP address '{address}' is already in use.")
        return address

    def clean(self):
        cleaned_data = super().clean()

        # "Other" dropdown overrides the primary-3 radio selection when chosen.
        if cleaned_data.get("tenant_other"):
            cleaned_data["tenant"] = cleaned_data["tenant_other"]

        if (
            cleaned_data.get("firewall_ticket_requested") == "yes"
            and not cleaned_data.get("firewall_ticket_reason", "").strip()
        ):
            self.add_error(
                "firewall_ticket_reason",
                "A reason is required when requesting a firewall ticket.",
            )
        return cleaned_data
