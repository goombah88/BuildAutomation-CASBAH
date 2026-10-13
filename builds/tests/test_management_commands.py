import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from casbah.secret_crypto import decrypt


def _run_command():
    call_command("set_vault_approle", stdout=StringIO(), stderr=StringIO())


class SetVaultApproleCommandTests(SimpleTestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        api_vars_path = Path(self._tmpdir.name) / ".api_variables_env"
        key_path = Path(self._tmpdir.name) / ".casbah" / "secret.key"
        self.enterContext(
            patch(
                "builds.management.commands.set_vault_approle.API_VARS_PATH",
                api_vars_path,
            )
        )
        self.enterContext(patch("casbah.secret_crypto.KEY_PATH", key_path))
        self.api_vars_path = api_vars_path

    def test_stores_encrypted_role_and_secret_id(self):
        with patch("getpass.getpass", side_effect=["my-role-id", "my-secret-id"]):
            _run_command()

        contents = self.api_vars_path.read_text()
        self.assertIn("VAULT_ROLE_ID_ENC=", contents)
        self.assertIn("VAULT_SECRET_ID_ENC=", contents)
        self.assertNotIn("VAULT_ROLE_ID=", contents)
        self.assertNotIn("my-role-id", contents)

        values = {}
        for line in contents.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                values[key] = value

        self.assertEqual(decrypt(values["VAULT_ROLE_ID_ENC"]), "my-role-id")
        self.assertEqual(decrypt(values["VAULT_SECRET_ID_ENC"]), "my-secret-id")

    def test_file_created_with_restrictive_permissions(self):
        with patch("getpass.getpass", side_effect=["role", "secret"]):
            _run_command()
        mode = self.api_vars_path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_missing_role_id_aborts_without_writing_file(self):
        with patch("getpass.getpass", side_effect=["", "secret"]):
            _run_command()
        self.assertFalse(self.api_vars_path.exists())

    def test_missing_secret_id_aborts_without_writing_file(self):
        with patch("getpass.getpass", side_effect=["role", ""]):
            _run_command()
        self.assertFalse(self.api_vars_path.exists())
