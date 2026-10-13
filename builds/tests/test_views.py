import csv
import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from builds.models import BuildRequest


class AuthenticatedTestCase(TestCase):
    """LoginRequiredMiddleware (see casbah/settings.py) gates every view now, so tests
    need a logged-in test client rather than exercising the real AD backend."""

    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_user(username="tester")
        self.client.force_login(user)


VCENTER_INVENTORY = {
    "datastores": ["infra-01"],
    "clusters": ["rcd09-infrastructure"],
    "resource_pools": ["utilities"],
    "folders": ["infrastructure"],
    "portgroups": ["/rcd09/network/rcd09-infra-vm-sw/rcd09-infra-servers"],
}
LATEST_TEMPLATE = "rcd09-c1-vmt-ubuntu2404-260721-1206"


def patch_form_lookups(
    vm_name_exists=False,
    build_repo_name_exists=False,
    ip_address_exists=False,
    local_build_repo_exists=False,
):
    """Patches the external lookups builds.forms.ServerBuildForm makes for its real
    (non-deferred) choice population + submit-time uniqueness checks."""
    return [
        patch("builds.forms.get_vcenter_inventory", return_value=VCENTER_INVENTORY),
        patch("builds.forms.get_latest_ubuntu_template", return_value=LATEST_TEMPLATE),
        patch("builds.forms.get_all_tenant_group_names", return_value=["HTD"]),
        patch("builds.forms.get_all_tenant_names", return_value=["Acme Corp"]),
        patch(
            "builds.forms.find_vm_url",
            return_value="https://netbox.example/vm" if vm_name_exists else None,
        ),
        patch(
            "builds.forms.find_build_repo_url",
            return_value=(
                "https://gitlab.example/vms/x" if build_repo_name_exists else None
            ),
        ),
        patch("builds.forms.ip_address_exists", return_value=ip_address_exists),
        patch(
            "builds.forms.local_build_repo_path",
            return_value="/builds/x" if local_build_repo_exists else None,
        ),
    ]


def valid_post_data(**overrides):
    data = {
        "vsphere_vm_name": "rcd09-d1-testvm01",
        "vsphere_vm_ipv4_address": "10.192.5.5",
        "vsphere_vm_ipv4_prefix_length": "24",
        "vsphere_vm_primary_vnic": "ens192",
        "vsphere_vm_num_cpus": "2",
        "vsphere_vm_memory": "8",
        "tenant_group": "HTD",
        "tenant": "VMCloud Prod",
        "tenant_other": "",
        "vsphere_datastore": "infra-01",
        "vsphere_compute_cluster": "rcd09-infrastructure",
        "vsphere_resource_pool": "utilities",
        "vsphere_vm_folder": "infrastructure",
        "vsphere_portgroup_name": "/rcd09/network/rcd09-infra-vm-sw/rcd09-infra-servers",
        "vsphere_vm_template": LATEST_TEMPLATE,
        "firewall_ticket_requested": "yes",
        "firewall_ticket_reason": "standard build",
    }
    data.update(overrides)
    return data


class LandingViewTests(AuthenticatedTestCase):
    def test_renders_landing_page(self):
        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "builds/landing.html")


class BuildFormGetTests(AuthenticatedTestCase):
    def test_valid_site_renders_form_without_hitting_vcenter_or_netbox(self):
        with patch("builds.forms.get_vcenter_inventory") as mock_inventory:
            response = self.client.get(reverse("build_form", args=["rcd09"]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "builds/build_form.html")
        mock_inventory.assert_not_called()

    def test_unknown_site_404s(self):
        response = self.client.get(reverse("build_form", args=["unknown"]))
        self.assertEqual(response.status_code, 404)

    def test_staging_form_renders(self):
        response = self.client.get(reverse("build_form_staging"))
        self.assertEqual(response.status_code, 200)


class BuildFormPostTests(AuthenticatedTestCase):
    def _post(self, data, site="rcd09"):
        patchers = patch_form_lookups()
        for p in patchers:
            self.enterContext(p)
        with patch("builds.views.async_task") as mock_async_task:
            response = self.client.post(reverse("build_form", args=[site]), data)
        return response, mock_async_task

    def test_valid_submission_creates_build_request_and_queues_pipeline(self):
        response, mock_async_task = self._post(valid_post_data())

        build_request = BuildRequest.objects.get()
        self.assertRedirects(
            response, reverse("build_submitted", args=[build_request.id])
        )
        self.assertEqual(build_request.vm_name, "rcd09-d1-testvm01")
        self.assertEqual(build_request.site, "rcd09")
        self.assertEqual(build_request.tenant, "VMCloud Prod")
        mock_async_task.assert_called_once_with(
            "builds.tasks.run_build_pipeline", build_request.id
        )

    def test_invalid_submission_rerenders_form_without_creating_or_queueing(self):
        response, mock_async_task = self._post(
            valid_post_data(vsphere_vm_name="bad-name-format")
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(BuildRequest.objects.exists())
        mock_async_task.assert_not_called()

    def test_tenant_other_selection_is_stored_as_tenant(self):
        response, _mock_async_task = self._post(
            valid_post_data(tenant="VMCloud Prod", tenant_other="Acme Corp")
        )
        build_request = BuildRequest.objects.get()
        self.assertEqual(build_request.tenant, "Acme Corp")


class BuildSubmittedViewTests(AuthenticatedTestCase):
    def test_renders_existing_build_request(self):
        build_request = BuildRequest.objects.create(
            vm_name="rcd09-d1-testvm01",
            site=BuildRequest.Site.RCD09,
            ipv4_address="10.0.0.5",
            tenant="VMCloud Dev",
        )
        response = self.client.get(reverse("build_submitted", args=[build_request.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["build_request"], build_request)

    def test_missing_build_request_404s(self):
        response = self.client.get(reverse("build_submitted", args=[999999]))
        self.assertEqual(response.status_code, 404)


class NetboxOptionsPrefetchTests(AuthenticatedTestCase):
    @patch("builds.views.get_all_tenant_names")
    @patch("builds.views.get_all_tenant_group_names")
    def test_warms_caches_and_returns_no_content(self, mock_groups, mock_names):
        response = self.client.get(reverse("netbox_options_prefetch"))
        self.assertEqual(response.status_code, 204)
        mock_groups.assert_called_once()
        mock_names.assert_called_once()


class BuildFormOptionsTests(AuthenticatedTestCase):
    def test_renders_fragment_with_no_store_cache_header(self):
        patchers = patch_form_lookups()
        for p in patchers:
            self.enterContext(p)
        response = self.client.get(reverse("build_form_options", args=["rcd09"]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "builds/_build_form_options.html")
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_unknown_site_404s(self):
        response = self.client.get(reverse("build_form_options", args=["unknown"]))
        self.assertEqual(response.status_code, 404)


class ValidateVmNameTests(AuthenticatedTestCase):
    def _get(self, name, site="rcd09"):
        return self.client.get(
            reverse("validate_vm_name", args=[site]), {"vsphere_vm_name": name}
        )

    def test_blank_name_returns_empty_response(self):
        response = self._get("")
        self.assertEqual(response.content, b"")

    def test_bad_format_is_rejected(self):
        response = self._get("not-a-valid-name")
        self.assertIn(b"\xe2\x9c\x97", response.content)  # ✗

    @patch("builds.views.local_build_repo_path", return_value=None)
    @patch("builds.views.find_build_repo_url", return_value=None)
    @patch("builds.views.find_vm_url", return_value=None)
    def test_available_name_is_accepted(self, _mock_vm, _mock_repo, _mock_local):
        response = self._get("rcd09-d1-testvm01")
        self.assertIn(b"\xe2\x9c\x93", response.content)  # check mark

    @patch("builds.views.local_build_repo_path", return_value=None)
    @patch("builds.views.find_build_repo_url", return_value=None)
    @patch("builds.views.find_vm_url", return_value=None)
    def test_name_already_used_by_another_casbah_build(
        self, _mock_vm, _mock_repo, _mock_local
    ):
        build_request = BuildRequest.objects.create(
            vm_name="rcd09-d1-testvm01",
            site=BuildRequest.Site.RCD09,
            ipv4_address="10.0.0.9",
            tenant="VMCloud Dev",
        )
        response = self._get("rcd09-d1-testvm01")
        self.assertIn(b"Already exists in", response.content)
        self.assertIn(b"CASBAH build history", response.content)
        self.assertIn(
            reverse("build_submitted", args=[build_request.pk]).encode(),
            response.content,
        )

    @patch("builds.views.local_build_repo_path", return_value=None)
    @patch("builds.views.find_build_repo_url", return_value=None)
    @patch(
        "builds.views.find_vm_url",
        return_value="https://netbox.example/virtualization/virtual-machines/?name=rcd09-d1-testvm01",
    )
    def test_name_exists_in_netbox(self, mock_vm, _mock_repo, _mock_local):
        response = self._get("rcd09-d1-testvm01")
        self.assertIn(b"NetBox", response.content)
        self.assertIn(b"netbox.example", response.content)

    @patch("builds.views.local_build_repo_path", return_value=None)
    @patch(
        "builds.views.find_build_repo_url",
        return_value="https://gitlab.example/vms/rcd09-d1-testvm01",
    )
    @patch("builds.views.find_vm_url", return_value=None)
    def test_name_exists_in_gitlab(self, _mock_vm, mock_repo, _mock_local):
        response = self._get("rcd09-d1-testvm01")
        self.assertIn(b"GitLab", response.content)
        self.assertIn(b"gitlab.example", response.content)

    @patch(
        "builds.views.local_build_repo_path", return_value="/builds/rcd09-d1-testvm01"
    )
    @patch("builds.views.find_build_repo_url", return_value=None)
    @patch("builds.views.find_vm_url", return_value=None)
    def test_name_already_has_local_build_copy(self, _mock_vm, _mock_repo, mock_local):
        response = self._get("rcd09-d1-testvm01")
        self.assertIn(b"local build dir", response.content)
        self.assertIn(b"file:///builds/rcd09-d1-testvm01", response.content)

    @patch(
        "builds.views.local_build_repo_path", return_value="/builds/rcd09-d1-testvm01"
    )
    @patch(
        "builds.views.find_build_repo_url",
        return_value="https://gitlab.example/vms/rcd09-d1-testvm01",
    )
    @patch(
        "builds.views.find_vm_url",
        return_value="https://netbox.example/virtualization/virtual-machines/?name=rcd09-d1-testvm01",
    )
    def test_shows_all_conflicting_locations_at_once(
        self, mock_vm, mock_repo, mock_local
    ):
        response = self._get("rcd09-d1-testvm01")
        self.assertIn(b"NetBox", response.content)
        self.assertIn(b"GitLab", response.content)
        self.assertIn(b"local build dir", response.content)


class ValidateIpTests(AuthenticatedTestCase):
    def _get(self, value):
        return self.client.get(
            reverse("validate_ip", args=["rcd09"]), {"vsphere_vm_ipv4_address": value}
        )

    def test_blank_returns_empty_response(self):
        response = self._get("")
        self.assertEqual(response.content, b"")

    def test_invalid_format_is_rejected(self):
        response = self._get("not-an-ip")
        self.assertIn(b"Not a valid IPv4 address", response.content)

    def test_already_used_by_another_casbah_build(self):
        BuildRequest.objects.create(
            vm_name="rcd09-d1-other",
            site=BuildRequest.Site.RCD09,
            ipv4_address="10.0.0.9",
            tenant="VMCloud Dev",
        )
        response = self._get("10.0.0.9")
        self.assertIn(b"Already used by another CASBAH build", response.content)

    @patch("builds.views.ip_address_exists", return_value=True)
    def test_already_in_use_in_netbox(self, _mock_ip):
        response = self._get("10.0.0.9")
        self.assertIn(b"Already in use in NetBox", response.content)

    @patch("builds.views.ip_address_exists", return_value=False)
    def test_available_address_is_accepted(self, _mock_ip):
        response = self._get("10.0.0.9")
        self.assertIn(b"Address is available", response.content)


class BuildPipelineStatusTests(AuthenticatedTestCase):
    def _make_build(
        self,
        gitlab_project_id=None,
        gitlab_merged_at=None,
        gitlab_apply_triggered_at=None,
        status=BuildRequest.Status.COMPLETE,
    ):
        return BuildRequest.objects.create(
            vm_name="rcd09-d1-testvm01",
            site=BuildRequest.Site.RCD09,
            ipv4_address="10.0.0.5",
            tenant="VMCloud Dev",
            gitlab_project_id=gitlab_project_id,
            gitlab_merged_at=gitlab_merged_at,
            gitlab_apply_triggered_at=gitlab_apply_triggered_at,
            status=status,
        )

    def test_step_one_still_in_progress_keeps_polling(self):
        build_request = self._make_build(
            gitlab_project_id=None, status=BuildRequest.Status.GITLAB_IN_PROGRESS
        )
        response = self.client.get(
            reverse("build_pipeline_status", args=[build_request.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["pipeline_terminal"])
        self.assertIn(b"Create GitLab repo", response.content)
        self.assertIn(b"Fetching status", response.content)

    def test_gitlab_failed_stops_polling_at_step_one(self):
        # Nothing else can ever happen -- no gitlab_project_id was ever set.
        build_request = self._make_build(
            gitlab_project_id=None, status=BuildRequest.Status.GITLAB_FAILED
        )
        response = self.client.get(
            reverse("build_pipeline_status", args=[build_request.id])
        )
        self.assertTrue(response.context["pipeline_terminal"])
        self.assertEqual(len(response.context["steps"]), 1)
        self.assertNotIn(b"Fetching status", response.content)

    @patch("builds.views.get_latest_pipeline", return_value=None)
    @patch("builds.views.get_gitlab_api_token", return_value=None)
    def test_unreachable_gitlab_does_not_error(self, _mock_token, _mock_pipeline):
        build_request = self._make_build(gitlab_project_id=7)
        response = self.client.get(
            reverse("build_pipeline_status", args=[build_request.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["pipeline_terminal"])

    @patch("builds.views.get_pipeline_jobs")
    @patch("builds.views.get_latest_pipeline")
    @patch("builds.views.get_gitlab_api_token", return_value="tok")
    def test_running_dev_pipeline_keeps_polling(
        self, _mock_token, mock_pipeline, mock_jobs
    ):
        mock_pipeline.return_value = {
            "id": 42,
            "status": "running",
            "web_url": "https://gitlab.example/pipelines/42",
        }
        mock_jobs.return_value = [
            {
                "name": "terraform_verify_and_plan_job",
                "stage": "verify_and_plan",
                "status": "running",
            }
        ]
        build_request = self._make_build(gitlab_project_id=7)
        response = self.client.get(
            reverse("build_pipeline_status", args=[build_request.id])
        )
        self.assertFalse(response.context["pipeline_terminal"])
        self.assertIn(b"running", response.content)
        self.assertIn(b"Fetching status", response.content)

    @patch("builds.views.get_pipeline_jobs", return_value=[])
    @patch("builds.views.get_latest_pipeline")
    @patch("builds.views.get_gitlab_api_token", return_value="tok")
    def test_dev_pipeline_failed_stops_polling(
        self, _mock_token, mock_pipeline, _mock_jobs
    ):
        # A bad terminal status on development's own plan job means nothing more
        # happens automatically (advance_pipelines never merges a failed plan).
        mock_pipeline.return_value = {
            "id": 42,
            "status": "failed",
            "web_url": "https://gitlab.example/pipelines/42",
        }
        build_request = self._make_build(gitlab_project_id=7)
        response = self.client.get(
            reverse("build_pipeline_status", args=[build_request.id])
        )
        self.assertTrue(response.context["pipeline_terminal"])
        self.assertNotIn(b"Fetching status", response.content)

    @patch("builds.views.get_pipeline_jobs", return_value=[])
    @patch("builds.views.get_latest_pipeline")
    @patch("builds.views.get_gitlab_api_token", return_value="tok")
    def test_dev_pipeline_succeeded_but_not_merged_keeps_polling(
        self, _mock_token, mock_pipeline, _mock_jobs
    ):
        # Success alone isn't terminal anymore -- still waiting on advance_pipelines
        # to notice and auto-merge development into main.
        mock_pipeline.return_value = {
            "id": 42,
            "status": "success",
            "web_url": "https://gitlab.example/pipelines/42",
        }
        build_request = self._make_build(gitlab_project_id=7)
        response = self.client.get(
            reverse("build_pipeline_status", args=[build_request.id])
        )
        self.assertFalse(response.context["pipeline_terminal"])
        self.assertIn(b"Auto-merge", response.content)

    @patch("builds.views.get_pipeline_jobs")
    @patch("builds.views.get_latest_pipeline")
    @patch("builds.views.get_gitlab_api_token", return_value="tok")
    def test_merged_waiting_on_main_plan_keeps_polling(
        self, _mock_token, mock_pipeline, mock_jobs
    ):
        dev_pipeline = {
            "id": 42,
            "status": "success",
            "web_url": "https://gitlab.example/pipelines/42",
        }
        main_pipeline = {
            "id": 43,
            "status": "running",
            "web_url": "https://gitlab.example/pipelines/43",
        }
        mock_pipeline.side_effect = [dev_pipeline, main_pipeline]
        mock_jobs.return_value = [
            {
                "name": "terraform_verify_and_plan_job",
                "stage": "verify_and_plan",
                "status": "running",
            }
        ]
        build_request = self._make_build(
            gitlab_project_id=7, gitlab_merged_at=timezone.now()
        )
        response = self.client.get(
            reverse("build_pipeline_status", args=[build_request.id])
        )
        self.assertFalse(response.context["pipeline_terminal"])
        self.assertIn(b"Plan (main, post-merge)", response.content)

    @patch("builds.views.get_pipeline_jobs")
    @patch("builds.views.get_latest_pipeline")
    @patch("builds.views.get_gitlab_api_token", return_value="tok")
    def test_main_plan_succeeded_waiting_on_auto_apply_keeps_polling(
        self, _mock_token, mock_pipeline, mock_jobs
    ):
        dev_pipeline = {
            "id": 42,
            "status": "success",
            "web_url": "https://gitlab.example/pipelines/42",
        }
        main_pipeline = {
            "id": 43,
            "status": "manual",
            "web_url": "https://gitlab.example/pipelines/43",
        }
        mock_pipeline.side_effect = [dev_pipeline, main_pipeline]
        mock_jobs.return_value = [
            {
                "name": "terraform_verify_and_plan_job",
                "stage": "verify_and_plan",
                "status": "success",
            }
        ]
        build_request = self._make_build(
            gitlab_project_id=7, gitlab_merged_at=timezone.now()
        )
        response = self.client.get(
            reverse("build_pipeline_status", args=[build_request.id])
        )
        self.assertFalse(response.context["pipeline_terminal"])
        self.assertIn(
            b"waiting for the background poller to trigger it", response.content
        )

    @patch("builds.views.get_pipeline_jobs")
    @patch("builds.views.get_latest_pipeline")
    @patch("builds.views.get_gitlab_api_token", return_value="tok")
    def test_apply_triggered_and_finished_stops_polling(
        self, _mock_token, mock_pipeline, mock_jobs
    ):
        dev_pipeline = {
            "id": 42,
            "status": "success",
            "web_url": "https://gitlab.example/pipelines/42",
        }
        main_pipeline = {
            "id": 43,
            "status": "success",
            "web_url": "https://gitlab.example/pipelines/43",
        }
        mock_pipeline.side_effect = [dev_pipeline, main_pipeline]
        mock_jobs.return_value = [
            {
                "name": "terraform_verify_and_plan_job",
                "stage": "verify_and_plan",
                "status": "success",
            },
            {"name": "terraform_apply_job", "stage": "apply", "status": "success"},
        ]
        build_request = self._make_build(
            gitlab_project_id=7,
            gitlab_merged_at=timezone.now(),
            gitlab_apply_triggered_at=timezone.now(),
        )
        response = self.client.get(
            reverse("build_pipeline_status", args=[build_request.id])
        )
        self.assertTrue(response.context["pipeline_terminal"])
        self.assertNotIn(b"Fetching status", response.content)

    def test_missing_build_request_404s(self):
        response = self.client.get(reverse("build_pipeline_status", args=[999999]))
        self.assertEqual(response.status_code, 404)


class BuildPreviewTests(AuthenticatedTestCase):
    @patch("builds.views.check_datastore_capacity_warning", return_value=None)
    def test_renders_confirm_modal(self, _mock_warning):
        response = self.client.post(
            reverse("build_preview", args=["rcd09"]), valid_post_data()
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "builds/_confirm_modal.html")
        self.assertIn(
            "vsphere_vm_name: rcd09-d1-testvm01", response.context["yaml_text"]
        )

    @patch("builds.views.check_datastore_capacity_warning", return_value="low on space")
    def test_surfaces_capacity_warning(self, _mock_warning):
        response = self.client.post(
            reverse("build_preview", args=["rcd09"]), valid_post_data()
        )
        self.assertEqual(response.context["capacity_warning"], "low on space")

    def test_unknown_site_404s(self):
        response = self.client.post(reverse("build_preview", args=["unknown"]), {})
        self.assertEqual(response.status_code, 404)


class BuildListTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.rcd09_dev = BuildRequest.objects.create(
            vm_name="rcd09-d1-dev01",
            site=BuildRequest.Site.RCD09,
            ipv4_address="10.0.0.1",
            tenant="VMCloud Dev",
            status=BuildRequest.Status.COMPLETE,
        )
        self.sjc17_prod = BuildRequest.objects.create(
            vm_name="sjc17-d1-prod01",
            site=BuildRequest.Site.SJC17,
            ipv4_address="10.0.0.2",
            tenant="VMCloud Prod",
            status=BuildRequest.Status.PENDING,
        )

    def test_lists_all_builds_by_default(self):
        response = self.client.get(reverse("build_list"))
        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(
            response.context["builds"], [self.rcd09_dev, self.sjc17_prod]
        )

    def test_filters_by_site(self):
        response = self.client.get(reverse("build_list"), {"site": "rcd09"})
        self.assertEqual(list(response.context["builds"]), [self.rcd09_dev])

    def test_filters_by_tenant(self):
        response = self.client.get(reverse("build_list"), {"tenant": "VMCloud Prod"})
        self.assertEqual(list(response.context["builds"]), [self.sjc17_prod])

    def test_filters_by_status(self):
        response = self.client.get(reverse("build_list"), {"status": "complete"})
        self.assertEqual(list(response.context["builds"]), [self.rcd09_dev])


class BuildListCsvTests(AuthenticatedTestCase):
    def test_exports_expected_rows(self):
        BuildRequest.objects.create(
            vm_name="rcd09-d1-dev01",
            site=BuildRequest.Site.RCD09,
            ipv4_address="10.0.0.1",
            tenant="VMCloud Dev",
            requested_by="jdoe",
        )
        response = self.client.get(reverse("build_list_csv"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(
            rows[0],
            [
                "vm_name",
                "site",
                "tenant",
                "status",
                "ipv4_address",
                "requested_by",
                "created_at",
            ],
        )
        self.assertEqual(rows[1][0], "rcd09-d1-dev01")
        self.assertEqual(rows[1][5], "jdoe")

    def test_respects_filters(self):
        BuildRequest.objects.create(
            vm_name="rcd09-d1-dev01",
            site=BuildRequest.Site.RCD09,
            ipv4_address="10.0.0.1",
            tenant="VMCloud Dev",
        )
        BuildRequest.objects.create(
            vm_name="sjc17-d1-prod01",
            site=BuildRequest.Site.SJC17,
            ipv4_address="10.0.0.2",
            tenant="VMCloud Prod",
        )
        response = self.client.get(reverse("build_list_csv"), {"site": "sjc17"})
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "sjc17-d1-prod01")


class LoginRequiredGateTests(TestCase):
    """Deliberately doesn't inherit AuthenticatedTestCase — this confirms the login
    gate (LoginRequiredMiddleware, casbah/settings.py) actually works."""

    def test_unauthenticated_request_redirects_to_login(self):
        response = self.client.get(reverse("landing"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('landing')}")

    def test_login_page_itself_is_reachable_without_auth(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
