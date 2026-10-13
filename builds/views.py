from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django_q.tasks import async_task

import csv
import ipaddress

from .forms import VM_NAME_RE, ServerBuildForm
from .gitlab_client import (
    APPLY_JOB_NAME,
    DEFAULT_BRANCH,
    DEVELOPMENT_BRANCH,
    PIPELINE_TERMINAL_STATUSES,
    PLAN_JOB_NAME,
    find_build_repo_url,
    get_gitlab_api_token,
    get_latest_pipeline,
    get_pipeline_jobs,
    local_build_repo_path,
)
from .models import BuildRequest
from .netbox_client import (
    find_vm_url,
    get_all_tenant_group_names,
    get_all_tenant_names,
    ip_address_exists,
)
from .vcenter_client import check_datastore_capacity_warning
from .yaml_builder import build_yaml_preview

VALID_SITES = {"rcd09", "sjc17"}

OK = '<span style="color:#227a3c;">✓ {}</span>'
BAD = '<span style="color:#c0392b;">✗ {}</span>'


def landing(request):
    return render(request, "builds/landing.html")


def build_form(request, site: str, staging: bool = False):
    site = site.lower()
    if site not in VALID_SITES or (staging and site != "rcd09"):
        raise Http404("Unknown site")

    if request.method == "POST":
        form = ServerBuildForm(request.POST, site=site, staging=staging)
        if form.is_valid():
            data = form.cleaned_data
            build_request = BuildRequest.objects.create(
                vm_name=data["vsphere_vm_name"],
                site=site,
                ipv4_address=data["vsphere_vm_ipv4_address"],
                ipv4_prefix_length=data["vsphere_vm_ipv4_prefix_length"],
                tenant_group=data["tenant_group"],
                tenant=data["tenant"],
                num_cpus=data["vsphere_vm_num_cpus"],
                memory_gb=data["vsphere_vm_memory"],
                firewall_ticket_requested=data["firewall_ticket_requested"] == "yes",
                firewall_ticket_reason=data.get("firewall_ticket_reason", ""),
                form_data=request.POST.dict(),
                requested_by=request.user.get_username(),
            )
            async_task("builds.tasks.run_build_pipeline", build_request.id)
            return redirect(reverse("build_submitted", args=[build_request.id]))
    else:
        form = ServerBuildForm(site=site, staging=staging, defer_lookups=True)

    return render(
        request,
        "builds/build_form.html",
        {
            "form": form,
            "site": site,
            "staging": staging,
            "options_url": reverse(
                "build_form_staging_options" if staging else "build_form_options",
                kwargs={} if staging else {"site": site},
            ),
            "dns_servers": {
                "rcd09": ["10.192.1.111", "10.192.1.112"],
                "sjc17": ["10.196.1.111", "10.196.1.112"],
            }[site],
            "vsphere_server": {
                "rcd09": "rcd09-c1-vm-vcenter-01.cll.cloud",
                "sjc17": "sjc17-c1-vm-vcenter-01.cll.cloud",
            }[site],
            "vsphere_datacenter": site,
        },
    )


def build_submitted(request, pk: int):
    build_request = get_object_or_404(BuildRequest, pk=pk)
    return render(
        request, "builds/build_submitted.html", {"build_request": build_request}
    )


def netbox_options_prefetch(request):
    """Warms the NetBox tenant/tenant-group cache (see builds/netbox_client.py) without
    blocking anything visible — hit via hx-trigger="load" on landing.html, since these
    values aren't site-specific and are usually needed a few seconds later once the user
    picks a site and lands on the build form."""
    get_all_tenant_group_names()
    get_all_tenant_names()
    return HttpResponse(status=204)


def build_form_options(request, site: str, staging: bool = False):
    """Live NetBox/vCenter dropdown values, rendered as an out-of-band htmx fragment.
    Used two ways: (1) build_form.html fires this via hx-trigger="load" right after the
    form shell renders, swapping the real choices in instead of blocking the initial
    page render on them; (2) landing.html's site buttons fire this on hover
    (hx-swap="none") purely to warm the cache ahead of the click."""
    site = site.lower()
    if site not in VALID_SITES or (staging and site != "rcd09"):
        raise Http404("Unknown site")
    form = ServerBuildForm(site=site, staging=staging)
    response = render(request, "builds/_build_form_options.html", {"form": form})
    # Never let the browser cache this fragment — freshness is already handled by our own
    # server-side cache (see vcenter_client.py/netbox_client.py), and a stale browser-cached
    # copy can silently reintroduce fixed bugs (e.g. missing markup) after a template change.
    response["Cache-Control"] = "no-store"
    return response


def validate_vm_name(request, site: str):
    """On-blur check for vsphere_vm_name (htmx): format, then live uniqueness against
    CASBAH's own BuildRequest history, NetBox, GitLab (infrastructure-as-code/vms), and
    the local VM_BUILDS_DIR copy — checks ALL of them (not just the first match) and
    links directly to each, so a rebuild's needed manual cleanup is quick to find. This
    is UX-only — the real, authoritative check is forms.py clean_vsphere_vm_name, which
    runs again at submit time."""
    site = site.lower()
    name = request.GET.get("vsphere_vm_name", "").strip().lower()
    if not name:
        return HttpResponse("")
    pattern = VM_NAME_RE.get(site)
    if pattern and not pattern.match(name):
        return HttpResponse(
            BAD.format(f"Must start with '{site}-&lt;letter&gt;&lt;digit&gt;-'")
        )

    found = []
    existing_build = BuildRequest.objects.filter(vm_name=name).first()
    if existing_build:
        url = reverse("build_submitted", args=[existing_build.pk])
        found.append(f'<a href="{url}">CASBAH build history</a>')
    netbox_url = find_vm_url(name)
    if netbox_url:
        found.append(f'<a href="{netbox_url}" target="_blank">NetBox</a>')
    gitlab_url = find_build_repo_url(name)
    if gitlab_url:
        found.append(f'<a href="{gitlab_url}" target="_blank">GitLab</a>')
    local_path = local_build_repo_path(name)
    if local_path:
        found.append(f'<a href="file://{local_path}">local build dir</a>')

    if found:
        return HttpResponse(BAD.format("Already exists in: " + ", ".join(found)))
    return HttpResponse(OK.format("Name is available"))


def validate_ip(request, site: str):
    """On-blur check for vsphere_vm_ipv4_address (htmx): format, then live availability
    against CASBAH's own BuildRequest history and NetBox. This is UX-only — the real,
    authoritative check is forms.py clean_vsphere_vm_ipv4_address, which runs again at
    submit time."""
    value = request.GET.get("vsphere_vm_ipv4_address", "").strip()
    if not value:
        return HttpResponse("")
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        return HttpResponse(BAD.format("Not a valid IPv4 address"))
    if BuildRequest.objects.filter(ipv4_address=value).exists():
        return HttpResponse(BAD.format("Already used by another CASBAH build"))
    if ip_address_exists(value) is True:
        return HttpResponse(BAD.format("Already in use in NetBox"))
    return HttpResponse(OK.format("Address is available"))


def _build_progress_steps(
    build_request, dev_pipeline, dev_jobs, main_pipeline, main_jobs
):
    """Computes the ordered step-by-step flow shown on the build-submitted page:
    (1) create the GitLab repo + NetBox VM, (2) development's plan job, (3) auto-merge
    into main, (4) main's post-merge plan job, (5) auto-triggered terraform apply.
    Returns (steps, flow_terminal) — flow_terminal is True once nothing more will
    happen automatically (a bad failure, or the final apply job itself finished)."""
    steps = []

    if build_request.status in (
        BuildRequest.Status.GITLAB_FAILED,
        BuildRequest.Status.NETBOX_FAILED,
    ):
        step1_state = "failed"
    elif build_request.status in (
        BuildRequest.Status.PENDING,
        BuildRequest.Status.GITLAB_IN_PROGRESS,
        BuildRequest.Status.NETBOX_IN_PROGRESS,
    ):
        step1_state = "active"
    else:
        # PLAN_IN_PROGRESS/PLAN_FAILED/APPLY_IN_PROGRESS/APPLY_FAILED/COMPLETE all mean
        # the repo + VM themselves were created successfully -- later steps below are
        # what actually distinguish the rest of the flow's progress/failure.
        step1_state = "done"
    steps.append(
        {
            "title": "Create GitLab repo & NetBox VM",
            "state": step1_state,
            "detail": build_request.get_status_display(),
            "error": build_request.error_detail if step1_state == "failed" else "",
            "links": [
                (label, url)
                for label, url in (
                    ("GitLab project", build_request.gitlab_project_url),
                    ("NetBox VM", build_request.netbox_vm_url),
                )
                if url
            ],
        }
    )
    if (
        step1_state == "failed"
        and build_request.status == BuildRequest.Status.GITLAB_FAILED
    ):
        # No gitlab_project_id was ever set -- nothing downstream can happen.
        return steps, True

    dev_success = bool(dev_pipeline) and dev_pipeline["status"] == "success"
    dev_failed = (
        bool(dev_pipeline)
        and dev_pipeline["status"] in PIPELINE_TERMINAL_STATUSES
        and dev_pipeline["status"] != "success"
    )
    if not build_request.gitlab_project_id:
        step2_state = "pending"
    elif dev_success:
        step2_state = "done"
    elif dev_failed:
        step2_state = "failed"
    else:
        step2_state = "active"
    steps.append(
        {
            "title": "Plan (development branch)",
            "state": step2_state,
            "detail": dev_pipeline["status"] if dev_pipeline else "not started yet",
            "jobs": dev_jobs,
            "web_url": dev_pipeline["web_url"] if dev_pipeline else "",
        }
    )
    if step2_state in ("pending", "failed"):
        return steps, step2_state == "failed"

    if build_request.gitlab_merged_at:
        step3_state = "done"
    elif dev_success:
        step3_state = "active"
    else:
        step3_state = "pending"
    steps.append(
        {
            "title": "Auto-merge development \u2192 main",
            "state": step3_state,
            "detail": (
                f"merged {build_request.gitlab_merged_at}"
                if build_request.gitlab_merged_at
                else "waiting for the background poller to merge it"
            ),
        }
    )
    if step3_state != "done":
        return steps, False

    main_plan_job = next((j for j in main_jobs if j["name"] == PLAN_JOB_NAME), None)
    if not main_pipeline:
        step4_state = "active"
    elif main_plan_job and main_plan_job["status"] == "success":
        step4_state = "done"
    elif main_plan_job and main_plan_job["status"] in PIPELINE_TERMINAL_STATUSES:
        step4_state = "failed"
    else:
        step4_state = "active"
    steps.append(
        {
            "title": "Plan (main, post-merge)",
            "state": step4_state,
            "detail": main_plan_job["status"] if main_plan_job else "not started yet",
            "jobs": main_jobs,
            "web_url": main_pipeline["web_url"] if main_pipeline else "",
        }
    )
    if step4_state != "done":
        return steps, step4_state == "failed"

    apply_job = next((j for j in main_jobs if j["name"] == APPLY_JOB_NAME), None)
    if build_request.gitlab_apply_triggered_at:
        step5_state = (
            "done"
            if apply_job and apply_job["status"] in PIPELINE_TERMINAL_STATUSES
            else "active"
        )
    else:
        step5_state = "active"
    steps.append(
        {
            "title": "Terraform apply",
            "state": step5_state,
            "detail": (
                apply_job["status"]
                if apply_job
                else "waiting for the background poller to trigger it"
            ),
            "web_url": main_pipeline["web_url"] if main_pipeline else "",
        }
    )
    flow_terminal = step5_state == "done" and bool(
        apply_job and apply_job["status"] in PIPELINE_TERMINAL_STATUSES
    )
    return steps, flow_terminal


def build_pipeline_status(request, pk: int):
    """Polled by builds/_pipeline_status_fragment.html — renders the whole build as a
    single ordered step flow (repo/VM creation, then development's plan pipeline,
    then the auto-merge, then main's post-merge plan+apply pipeline). Unreachable
    GitLab/no pipeline yet just renders an "unknown" state rather than erroring — this
    view itself is monitoring/display only; advance_pipelines (builds/tasks.py) is
    what actually drives the merge/apply forward."""
    build_request = get_object_or_404(BuildRequest, pk=pk)
    dev_pipeline = None
    dev_jobs = []
    main_pipeline = None
    main_jobs = []

    if build_request.gitlab_project_id:
        token = get_gitlab_api_token()
        if token:
            dev_pipeline = get_latest_pipeline(
                build_request.gitlab_project_id, token, ref=DEVELOPMENT_BRANCH
            )
            if dev_pipeline:
                dev_jobs = get_pipeline_jobs(
                    build_request.gitlab_project_id, dev_pipeline["id"], token
                )
            if build_request.gitlab_merged_at:
                main_pipeline = get_latest_pipeline(
                    build_request.gitlab_project_id, token, ref=DEFAULT_BRANCH
                )
                if main_pipeline:
                    main_jobs = get_pipeline_jobs(
                        build_request.gitlab_project_id, main_pipeline["id"], token
                    )

    steps, pipeline_terminal = _build_progress_steps(
        build_request, dev_pipeline, dev_jobs, main_pipeline, main_jobs
    )

    return render(
        request,
        "builds/_pipeline_status_fragment.html",
        {
            "build_request": build_request,
            "steps": steps,
            "pipeline_terminal": pipeline_terminal,
        },
    )


def build_preview(request, site: str):
    """Renders the confirmation-step preview (yaml + capacity warning) from raw POST data.
    Deliberately does NOT run full form validation — that still happens exclusively in the
    real build_form view when the user clicks "Confirm & Create Build" inside the modal.
    """
    site = site.lower()
    if site not in VALID_SITES:
        raise Http404("Unknown site")

    yaml_text = build_yaml_preview(request.POST, site)
    capacity_warning = check_datastore_capacity_warning(
        site, request.POST.get("vsphere_datastore", "")
    )
    return render(
        request,
        "builds/_confirm_modal.html",
        {"yaml_text": yaml_text, "capacity_warning": capacity_warning},
    )


def _filtered_builds(request):
    qs = BuildRequest.objects.all()
    site = request.GET.get("site", "")
    tenant = request.GET.get("tenant", "")
    status = request.GET.get("status", "")
    if site:
        qs = qs.filter(site=site)
    if tenant:
        qs = qs.filter(tenant=tenant)
    if status:
        qs = qs.filter(status=status)
    return qs


def build_list(request):
    builds = _filtered_builds(request)
    return render(
        request,
        "builds/build_list.html",
        {
            "builds": builds[:200],
            "site_choices": BuildRequest.Site.choices,
            "status_choices": BuildRequest.Status.choices,
            "selected_site": request.GET.get("site", ""),
            "selected_tenant": request.GET.get("tenant", ""),
            "selected_status": request.GET.get("status", ""),
        },
    )


def build_list_csv(request):
    builds = _filtered_builds(request)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="casbah_builds.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "vm_name",
            "site",
            "tenant",
            "status",
            "ipv4_address",
            "requested_by",
            "created_at",
        ]
    )
    for build_request in builds:
        writer.writerow(
            [
                build_request.vm_name,
                build_request.site,
                build_request.tenant,
                build_request.get_status_display(),
                build_request.ipv4_address,
                build_request.requested_by,
                build_request.created_at.isoformat(),
            ]
        )
    return response
