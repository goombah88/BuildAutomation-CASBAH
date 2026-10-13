from unittest.mock import patch

from django.test import TestCase, override_settings

from builds.gitlab_client import GitLabBuildError
from builds.models import BuildRequest
from builds.netbox_client import NetBoxBuildError
from builds.tasks import run_build_pipeline


def make_build_request(**overrides):
    defaults = {
        "vm_name": "rcd09-d1-testvm01",
        "site": BuildRequest.Site.RCD09,
        "ipv4_address": "10.0.0.5",
        "ipv4_prefix_length": 24,
        "tenant_group": "HTD",
        "tenant": "VMCloud Dev",
        "num_cpus": 2,
        "memory_gb": 8,
        "form_data": {"vsphere_compute_cluster": "rcd09-infrastructure"},
    }
    defaults.update(overrides)
    return BuildRequest.objects.create(**defaults)


@patch(
    "builds.tasks.create_standard_build_ticket",
    return_value="https://jira.example/browse/LABS-1",
)
@patch("builds.tasks.build_yaml_preview", return_value="key: value\n")
class RunBuildPipelineTests(TestCase):
    @patch("builds.tasks.create_build_vm")
    @patch("builds.tasks.create_build_repo")
    def test_happy_path_marks_plan_in_progress_and_stores_urls(
        self, mock_create_repo, mock_create_vm, _mock_yaml, _mock_ticket
    ):
        mock_create_repo.return_value = {
            "project_id": 7,
            "project_web_url": "https://gitlab.example/vms/rcd09-d1-testvm01",
            "merge_request_url": "https://gitlab.example/vms/rcd09-d1-testvm01/-/merge_requests/new",
        }
        mock_create_vm.return_value = {
            "vm_url": "https://netbox.example/virtualization/virtual-machines/1/"
        }
        build_request = make_build_request()

        run_build_pipeline(build_request.id)

        build_request.refresh_from_db()
        # Repo + VM creation succeeding isn't the whole build -- advance_pipelines
        # still has to drive the terraform plan/merge/apply flow to COMPLETE.
        self.assertEqual(build_request.status, BuildRequest.Status.PLAN_IN_PROGRESS)
        self.assertEqual(build_request.gitlab_project_id, 7)
        self.assertEqual(
            build_request.gitlab_project_url,
            "https://gitlab.example/vms/rcd09-d1-testvm01",
        )
        self.assertEqual(
            build_request.netbox_vm_url,
            "https://netbox.example/virtualization/virtual-machines/1/",
        )
        mock_create_vm.assert_called_once_with(
            vm_name="rcd09-d1-testvm01",
            site="rcd09",
            cluster_name="rcd09-infrastructure",
            tenant_group="HTD",
            tenant="VMCloud Dev",
            num_cpus=2,
            memory_gb=8,
            ipv4_address="10.0.0.5",
            ipv4_prefix_length=24,
        )

    @patch("builds.tasks.create_build_repo")
    def test_gitlab_failure_marks_gitlab_failed_and_reraises(
        self, mock_create_repo, _mock_yaml, _mock_ticket
    ):
        mock_create_repo.side_effect = GitLabBuildError("fork failed")
        build_request = make_build_request()

        with self.assertRaises(GitLabBuildError):
            run_build_pipeline(build_request.id)

        build_request.refresh_from_db()
        self.assertEqual(build_request.status, BuildRequest.Status.GITLAB_FAILED)
        self.assertEqual(build_request.error_detail, "fork failed")

    @patch("builds.tasks.create_build_vm")
    @patch("builds.tasks.create_build_repo")
    def test_netbox_failure_keeps_gitlab_result_and_marks_netbox_failed(
        self, mock_create_repo, mock_create_vm, _mock_yaml, _mock_ticket
    ):
        mock_create_repo.return_value = {
            "project_id": 7,
            "project_web_url": "https://gitlab.example/vms/rcd09-d1-testvm01",
            "merge_request_url": "https://gitlab.example/vms/rcd09-d1-testvm01/-/merge_requests/new",
        }
        mock_create_vm.side_effect = NetBoxBuildError("vm create failed")
        build_request = make_build_request()

        with self.assertRaises(NetBoxBuildError):
            run_build_pipeline(build_request.id)

        build_request.refresh_from_db()
        self.assertEqual(build_request.status, BuildRequest.Status.NETBOX_FAILED)
        self.assertEqual(build_request.error_detail, "vm create failed")
        # GitLab step's result must not be rolled back (see ai_prompt_revised.txt
        # ERROR HANDLING policy: no automatic rollback of steps that already succeeded).
        self.assertEqual(
            build_request.gitlab_project_url,
            "https://gitlab.example/vms/rcd09-d1-testvm01",
        )

    def test_unexpected_error_is_surfaced_without_changing_status(
        self, mock_yaml, _mock_ticket
    ):
        mock_yaml.side_effect = RuntimeError("unexpected boom")
        build_request = make_build_request()

        with self.assertRaises(RuntimeError):
            run_build_pipeline(build_request.id)

        build_request.refresh_from_db()
        # Only GITLAB_IN_PROGRESS was set before the unexpected failure — the generic
        # except branch records error_detail but doesn't reassign status (see tasks.py).
        self.assertEqual(build_request.status, BuildRequest.Status.GITLAB_IN_PROGRESS)
        self.assertEqual(build_request.error_detail, "unexpected boom")


@override_settings(JIRA_EMAIL="user@example.com", JIRA_API_TOKEN="tok")
class FileFirewallTicketTests(TestCase):
    """Explicit override_settings on every test here guards against this ever hitting
    the real Jira API, regardless of credentials cached in a developer's own
    ~/.api_variables_env (see builds/jira_client.py / builds/firewall_ticket.py)."""

    def test_noop_when_not_requested(self):
        from builds.tasks import _file_firewall_ticket

        build_request = make_build_request(firewall_ticket_requested=False)
        with patch("builds.tasks.create_standard_build_ticket") as mock_create:
            _file_firewall_ticket(build_request)
        mock_create.assert_not_called()

    @override_settings(JIRA_EMAIL="", JIRA_API_TOKEN="")
    def test_records_error_when_credentials_not_configured(self):
        from builds.tasks import _file_firewall_ticket

        build_request = make_build_request(firewall_ticket_requested=True)
        with patch("builds.tasks.create_standard_build_ticket") as mock_create:
            _file_firewall_ticket(build_request)
        mock_create.assert_not_called()
        build_request.refresh_from_db()
        self.assertIn("not configured", build_request.firewall_ticket_error)

    @patch("builds.tasks.create_standard_build_ticket")
    def test_stores_url_on_success(self, mock_create):
        from builds.tasks import _file_firewall_ticket

        mock_create.return_value = "https://jira.example/browse/LABS-1"
        build_request = make_build_request(
            firewall_ticket_requested=True, firewall_ticket_reason="standard build"
        )

        _file_firewall_ticket(build_request)

        mock_create.assert_called_once_with(
            hostname="rcd09-d1-testvm01",
            site="rcd09",
            ip="10.0.0.5",
            reason="standard build",
            email="user@example.com",
            token="tok",
        )
        build_request.refresh_from_db()
        self.assertEqual(
            build_request.firewall_ticket_url, "https://jira.example/browse/LABS-1"
        )

    @patch("builds.tasks.create_standard_build_ticket")
    def test_records_error_without_raising_on_jira_failure(self, mock_create):
        from builds.tasks import _file_firewall_ticket
        from builds.firewall_ticket import FirewallTicketError

        mock_create.side_effect = FirewallTicketError("Jira unreachable")
        build_request = make_build_request(firewall_ticket_requested=True)

        _file_firewall_ticket(build_request)  # must not raise

        build_request.refresh_from_db()
        self.assertEqual(build_request.firewall_ticket_error, "Jira unreachable")
        self.assertEqual(build_request.firewall_ticket_url, "")


@patch("builds.tasks.get_gitlab_api_token", return_value="tok")
class AdvancePipelinesTests(TestCase):
    def test_no_token_does_nothing(self, mock_token):
        mock_token.return_value = None
        make_build_request(gitlab_project_id=7)
        with patch("builds.tasks.get_latest_pipeline") as mock_pipeline:
            from builds.tasks import advance_pipelines

            advance_pipelines()
            mock_pipeline.assert_not_called()

    @patch("builds.tasks.merge_development_branch")
    @patch("builds.tasks.get_latest_pipeline")
    def test_merges_once_dev_plan_succeeds(
        self, mock_pipeline, mock_merge, _mock_token
    ):
        from builds.tasks import advance_pipelines

        mock_pipeline.return_value = {"id": 1, "status": "success"}
        build_request = make_build_request(gitlab_project_id=7)

        advance_pipelines()

        mock_merge.assert_called_once_with(7, "tok")
        build_request.refresh_from_db()
        self.assertIsNotNone(build_request.gitlab_merged_at)

    @patch("builds.tasks.merge_development_branch")
    @patch("builds.tasks.get_latest_pipeline")
    def test_does_not_merge_when_dev_plan_not_successful(
        self, mock_pipeline, mock_merge, _mock_token
    ):
        from builds.tasks import advance_pipelines

        mock_pipeline.return_value = {"id": 1, "status": "running"}
        build_request = make_build_request(gitlab_project_id=7)

        advance_pipelines()

        mock_merge.assert_not_called()
        build_request.refresh_from_db()
        self.assertIsNone(build_request.gitlab_merged_at)

    @patch("builds.tasks.merge_development_branch")
    @patch("builds.tasks.get_latest_pipeline")
    def test_merge_failure_leaves_state_for_retry(
        self, mock_pipeline, mock_merge, _mock_token
    ):
        from builds.tasks import advance_pipelines

        mock_pipeline.return_value = {"id": 1, "status": "success"}
        mock_merge.side_effect = GitLabBuildError("boom")
        build_request = make_build_request(gitlab_project_id=7)

        advance_pipelines()  # must not raise

        build_request.refresh_from_db()
        self.assertIsNone(build_request.gitlab_merged_at)

    @patch("builds.tasks.play_job")
    @patch("builds.tasks.get_pipeline_jobs")
    @patch("builds.tasks.get_latest_pipeline")
    def test_plays_apply_job_once_main_plan_succeeds(
        self, mock_pipeline, mock_jobs, mock_play, _mock_token
    ):
        from django.utils import timezone

        from builds.tasks import advance_pipelines

        mock_pipeline.return_value = {"id": 2, "status": "manual"}
        mock_jobs.return_value = [
            {
                "id": 100,
                "name": "terraform_verify_and_plan_job",
                "status": "success",
            },
            {"id": 101, "name": "terraform_apply_job", "status": "manual"},
        ]
        build_request = make_build_request(
            gitlab_project_id=7, gitlab_merged_at=timezone.now()
        )

        advance_pipelines()

        mock_play.assert_called_once_with(7, 101, "tok")
        build_request.refresh_from_db()
        self.assertIsNotNone(build_request.gitlab_apply_triggered_at)

    @patch("builds.tasks.play_job")
    @patch("builds.tasks.get_pipeline_jobs")
    @patch("builds.tasks.get_latest_pipeline")
    def test_does_not_play_apply_job_when_plan_not_yet_successful(
        self, mock_pipeline, mock_jobs, mock_play, _mock_token
    ):
        from django.utils import timezone

        from builds.tasks import advance_pipelines

        mock_pipeline.return_value = {"id": 2, "status": "running"}
        mock_jobs.return_value = [
            {
                "id": 100,
                "name": "terraform_verify_and_plan_job",
                "status": "running",
            }
        ]
        make_build_request(gitlab_project_id=7, gitlab_merged_at=timezone.now())

        advance_pipelines()

        mock_play.assert_not_called()

    @patch("builds.tasks.play_job")
    @patch("builds.tasks.get_pipeline_jobs")
    @patch("builds.tasks.get_latest_pipeline")
    def test_does_not_reapply_once_already_triggered(
        self, mock_pipeline, mock_jobs, mock_play, _mock_token
    ):
        from django.utils import timezone

        from builds.tasks import advance_pipelines

        make_build_request(
            gitlab_project_id=7,
            gitlab_merged_at=timezone.now(),
            gitlab_apply_triggered_at=timezone.now(),
        )

        advance_pipelines()

        mock_pipeline.assert_not_called()
        mock_play.assert_not_called()

    @patch("builds.tasks.merge_development_branch")
    @patch("builds.tasks.get_latest_pipeline")
    def test_dev_pipeline_failure_marks_plan_failed(
        self, mock_pipeline, mock_merge, _mock_token
    ):
        from builds.tasks import advance_pipelines

        mock_pipeline.return_value = {"id": 1, "status": "failed"}
        build_request = make_build_request(
            gitlab_project_id=7, status=BuildRequest.Status.PLAN_IN_PROGRESS
        )

        advance_pipelines()

        mock_merge.assert_not_called()
        build_request.refresh_from_db()
        self.assertEqual(build_request.status, BuildRequest.Status.PLAN_FAILED)
        self.assertIn("failed", build_request.error_detail)

    @patch("builds.tasks.play_job")
    @patch("builds.tasks.get_pipeline_jobs")
    @patch("builds.tasks.get_latest_pipeline")
    def test_main_plan_job_failure_marks_plan_failed(
        self, mock_pipeline, mock_jobs, mock_play, _mock_token
    ):
        from django.utils import timezone

        from builds.tasks import advance_pipelines

        mock_pipeline.return_value = {"id": 2, "status": "failed"}
        mock_jobs.return_value = [
            {"id": 100, "name": "terraform_verify_and_plan_job", "status": "failed"},
        ]
        build_request = make_build_request(
            gitlab_project_id=7,
            gitlab_merged_at=timezone.now(),
            status=BuildRequest.Status.PLAN_IN_PROGRESS,
        )

        advance_pipelines()

        mock_play.assert_not_called()
        build_request.refresh_from_db()
        self.assertEqual(build_request.status, BuildRequest.Status.PLAN_FAILED)

    @patch("builds.tasks.get_pipeline_jobs")
    @patch("builds.tasks.get_latest_pipeline")
    def test_finalize_marks_complete_once_apply_job_succeeds(
        self, mock_pipeline, mock_jobs, _mock_token
    ):
        from django.utils import timezone

        from builds.tasks import advance_pipelines

        mock_pipeline.return_value = {"id": 2, "status": "success"}
        mock_jobs.return_value = [
            {"id": 101, "name": "terraform_apply_job", "status": "success"},
        ]
        build_request = make_build_request(
            gitlab_project_id=7,
            gitlab_merged_at=timezone.now(),
            gitlab_apply_triggered_at=timezone.now(),
            status=BuildRequest.Status.APPLY_IN_PROGRESS,
        )

        advance_pipelines()

        build_request.refresh_from_db()
        self.assertEqual(build_request.status, BuildRequest.Status.COMPLETE)

    @patch("builds.tasks.get_pipeline_jobs")
    @patch("builds.tasks.get_latest_pipeline")
    def test_finalize_marks_apply_failed_once_apply_job_fails(
        self, mock_pipeline, mock_jobs, _mock_token
    ):
        from django.utils import timezone

        from builds.tasks import advance_pipelines

        mock_pipeline.return_value = {"id": 2, "status": "failed"}
        mock_jobs.return_value = [
            {"id": 101, "name": "terraform_apply_job", "status": "failed"},
        ]
        build_request = make_build_request(
            gitlab_project_id=7,
            gitlab_merged_at=timezone.now(),
            gitlab_apply_triggered_at=timezone.now(),
            status=BuildRequest.Status.APPLY_IN_PROGRESS,
        )

        advance_pipelines()

        build_request.refresh_from_db()
        self.assertEqual(build_request.status, BuildRequest.Status.APPLY_FAILED)

    @patch("builds.tasks.get_pipeline_jobs")
    @patch("builds.tasks.get_latest_pipeline")
    def test_finalize_does_not_run_once_already_terminal(
        self, mock_pipeline, mock_jobs, _mock_token
    ):
        from django.utils import timezone

        from builds.tasks import advance_pipelines

        make_build_request(
            gitlab_project_id=7,
            gitlab_merged_at=timezone.now(),
            gitlab_apply_triggered_at=timezone.now(),
            status=BuildRequest.Status.COMPLETE,
        )

        advance_pipelines()

        mock_pipeline.assert_not_called()
