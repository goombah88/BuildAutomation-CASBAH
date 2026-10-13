from django.db import models
from simple_history.models import HistoricalRecords


class BuildRequest(models.Model):
    """One record per server build submitted through CASBAH.

    This is the foundation for future logging/reporting features (see
    ai_prompt_revised.txt "BACKEND STACK") — every field a report might need
    should be captured here rather than derived later from GitLab/NetBox.
    """

    class Site(models.TextChoices):
        RCD09 = "rcd09", "RCD09"
        SJC17 = "sjc17", "SJC17"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        GITLAB_IN_PROGRESS = "gitlab_in_progress", "GitLab: in progress"
        GITLAB_FAILED = "gitlab_failed", "GitLab: failed"
        NETBOX_IN_PROGRESS = "netbox_in_progress", "NetBox: in progress"
        NETBOX_FAILED = "netbox_failed", "NetBox: failed"
        # Repo + NetBox VM created; now waiting on the terraform plan/merge/plan pipeline
        # flow (see builds/tasks.py advance_pipelines) before anything is actually applied.
        PLAN_IN_PROGRESS = "plan_in_progress", "Terraform: plan in progress"
        PLAN_FAILED = "plan_failed", "Terraform: plan failed"
        APPLY_IN_PROGRESS = "apply_in_progress", "Terraform: apply in progress"
        APPLY_FAILED = "apply_failed", "Terraform: apply failed"
        COMPLETE = "complete", "Complete"

    # Identity / form inputs
    vm_name = models.CharField(max_length=255)
    site = models.CharField(max_length=16, choices=Site.choices)
    ipv4_address = models.GenericIPAddressField(protocol="IPv4")
    ipv4_prefix_length = models.PositiveSmallIntegerField(default=24)
    tenant_group = models.CharField(max_length=255, default="HTD")
    tenant = models.CharField(max_length=255)
    num_cpus = models.PositiveSmallIntegerField(default=2)
    memory_gb = models.PositiveSmallIntegerField(default=4)

    # Firewall ticket intent — filed automatically in Jira on submit (see
    # builds/jira_client.py / builds/firewall_ticket.py) using the requester's own
    # personal Jira credentials.
    firewall_ticket_requested = models.BooleanField(default=True)
    firewall_ticket_reason = models.TextField(blank=True)
    # Populated once builds.tasks.run_build_pipeline successfully files the ticket.
    firewall_ticket_url = models.URLField(blank=True)
    # Best-effort: a Jira failure here never fails the build itself (see tasks.py),
    # so the reason it didn't get filed is surfaced here instead.
    firewall_ticket_error = models.TextField(blank=True)

    # Full form payload as submitted, for anything not broken out into its own
    # column above (compute cluster, resource pool, folder, portgroup, etc.)
    form_data = models.JSONField(default=dict, blank=True)

    # Who submitted it (nullable for now since local dev has no real auth wired up)
    requested_by = models.CharField(max_length=255, blank=True)

    # Outcome tracking
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.PENDING
    )
    gitlab_project_url = models.URLField(blank=True)
    # Populated as soon as create_build_repo succeeds (see builds/tasks.py) — lets
    # builds/views.build_pipeline_status poll GitLab's Pipelines API without needing
    # to re-derive the project id from gitlab_project_url.
    gitlab_project_id = models.PositiveIntegerField(null=True, blank=True)
    # Set by builds.tasks.advance_pipelines (background poller) once it auto-merges
    # development into main after development's plan job succeeds.
    gitlab_merged_at = models.DateTimeField(null=True, blank=True)
    # Set by the same poller once it auto-plays terraform_apply_job after main's own
    # (post-merge) plan job succeeds. Both auto-actions are deliberate — see
    # ai_prompt_revised.txt "pipeline monitoring" section.
    gitlab_apply_triggered_at = models.DateTimeField(null=True, blank=True)
    netbox_vm_url = models.URLField(blank=True)
    error_detail = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.vm_name} ({self.get_status_display()})"

    @property
    def is_terminal(self) -> bool:
        """True once the build has reached a final state — used to stop status polling."""
        return self.status in {
            self.Status.COMPLETE,
            self.Status.GITLAB_FAILED,
            self.Status.NETBOX_FAILED,
            self.Status.PLAN_FAILED,
            self.Status.APPLY_FAILED,
        }
