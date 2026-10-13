from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from builds import gitlab_client
from builds.gitlab_client import GitLabBuildError


def _response(status_code, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


@override_settings(GITLAB_URL="https://gitlab.example")
class NamespaceLookupTests(SimpleTestCase):
    @patch("builds.gitlab_client.requests.request")
    def test_get_namespace_id_prefers_lookup_by_id(self, mock_request):
        mock_request.return_value = _response(200, {"id": 600})
        self.assertEqual(gitlab_client.get_namespace_id("tok"), 600)
        mock_request.assert_called_once()
        self.assertIn(
            f"/groups/{gitlab_client.VMS_NAMESPACE_ID}", mock_request.call_args[0][1]
        )

    @patch("builds.gitlab_client.requests.request")
    def test_get_namespace_id_falls_back_to_path_lookup(self, mock_request):
        mock_request.side_effect = [
            _response(404, text="not found"),
            _response(200, {"id": 601}),
        ]
        self.assertEqual(gitlab_client.get_namespace_id("tok"), 601)

    @patch("builds.gitlab_client.requests.request")
    def test_get_namespace_id_raises_when_both_lookups_fail(self, mock_request):
        mock_request.side_effect = [_response(404), _response(500, text="boom")]
        with self.assertRaises(GitLabBuildError):
            gitlab_client.get_namespace_id("tok")


@override_settings(GITLAB_URL="https://gitlab.example")
class ProjectExistsTests(SimpleTestCase):
    @patch("builds.gitlab_client.requests.request")
    def test_true_when_name_matches(self, mock_request):
        mock_request.return_value = _response(200, [{"name": "rcd09-d1-testvm01"}])
        self.assertTrue(gitlab_client.project_exists("rcd09-d1-testvm01", 600, "tok"))

    @patch("builds.gitlab_client.requests.request")
    def test_false_when_no_match(self, mock_request):
        mock_request.return_value = _response(200, [])
        self.assertFalse(gitlab_client.project_exists("rcd09-d1-testvm01", 600, "tok"))

    @patch("builds.gitlab_client.requests.request")
    def test_raises_on_non_200(self, mock_request):
        mock_request.return_value = _response(500, text="boom")
        with self.assertRaises(GitLabBuildError):
            gitlab_client.project_exists("rcd09-d1-testvm01", 600, "tok")


@override_settings(GITLAB_URL="https://gitlab.example")
class FindProjectTests(SimpleTestCase):
    @patch("builds.gitlab_client.requests.request")
    def test_returns_matching_project(self, mock_request):
        mock_request.return_value = _response(
            200,
            [{"name": "rcd09-d1-testvm01", "web_url": "https://gitlab.example/x"}],
        )
        project = gitlab_client.find_project("rcd09-d1-testvm01", 600, "tok")
        self.assertEqual(project["web_url"], "https://gitlab.example/x")

    @patch("builds.gitlab_client.requests.request")
    def test_none_when_no_match(self, mock_request):
        mock_request.return_value = _response(200, [])
        self.assertIsNone(gitlab_client.find_project("rcd09-d1-testvm01", 600, "tok"))

    @patch("builds.gitlab_client.requests.request")
    def test_raises_on_non_200(self, mock_request):
        mock_request.return_value = _response(500, text="boom")
        with self.assertRaises(GitLabBuildError):
            gitlab_client.find_project("rcd09-d1-testvm01", 600, "tok")


class FindBuildRepoUrlTests(SimpleTestCase):
    @patch("builds.gitlab_client.get_gitlab_api_token")
    def test_none_when_no_token(self, mock_get_token):
        mock_get_token.return_value = None
        self.assertIsNone(gitlab_client.find_build_repo_url("rcd09-d1-testvm01"))

    @patch("builds.gitlab_client.find_project")
    @patch("builds.gitlab_client.get_namespace_id")
    @patch("builds.gitlab_client.get_gitlab_api_token")
    def test_returns_web_url_when_found(
        self, mock_get_token, mock_namespace, mock_find
    ):
        mock_get_token.return_value = "tok"
        mock_namespace.return_value = 600
        mock_find.return_value = {"web_url": "https://gitlab.example/vms/x"}
        self.assertEqual(
            gitlab_client.find_build_repo_url("rcd09-d1-testvm01"),
            "https://gitlab.example/vms/x",
        )

    @patch("builds.gitlab_client.find_project")
    @patch("builds.gitlab_client.get_namespace_id")
    @patch("builds.gitlab_client.get_gitlab_api_token")
    def test_none_when_not_found(self, mock_get_token, mock_namespace, mock_find):
        mock_get_token.return_value = "tok"
        mock_namespace.return_value = 600
        mock_find.return_value = None
        self.assertIsNone(gitlab_client.find_build_repo_url("rcd09-d1-testvm01"))

    @patch("builds.gitlab_client.get_namespace_id")
    @patch("builds.gitlab_client.get_gitlab_api_token")
    def test_none_when_gitlab_unreachable(self, mock_get_token, mock_namespace):
        mock_get_token.return_value = "tok"
        mock_namespace.side_effect = GitLabBuildError("down")
        self.assertIsNone(gitlab_client.find_build_repo_url("rcd09-d1-testvm01"))


class BuildRepoNameExistsTests(SimpleTestCase):
    @patch("builds.gitlab_client.get_gitlab_api_token")
    def test_none_when_no_token(self, mock_get_token):
        mock_get_token.return_value = None
        self.assertIsNone(gitlab_client.build_repo_name_exists("rcd09-d1-testvm01"))

    @patch("builds.gitlab_client.project_exists")
    @patch("builds.gitlab_client.get_namespace_id")
    @patch("builds.gitlab_client.get_gitlab_api_token")
    def test_delegates_to_project_exists(
        self, mock_get_token, mock_namespace, mock_exists
    ):
        mock_get_token.return_value = "tok"
        mock_namespace.return_value = 600
        mock_exists.return_value = True
        self.assertTrue(gitlab_client.build_repo_name_exists("rcd09-d1-testvm01"))

    @patch("builds.gitlab_client.get_namespace_id")
    @patch("builds.gitlab_client.get_gitlab_api_token")
    def test_none_when_gitlab_unreachable(self, mock_get_token, mock_namespace):
        mock_get_token.return_value = "tok"
        mock_namespace.side_effect = GitLabBuildError("down")
        self.assertIsNone(gitlab_client.build_repo_name_exists("rcd09-d1-testvm01"))


class LocalBuildRepoPathTests(SimpleTestCase):
    def test_none_when_vm_builds_dir_not_configured(self):
        with override_settings(VM_BUILDS_DIR=""):
            self.assertIsNone(gitlab_client.local_build_repo_path("rcd09-d1-testvm01"))

    def test_none_when_directory_missing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(VM_BUILDS_DIR=tmpdir):
                self.assertIsNone(
                    gitlab_client.local_build_repo_path("rcd09-d1-testvm01")
                )

    def test_returns_path_when_present(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "rcd09-d1-testvm01").mkdir()
            with override_settings(VM_BUILDS_DIR=tmpdir):
                result = gitlab_client.local_build_repo_path("rcd09-d1-testvm01")
                self.assertEqual(result, Path(tmpdir) / "rcd09-d1-testvm01")


class LocalBuildRepoExistsTests(SimpleTestCase):
    def test_false_when_vm_builds_dir_not_configured(self):
        with override_settings(VM_BUILDS_DIR=""):
            self.assertFalse(gitlab_client.local_build_repo_exists("rcd09-d1-testvm01"))

    def test_false_when_directory_missing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(VM_BUILDS_DIR=tmpdir):
                self.assertFalse(
                    gitlab_client.local_build_repo_exists("rcd09-d1-testvm01")
                )

    def test_true_when_directory_present(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "rcd09-d1-testvm01").mkdir()
            with override_settings(VM_BUILDS_DIR=tmpdir):
                self.assertTrue(
                    gitlab_client.local_build_repo_exists("rcd09-d1-testvm01")
                )


@override_settings(GITLAB_URL="https://gitlab.example")
class CreateProjectTests(SimpleTestCase):
    @patch("builds.gitlab_client.requests.request")
    def test_success_returns_project_json(self, mock_request):
        mock_request.return_value = _response(
            201, {"id": 7, "name": "rcd09-d1-testvm01"}
        )
        project = gitlab_client.create_project("rcd09-d1-testvm01", 600, "tok")
        self.assertEqual(project["id"], 7)
        self.assertEqual(
            mock_request.call_args.kwargs["data"]["initialize_with_readme"], "true"
        )

    @patch("builds.gitlab_client.requests.request")
    def test_raises_on_failure(self, mock_request):
        mock_request.return_value = _response(400, text="bad")
        with self.assertRaises(GitLabBuildError):
            gitlab_client.create_project("rcd09-d1-testvm01", 600, "tok")


@override_settings(GITLAB_URL="https://gitlab.example")
class GetCurrentUsernameTests(SimpleTestCase):
    @patch("builds.gitlab_client.requests.request")
    def test_success_returns_username(self, mock_request):
        mock_request.return_value = _response(200, {"username": "service_gitlab"})
        self.assertEqual(gitlab_client.get_current_username("tok"), "service_gitlab")

    @patch("builds.gitlab_client.requests.request")
    def test_raises_on_failure(self, mock_request):
        mock_request.return_value = _response(401, text="unauthorized")
        with self.assertRaises(GitLabBuildError):
            gitlab_client.get_current_username("tok")


class GitAuthArgsTests(SimpleTestCase):
    def test_encodes_username_and_token_as_basic_auth_header(self):
        import base64

        args = gitlab_client._git_auth_args("svc", "my-token")
        self.assertEqual(args[0], "-c")
        expected = base64.b64encode(b"svc:my-token").decode()
        self.assertEqual(args[1], f"http.extraheader=Authorization: Basic {expected}")


class RedactTests(SimpleTestCase):
    def test_masks_token_when_present(self):
        self.assertEqual(
            gitlab_client._redact("error: token abc123 invalid", "abc123"),
            "error: token *** invalid",
        )

    def test_returns_text_unchanged_when_no_token(self):
        self.assertEqual(gitlab_client._redact("error: oops", None), "error: oops")


class RunGitTests(SimpleTestCase):
    @patch("builds.gitlab_client.subprocess.run")
    def test_success_does_not_raise(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        gitlab_client._run_git("status", cwd="/tmp/whatever")

    @patch("builds.gitlab_client.subprocess.run")
    def test_failure_raises_with_redacted_message(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stderr="fatal: https://svc:secret-token@gitlab.example/x.git"
        )
        with self.assertRaises(GitLabBuildError) as ctx:
            gitlab_client._run_git("push", cwd="/tmp/whatever", redact="secret-token")
        self.assertNotIn("secret-token", str(ctx.exception))
        self.assertIn("***", str(ctx.exception))

    @patch("builds.gitlab_client.subprocess.run")
    def test_timeout_raises(self, mock_run):
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=60)
        with self.assertRaises(GitLabBuildError):
            gitlab_client._run_git("clone", cwd="/tmp/whatever")


@override_settings(GITLAB_URL="https://gitlab.example")
class SetCiVariableTests(SimpleTestCase):
    @patch("builds.gitlab_client.requests.request")
    def test_success(self, mock_request):
        mock_request.return_value = _response(201)
        gitlab_client.set_ci_variable(7, "tok", "CONFIG_FILE", "vm.yaml")
        mock_request.assert_called_once()

    @patch("builds.gitlab_client.requests.request")
    def test_masked_failure_falls_back_to_unmasked(self, mock_request):
        mock_request.side_effect = [_response(400, text="bad"), _response(201)]
        gitlab_client.set_ci_variable(7, "tok", "VAULT_TOKEN", "secret", masked=True)
        self.assertEqual(mock_request.call_count, 2)
        second_call_kwargs = mock_request.call_args_list[1].kwargs
        self.assertEqual(second_call_kwargs["data"]["masked"], "false")

    @patch("builds.gitlab_client.requests.request")
    def test_masked_fallback_also_failing_raises(self, mock_request):
        mock_request.side_effect = [
            _response(400, text="bad"),
            _response(400, text="still bad"),
        ]
        with self.assertRaises(GitLabBuildError):
            gitlab_client.set_ci_variable(
                7, "tok", "VAULT_TOKEN", "secret", masked=True
            )

    @patch("builds.gitlab_client.requests.request")
    def test_unmasked_failure_raises(self, mock_request):
        mock_request.return_value = _response(500, text="boom")
        with self.assertRaises(GitLabBuildError):
            gitlab_client.set_ci_variable(7, "tok", "CONFIG_FILE", "vm.yaml")


class MergeRequestUrlTests(SimpleTestCase):
    def test_includes_source_and_target_branch(self):
        url = gitlab_client.merge_request_url(
            "https://gitlab.example/vms/rcd09-d1-testvm01"
        )
        self.assertIn("/-/merge_requests/new", url)
        self.assertIn("source_branch%5D=development", url)
        self.assertIn("target_branch%5D=main", url)


@override_settings(GITLAB_URL="https://gitlab.example")
class GetProjectTests(SimpleTestCase):
    @patch("builds.gitlab_client.requests.request")
    def test_returns_project_json(self, mock_request):
        mock_request.return_value = _response(
            200, {"id": 7, "http_url_to_repo": "https://gitlab.example/vms/x.git"}
        )
        project = gitlab_client.get_project(7, "tok")
        self.assertEqual(
            project["http_url_to_repo"], "https://gitlab.example/vms/x.git"
        )

    @patch("builds.gitlab_client.requests.request")
    def test_raises_on_failure(self, mock_request):
        mock_request.return_value = _response(404, text="not found")
        with self.assertRaises(GitLabBuildError):
            gitlab_client.get_project(7, "tok")


@override_settings(GITLAB_URL="https://gitlab.example")
class CloneExistingBuildRepoTests(SimpleTestCase):
    def setUp(self):
        import shutil as shutil_mod
        import tempfile as tempfile_mod

        self.builds_dir = tempfile_mod.mkdtemp()
        self.addCleanup(lambda: shutil_mod.rmtree(self.builds_dir, ignore_errors=True))
        settings_override = override_settings(VM_BUILDS_DIR=self.builds_dir)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

    def _patch_all(self):
        patchers = {
            "get_gitlab_api_token": patch(
                "builds.gitlab_client.get_gitlab_api_token", return_value="gl-tok"
            ),
            "get_current_username": patch(
                "builds.gitlab_client.get_current_username",
                return_value="service_gitlab",
            ),
            "get_project": patch(
                "builds.gitlab_client.get_project",
                return_value={
                    "id": 7,
                    "http_url_to_repo": "https://gitlab.example/vms/rcd09-d1-testvm01.git",
                },
            ),
            "_run_git": patch("builds.gitlab_client._run_git"),
            "which": patch(
                "builds.gitlab_client.shutil.which", return_value="/usr/bin/git"
            ),
        }
        mocks = {name: p.start() for name, p in patchers.items()}
        for p in patchers.values():
            self.addCleanup(p.stop)
        return mocks

    def test_clones_into_vm_builds_dir(self):
        mocks = self._patch_all()

        result = gitlab_client.clone_existing_build_repo("rcd09-d1-testvm01", 7)

        self.assertEqual(result, Path(self.builds_dir) / "rcd09-d1-testvm01")
        mocks["_run_git"].assert_called_once()
        args = mocks["_run_git"].call_args.args
        self.assertIn("clone", args)
        self.assertIn("https://gitlab.example/vms/rcd09-d1-testvm01.git", args)

    def test_raises_when_build_dir_already_exists(self):
        self._patch_all()
        (Path(self.builds_dir) / "rcd09-d1-testvm01").mkdir()
        with self.assertRaises(GitLabBuildError):
            gitlab_client.clone_existing_build_repo("rcd09-d1-testvm01", 7)

    def test_raises_on_path_traversal_vm_name(self):
        self._patch_all()
        with self.assertRaises(GitLabBuildError):
            gitlab_client.clone_existing_build_repo("../../etc/evil", 7)

    def test_raises_when_vm_builds_dir_not_configured(self):
        self._patch_all()
        with override_settings(VM_BUILDS_DIR=""):
            with self.assertRaises(GitLabBuildError):
                gitlab_client.clone_existing_build_repo("rcd09-d1-testvm01", 7)

    def test_raises_when_no_gitlab_token(self):
        self._patch_all()
        with patch("builds.gitlab_client.get_gitlab_api_token", return_value=None):
            with self.assertRaises(GitLabBuildError):
                gitlab_client.clone_existing_build_repo("rcd09-d1-testvm01", 7)


@override_settings(GITLAB_URL="https://gitlab.example")
class CreateBuildRepoTests(SimpleTestCase):
    def test_rejects_path_traversal_vm_name_before_any_network_call(self):
        # Defense-in-depth: even if a caller bypasses ServerBuildForm's own
        # (stricter) validation, create_build_repo must never write to a path
        # derived from an unvalidated vm_name.
        with patch("builds.gitlab_client.get_gitlab_api_token") as mock_token:
            with self.assertRaises(GitLabBuildError):
                gitlab_client.create_build_repo("../../etc/evil", "key: value\n")
            mock_token.assert_not_called()

    def setUp(self):
        import shutil as shutil_mod
        import tempfile as tempfile_mod

        self.builds_dir = tempfile_mod.mkdtemp()
        self.addCleanup(lambda: shutil_mod.rmtree(self.builds_dir, ignore_errors=True))
        settings_override = override_settings(VM_BUILDS_DIR=self.builds_dir)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        # Simulate an already-cloned skeleton-vm checkout (the steady-state case
        # after the first build ever run) -- a .git dir (left alone, not copied into
        # build_dir) and the placeholder yaml file create_build_repo renames/
        # overwrites.
        self.skeleton_dir = Path(self.builds_dir) / "skeleton-vm"
        self.skeleton_dir.mkdir()
        (self.skeleton_dir / ".git").mkdir()
        (self.skeleton_dir / gitlab_client.NEW_VM_TEMPLATE_FILENAME).write_text(
            "placeholder\n"
        )
        self.build_dir = Path(self.builds_dir) / "rcd09-d1-testvm01"

    def _patch_all(self):
        patchers = {
            "get_gitlab_api_token": patch(
                "builds.gitlab_client.get_gitlab_api_token", return_value="gl-tok"
            ),
            "get_netbox_api_token": patch(
                "builds.gitlab_client.get_netbox_api_token", return_value="nb-tok"
            ),
            "get_service_vault_token": patch(
                "builds.gitlab_client.get_service_vault_token", return_value="vault-tok"
            ),
            "get_namespace_id": patch(
                "builds.gitlab_client.get_namespace_id", return_value=600
            ),
            "project_exists": patch(
                "builds.gitlab_client.project_exists", return_value=False
            ),
            "get_current_username": patch(
                "builds.gitlab_client.get_current_username",
                return_value="service_gitlab",
            ),
            "create_project": patch(
                "builds.gitlab_client.create_project",
                return_value={
                    "id": 7,
                    "name": "rcd09-d1-testvm01",
                    "web_url": "https://gitlab.example/vms/rcd09-d1-testvm01",
                    "http_url_to_repo": "https://gitlab.example/vms/rcd09-d1-testvm01.git",
                },
            ),
            "set_ci_variable": patch("builds.gitlab_client.set_ci_variable"),
            # _run_git is mocked (no real git commands run) but must still create
            # build_dir for clone, since the real create_build_repo code only
            # mkdir()s it itself -- a real `git clone` would populate it, so tests
            # simulate that side effect to let the real shutil.copytree() below run.
            "_run_git": patch(
                "builds.gitlab_client._run_git",
                side_effect=lambda *a, cwd, **kw: None,
            ),
            "which": patch(
                "builds.gitlab_client.shutil.which", return_value="/usr/bin/git"
            ),
        }
        mocks = {name: p.start() for name, p in patchers.items()}
        for p in patchers.values():
            self.addCleanup(p.stop)
        return mocks

    def test_happy_path_returns_urls(self):
        mocks = self._patch_all()

        result = gitlab_client.create_build_repo("rcd09-d1-testvm01", "key: value\n")

        self.assertEqual(result["project_id"], 7)
        self.assertEqual(
            result["project_web_url"], "https://gitlab.example/vms/rcd09-d1-testvm01"
        )
        self.assertIn("/-/merge_requests/new", result["merge_request_url"])
        mocks["set_ci_variable"].assert_any_call(
            7, "gl-tok", "CONFIG_FILE", "rcd09-d1-testvm01.yaml"
        )
        mocks["set_ci_variable"].assert_any_call(
            7, "gl-tok", "NETBOX_TOKEN", "nb-tok", masked=True
        )
        mocks["set_ci_variable"].assert_any_call(
            7, "gl-tok", "VAULT_TOKEN", "vault-tok", masked=True
        )
        git_calls = [call.args for call in mocks["_run_git"].call_args_list]
        # Every call's git subcommand is whichever positional arg comes right after
        # any leading "-c http.extraheader=..." auth args.
        subcommands = [args[2] if args[0] == "-c" else args[0] for args in git_calls]
        self.assertEqual(
            subcommands,
            [
                "remote",  # normalize skeleton-vm's remote to HTTPS before pulling
                "pull",  # skeleton-vm already present -> pulled, not cloned
                "clone",  # the new project itself
                "config",
                "config",
                "add",
                "commit",
                "push",
                "checkout",
                "add",
                "commit",
                "push",
            ],
        )
        # skeleton-vm's content (incl. the placeholder yaml) was copied into build_dir
        # (but NOT its .git), then the generated yaml replaced that placeholder before
        # the final push.
        self.assertFalse(
            (self.build_dir / gitlab_client.NEW_VM_TEMPLATE_FILENAME).exists()
        )
        self.assertFalse((self.build_dir / ".git").exists())
        self.assertEqual(
            (self.build_dir / "rcd09-d1-testvm01.yaml").read_text(), "key: value\n"
        )

    def test_clones_skeleton_vm_when_not_already_present(self):
        import shutil as shutil_mod

        shutil_mod.rmtree(self.skeleton_dir)
        mocks = self._patch_all()

        # The mocked "clone" of skeleton-vm wouldn't actually create it on disk --
        # recreate what a real `git clone` would have left behind.
        def _run_git_side_effect(*args, cwd, **kwargs):
            if "clone" in args and str(self.skeleton_dir) in args:
                self.skeleton_dir.mkdir()
                (self.skeleton_dir / gitlab_client.NEW_VM_TEMPLATE_FILENAME).write_text(
                    "placeholder\n"
                )

        mocks["_run_git"].side_effect = _run_git_side_effect

        gitlab_client.create_build_repo("rcd09-d1-testvm01", "key: value\n")

        first_call_args = mocks["_run_git"].call_args_list[0].args
        self.assertIn("clone", first_call_args)

    def test_raises_when_build_dir_already_exists(self):
        self._patch_all()
        self.build_dir.mkdir()
        with self.assertRaises(GitLabBuildError):
            gitlab_client.create_build_repo("rcd09-d1-testvm01", "key: value\n")

    def test_raises_when_vm_builds_dir_not_configured(self):
        self._patch_all()
        with override_settings(VM_BUILDS_DIR=""):
            with self.assertRaises(GitLabBuildError):
                gitlab_client.create_build_repo("rcd09-d1-testvm01", "key: value\n")

    def test_raises_when_no_gitlab_token(self):
        self._patch_all()
        with patch("builds.gitlab_client.get_gitlab_api_token", return_value=None):
            with self.assertRaises(GitLabBuildError):
                gitlab_client.create_build_repo("rcd09-d1-testvm01", "key: value\n")

    def test_raises_when_git_not_installed(self):
        self._patch_all()
        with patch("builds.gitlab_client.shutil.which", return_value=None):
            with self.assertRaises(GitLabBuildError):
                gitlab_client.create_build_repo("rcd09-d1-testvm01", "key: value\n")

    def test_raises_when_project_name_already_exists(self):
        self._patch_all()
        with patch("builds.gitlab_client.project_exists", return_value=True):
            with self.assertRaises(GitLabBuildError):
                gitlab_client.create_build_repo("rcd09-d1-testvm01", "key: value\n")

    def test_raises_when_created_project_name_mismatches(self):
        self._patch_all()
        with patch(
            "builds.gitlab_client.create_project",
            return_value={
                "id": 7,
                "name": "something-else",
                "web_url": "https://gitlab.example/vms/something-else",
                "http_url_to_repo": "https://gitlab.example/vms/something-else.git",
            },
        ):
            with self.assertRaises(GitLabBuildError):
                gitlab_client.create_build_repo("rcd09-d1-testvm01", "key: value\n")

    def test_raises_when_skeleton_missing_placeholder_yaml(self):
        self._patch_all()
        (self.skeleton_dir / gitlab_client.NEW_VM_TEMPLATE_FILENAME).unlink()
        with self.assertRaises(GitLabBuildError):
            gitlab_client.create_build_repo("rcd09-d1-testvm01", "key: value\n")

    def test_git_command_failure_propagates(self):
        mocks = self._patch_all()
        mocks["_run_git"].side_effect = GitLabBuildError("push failed")
        with self.assertRaises(GitLabBuildError):
            gitlab_client.create_build_repo("rcd09-d1-testvm01", "key: value\n")

    def test_missing_optional_tokens_skip_those_ci_variables_without_failing(self):
        mocks = self._patch_all()
        with patch(
            "builds.gitlab_client.get_netbox_api_token", return_value=None
        ), patch("builds.gitlab_client.get_service_vault_token", return_value=None):
            result = gitlab_client.create_build_repo(
                "rcd09-d1-testvm01", "key: value\n"
            )

        self.assertIn("project_web_url", result)
        ci_var_keys = [call.args[2] for call in mocks["set_ci_variable"].call_args_list]
        self.assertIn("CONFIG_FILE", ci_var_keys)
        self.assertNotIn("NETBOX_TOKEN", ci_var_keys)
        self.assertNotIn("VAULT_TOKEN", ci_var_keys)


@override_settings(GITLAB_URL="https://gitlab.example")
class GetLatestPipelineTests(SimpleTestCase):
    @patch("builds.gitlab_client.requests.request")
    def test_returns_newest_pipeline(self, mock_request):
        mock_request.return_value = _response(
            200, [{"id": 42, "status": "running", "web_url": "https://x/pipelines/42"}]
        )
        pipeline = gitlab_client.get_latest_pipeline(7, "tok")
        self.assertEqual(pipeline["id"], 42)
        self.assertEqual(
            mock_request.call_args.kwargs["params"],
            {"order_by": "id", "sort": "desc", "per_page": 1},
        )

    @patch("builds.gitlab_client.requests.request")
    def test_filters_by_ref_when_given(self, mock_request):
        mock_request.return_value = _response(200, [])
        gitlab_client.get_latest_pipeline(7, "tok", ref="development")
        self.assertEqual(
            mock_request.call_args.kwargs["params"],
            {"order_by": "id", "sort": "desc", "per_page": 1, "ref": "development"},
        )

    @patch("builds.gitlab_client.requests.request")
    def test_returns_none_when_no_pipelines_yet(self, mock_request):
        mock_request.return_value = _response(200, [])
        self.assertIsNone(gitlab_client.get_latest_pipeline(7, "tok"))

    @patch("builds.gitlab_client.requests.request")
    def test_returns_none_on_api_error(self, mock_request):
        mock_request.return_value = _response(500, text="boom")
        self.assertIsNone(gitlab_client.get_latest_pipeline(7, "tok"))


@override_settings(GITLAB_URL="https://gitlab.example")
class GetPipelineJobsTests(SimpleTestCase):
    @patch("builds.gitlab_client.requests.request")
    def test_returns_jobs_in_stage_execution_order(self, mock_request):
        # GitLab's jobs endpoint sorts descending by id (newest/last-stage first).
        mock_request.return_value = _response(
            200,
            [
                {"name": "terraform_apply_job", "stage": "apply", "status": "manual"},
                {
                    "name": "terraform_verify_and_plan_job",
                    "stage": "verify_and_plan",
                    "status": "success",
                },
            ],
        )
        jobs = gitlab_client.get_pipeline_jobs(7, 42, "tok")
        self.assertEqual(
            [j["name"] for j in jobs],
            [
                "terraform_verify_and_plan_job",
                "terraform_apply_job",
            ],
        )

    @patch("builds.gitlab_client.requests.request")
    def test_returns_empty_list_on_api_error(self, mock_request):
        mock_request.return_value = _response(500, text="boom")
        self.assertEqual(gitlab_client.get_pipeline_jobs(7, 42, "tok"), [])


@override_settings(GITLAB_URL="https://gitlab.example")
class MergeDevelopmentBranchTests(SimpleTestCase):
    @patch("builds.gitlab_client.requests.request")
    def test_creates_mr_when_none_open_then_merges(self, mock_request):
        mock_request.side_effect = [
            _response(200, []),  # GET open MRs -> none
            _response(201, {"iid": 5}),  # POST create MR
            _response(200, {"state": "merged"}),  # PUT merge
        ]
        result = gitlab_client.merge_development_branch(7, "tok")
        self.assertEqual(result, {"state": "merged"})
        create_call = mock_request.call_args_list[1]
        self.assertEqual(create_call.args[0], "POST")
        merge_call = mock_request.call_args_list[2]
        self.assertIn("/merge_requests/5/merge", merge_call.args[1])

    @patch("builds.gitlab_client.requests.request")
    def test_reuses_existing_open_mr(self, mock_request):
        mock_request.side_effect = [
            _response(200, [{"iid": 9}]),  # GET open MRs -> already exists
            _response(200, {"state": "merged"}),  # PUT merge
        ]
        gitlab_client.merge_development_branch(7, "tok")
        self.assertEqual(mock_request.call_count, 2)
        merge_call = mock_request.call_args_list[1]
        self.assertIn("/merge_requests/9/merge", merge_call.args[1])

    @patch("builds.gitlab_client.requests.request")
    def test_raises_on_merge_failure(self, mock_request):
        mock_request.side_effect = [
            _response(200, [{"iid": 9}]),
            _response(405, text="not mergeable"),
        ]
        with self.assertRaises(GitLabBuildError):
            gitlab_client.merge_development_branch(7, "tok")

    @patch("builds.gitlab_client.requests.request")
    def test_raises_when_lookup_fails(self, mock_request):
        mock_request.return_value = _response(500, text="boom")
        with self.assertRaises(GitLabBuildError):
            gitlab_client.merge_development_branch(7, "tok")


@override_settings(GITLAB_URL="https://gitlab.example")
class PlayJobTests(SimpleTestCase):
    @patch("builds.gitlab_client.requests.request")
    def test_plays_job_successfully(self, mock_request):
        mock_request.return_value = _response(200, {"status": "pending"})
        gitlab_client.play_job(7, 99, "tok")
        call = mock_request.call_args
        self.assertEqual(call.args[0], "POST")
        self.assertIn("/jobs/99/play", call.args[1])

    @patch("builds.gitlab_client.requests.request")
    def test_raises_on_failure(self, mock_request):
        mock_request.return_value = _response(403, text="not allowed")
        with self.assertRaises(GitLabBuildError):
            gitlab_client.play_job(7, 99, "tok")
