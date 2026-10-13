"""Background tasks queued via django-q2 (ORM broker, see casbah/settings.py Q_CLUSTER).

run_build_pipeline runs the real GitLab fork/edit (builds.gitlab_client) + NetBox VM/
interface/IP creation (builds.netbox_client) steps (see ai_prompt_revised.txt sections 4
and 5). Per the resolved ERROR HANDLING policy, failures update `status`/`error_detail`
and never silently roll back steps that already succeeded.

advance_pipelines runs on a django_q Schedule (every minute, see the 0005 migration)
and auto-merges development into main once its plan job succeeds, then auto-plays
terraform_apply_job once main's post-merge plan job succeeds \u2014 both confirmed with
the user 2026-09-29, deliberately removing GitLab's `when: manual` human checkpoint.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

from .firewall_ticket import FirewallTicketError, create_standard_build_ticket
from .gitlab_client import (
    APPLY_JOB_NAME,
    DEFAULT_BRANCH,
    DEVELOPMENT_BRANCH,
    PIPELINE_TERMINAL_STATUSES,
    PLAN_JOB_NAME,
    GitLabBuildError,
    create_build_repo,
    get_gitlab_api_token,
    get_latest_pipeline,
    get_pipeline_jobs,
    merge_development_branch,
    play_job,
)
from .models import BuildRequest
from .netbox_client import NetBoxBuildError, create_build_vm
from .yaml_builder import build_yaml_preview

logger = logging.getLogger(__name__)


def _file_firewall_ticket(build_request: BuildRequest) -> None:
    """Best-effort: files the standard-build firewall ticket in Jira using the
    requester's own personal credentials (settings.JIRA_EMAIL/JIRA_API_TOKEN, read from
    ~/.api_variables_env). A failure here is recorded on the build but never raises --
    the GitLab/NetBox build itself must not fail just because Jira is unreachable or
    credentials aren't set up yet."""
    if not build_request.firewall_ticket_requested:
        return
    if not settings.JIRA_EMAIL or not settings.JIRA_API_TOKEN:
        build_request.firewall_ticket_error = (
            "JIRA_EMAIL/JIRA_API_TOKEN not configured (see ~/.api_variables_env)."
        )
        build_request.save(update_fields=["firewall_ticket_error", "updated_at"])
        return
    try:
        build_request.firewall_ticket_url = create_standard_build_ticket(
            hostname=build_request.vm_name,
            site=build_request.site,
            ip=build_request.ipv4_address,
            reason=build_request.firewall_ticket_reason,
            email=settings.JIRA_EMAIL,
            token=settings.JIRA_API_TOKEN,
        )
        build_request.save(update_fields=["firewall_ticket_url", "updated_at"])
    except FirewallTicketError as exc:
        logger.warning(
            "Firewall ticket filing failed for build %s.",
            build_request.pk,
            exc_info=True,
        )
        build_request.firewall_ticket_error = str(exc)
        build_request.save(update_fields=["firewall_ticket_error", "updated_at"])


def run_build_pipeline(build_request_id: int) -> None:
    build_request = BuildRequest.objects.get(pk=build_request_id)
    try:
        build_request.status = BuildRequest.Status.GITLAB_IN_PROGRESS
        build_request.save(update_fields=["status", "updated_at"])

        yaml_text = build_yaml_preview(build_request.form_data, build_request.site)
        result = create_build_repo(build_request.vm_name, yaml_text)
        build_request.gitlab_project_url = result["project_web_url"]
        build_request.gitlab_project_id = result["project_id"]

        build_request.status = BuildRequest.Status.NETBOX_IN_PROGRESS
        build_request.save(
            update_fields=[
                "status",
                "gitlab_project_url",
                "gitlab_project_id",
                "updated_at",
            ]
        )

        netbox_result = create_build_vm(
            vm_name=build_request.vm_name,
            site=build_request.site,
            cluster_name=build_request.form_data.get("vsphere_compute_cluster", ""),
            tenant_group=build_request.tenant_group,
            tenant=build_request.tenant,
            num_cpus=build_request.num_cpus,
            memory_gb=build_request.memory_gb,
            ipv4_address=build_request.ipv4_address,
            ipv4_prefix_length=build_request.ipv4_prefix_length,
        )
        build_request.netbox_vm_url = netbox_result["vm_url"]

        _file_firewall_ticket(build_request)

        # Repo + VM exist, but the build isn't done yet -- advance_pipelines still has
        # to drive the terraform plan/merge/apply flow to completion (see below).
        build_request.status = BuildRequest.Status.PLAN_IN_PROGRESS
        build_request.save(update_fields=["status", "netbox_vm_url", "updated_at"])
    except GitLabBuildError as exc:
        build_request.status = BuildRequest.Status.GITLAB_FAILED
        build_request.error_detail = str(exc)
        build_request.save(update_fields=["status", "error_detail", "updated_at"])
        raise
    except NetBoxBuildError as exc:
        build_request.status = BuildRequest.Status.NETBOX_FAILED
        build_request.error_detail = str(exc)
        build_request.save(update_fields=["status", "error_detail", "updated_at"])
        raise
    except Exception as exc:  # surfaced via error_detail, no auto-rollback (see spec)
        build_request.error_detail = str(exc)
        build_request.save(update_fields=["error_detail", "updated_at"])
        raise
    # Reaching here only means the repo + VM exist -- NOT terminal.
    # advance_pipelines (below) still has to drive the plan/merge/apply flow and is
    # what marks the build COMPLETE/*_FAILED.


def _mark_plan_failed(build_request: BuildRequest, detail: str) -> None:
    build_request.status = BuildRequest.Status.PLAN_FAILED
    build_request.error_detail = detail
    build_request.save(update_fields=["status", "error_detail", "updated_at"])


def _maybe_merge(build_request: BuildRequest, token: str) -> None:
    """Auto-merges development into main once development's own plan job has
    succeeded \u2014 no human reviews the plan output first (confirmed with user,
    2026-09-29). No-ops (retried on the next poll) if the pipeline isn't done yet,
    or the merge call itself fails. Marks the build PLAN_FAILED (terminal) if the
    development pipeline itself fails outright."""
    pipeline = get_latest_pipeline(
        build_request.gitlab_project_id, token, ref=DEVELOPMENT_BRANCH
    )
    if not pipeline:
        return
    if (
        pipeline["status"] in PIPELINE_TERMINAL_STATUSES
        and pipeline["status"] != "success"
    ):
        _mark_plan_failed(
            build_request,
            f"Development branch pipeline finished with status '{pipeline['status']}'.",
        )
        return
    if pipeline["status"] != "success":
        return
    try:
        merge_development_branch(build_request.gitlab_project_id, token)
    except GitLabBuildError:
        logger.warning(
            "Auto-merge of development into main failed for build %s.",
            build_request.pk,
            exc_info=True,
        )
        return
    build_request.gitlab_merged_at = timezone.now()
    build_request.save(update_fields=["gitlab_merged_at", "updated_at"])


def _maybe_apply(build_request: BuildRequest, token: str) -> None:
    """Auto-plays terraform_apply_job once main's own (post-merge) plan job has
    succeeded \u2014 no further human checkpoint (confirmed with user, 2026-09-29),
    even though the job is `when: manual` in skeleton-vm's .gitlab-ci.yml. The plan
    and apply jobs share one pipeline (apply just sits `manual` until played), so this
    checks job-level status, not the pipeline's overall status. Marks the build
    PLAN_FAILED (terminal) if main's post-merge plan job fails outright."""
    pipeline = get_latest_pipeline(
        build_request.gitlab_project_id, token, ref=DEFAULT_BRANCH
    )
    if not pipeline:
        return
    jobs = get_pipeline_jobs(build_request.gitlab_project_id, pipeline["id"], token)
    plan_job = next((j for j in jobs if j["name"] == PLAN_JOB_NAME), None)
    if (
        plan_job
        and plan_job["status"] in PIPELINE_TERMINAL_STATUSES
        and plan_job["status"] != "success"
    ):
        _mark_plan_failed(
            build_request,
            f"Main (post-merge) plan job finished with status '{plan_job['status']}'.",
        )
        return
    if not plan_job or plan_job["status"] != "success":
        return
    apply_job = next((j for j in jobs if j["name"] == APPLY_JOB_NAME), None)
    if not apply_job or apply_job["status"] != "manual":
        return
    try:
        play_job(build_request.gitlab_project_id, apply_job["id"], token)
    except GitLabBuildError:
        logger.warning(
            "Auto-play of terraform_apply_job failed for build %s.",
            build_request.pk,
            exc_info=True,
        )
        return
    build_request.gitlab_apply_triggered_at = timezone.now()
    build_request.status = BuildRequest.Status.APPLY_IN_PROGRESS
    build_request.save(
        update_fields=["gitlab_apply_triggered_at", "status", "updated_at"]
    )


def _maybe_finalize(build_request: BuildRequest, token: str) -> None:
    """Marks the build COMPLETE (or APPLY_FAILED) once the already-triggered
    terraform_apply_job itself reaches a terminal status \u2014 without this, `status`
    gets stuck at APPLY_IN_PROGRESS forever, since nothing else here re-checks a job
    once it's been played."""
    pipeline = get_latest_pipeline(
        build_request.gitlab_project_id, token, ref=DEFAULT_BRANCH
    )
    if not pipeline:
        return
    jobs = get_pipeline_jobs(build_request.gitlab_project_id, pipeline["id"], token)
    apply_job = next((j for j in jobs if j["name"] == APPLY_JOB_NAME), None)
    if not apply_job or apply_job["status"] not in PIPELINE_TERMINAL_STATUSES:
        return
    if apply_job["status"] == "success":
        build_request.status = BuildRequest.Status.COMPLETE
    else:
        build_request.status = BuildRequest.Status.APPLY_FAILED
        build_request.error_detail = (
            f"terraform_apply_job finished with status '{apply_job['status']}'."
        )
    build_request.save(update_fields=["status", "error_detail", "updated_at"])


def advance_pipelines() -> None:
    """Periodic task (see django_q Schedule registered in builds/migrations/
    0005_buildrequest_gitlab_merged_at_and_more.py) that drives each build's GitLab
    pipeline forward without any human clicking through GitLab's UI: merges
    development into main once its plan job succeeds, then plays terraform_apply_job
    once main's own post-merge plan job succeeds. Both steps are best-effort per
    build \u2014 one build's GitLab hiccup must never stop this from checking the rest.
    """
    token = get_gitlab_api_token()
    if not token:
        return

    # PLAN_FAILED is terminal -- excluded everywhere below so a build that failed
    # outright doesn't get re-checked (and re-notified) on every poll forever.
    pending_merge = BuildRequest.objects.filter(
        gitlab_project_id__isnull=False, gitlab_merged_at__isnull=True
    ).exclude(status=BuildRequest.Status.PLAN_FAILED)
    for build_request in pending_merge:
        _maybe_merge(build_request, token)

    pending_apply = BuildRequest.objects.filter(
        gitlab_merged_at__isnull=False, gitlab_apply_triggered_at__isnull=True
    ).exclude(status=BuildRequest.Status.PLAN_FAILED)
    for build_request in pending_apply:
        _maybe_apply(build_request, token)

    pending_finalize = BuildRequest.objects.filter(
        gitlab_apply_triggered_at__isnull=False,
        status=BuildRequest.Status.APPLY_IN_PROGRESS,
    )
    for build_request in pending_finalize:
        _maybe_finalize(build_request, token)
