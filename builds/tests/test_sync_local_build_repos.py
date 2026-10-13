from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from io import StringIO

from builds.gitlab_client import GitLabBuildError
from builds.models import BuildRequest


def _run_command():
    out, err = StringIO(), StringIO()
    call_command("sync_local_build_repos", stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


class SyncLocalBuildReposCommandTests(TestCase):
    def setUp(self):
        import shutil as shutil_mod
        import tempfile as tempfile_mod

        self.builds_dir = tempfile_mod.mkdtemp()
        self.addCleanup(lambda: shutil_mod.rmtree(self.builds_dir, ignore_errors=True))
        self.enterContext(override_settings(VM_BUILDS_DIR=self.builds_dir))

    def _make_build(self, vm_name, project_id=7):
        return BuildRequest.objects.create(
            vm_name=vm_name,
            site=BuildRequest.Site.RCD09,
            ipv4_address="10.0.0.5",
            tenant="VMCloud Dev",
            gitlab_project_id=project_id,
        )

    def test_skips_builds_without_gitlab_project_id(self):
        BuildRequest.objects.create(
            vm_name="rcd09-d1-no-project",
            site=BuildRequest.Site.RCD09,
            ipv4_address="10.0.0.6",
            tenant="VMCloud Dev",
        )
        with patch(
            "builds.management.commands.sync_local_build_repos.clone_existing_build_repo"
        ) as mock_clone:
            out, _ = _run_command()
        mock_clone.assert_not_called()
        self.assertIn("0 cloned, 0 already present, 0 failed", out)

    def test_skips_builds_with_existing_local_dir(self):
        build_request = self._make_build("rcd09-d1-testvm01")
        (Path(self.builds_dir) / build_request.vm_name).mkdir()
        with patch(
            "builds.management.commands.sync_local_build_repos.clone_existing_build_repo"
        ) as mock_clone:
            out, _ = _run_command()
        mock_clone.assert_not_called()
        self.assertIn("1 already present", out)

    def test_clones_missing_build(self):
        build_request = self._make_build("rcd09-d1-testvm01")
        with patch(
            "builds.management.commands.sync_local_build_repos.clone_existing_build_repo"
        ) as mock_clone:
            out, _ = _run_command()
        mock_clone.assert_called_once_with(build_request.vm_name, 7)
        self.assertIn("1 cloned", out)

    def test_reports_failure_without_stopping_other_builds(self):
        self._make_build("rcd09-d1-testvm01", project_id=7)
        self._make_build("rcd09-d1-testvm02", project_id=8)
        with patch(
            "builds.management.commands.sync_local_build_repos.clone_existing_build_repo",
            side_effect=[GitLabBuildError("boom"), None],
        ) as mock_clone:
            out, err = _run_command()
        self.assertEqual(mock_clone.call_count, 2)
        self.assertIn("boom", err)
        self.assertIn("1 cloned, 0 already present, 1 failed", out)

    def test_errors_when_vm_builds_dir_not_configured(self):
        with override_settings(VM_BUILDS_DIR=""):
            out, err = _run_command()
        self.assertIn("VM_BUILDS_DIR is not configured", err)
