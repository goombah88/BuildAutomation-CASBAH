from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from builds import vault_client

NO_VAULT = {
    "VAULT_URL": "",
    "VAULT_ROLE_ID": "",
    "VAULT_SECRET_ID": "",
    "VAULT_TOKEN": "",
}
TOKEN_ONLY = {
    "VAULT_URL": "https://vault.example",
    "VAULT_ROLE_ID": "",
    "VAULT_SECRET_ID": "",
    "VAULT_TOKEN": "static-token",
}
APPROLE = {
    "VAULT_URL": "https://vault.example",
    "VAULT_ROLE_ID": "role-id",
    "VAULT_SECRET_ID": "secret-id",
    "VAULT_TOKEN": "",
}


class ReadVaultSecretTests(SimpleTestCase):
    @override_settings(**NO_VAULT)
    def test_returns_none_when_vault_url_not_configured(self):
        self.assertIsNone(vault_client._read_vault_secret("some/path", "key"))

    @override_settings(**{**TOKEN_ONLY, "VAULT_TOKEN": ""})
    def test_returns_none_when_no_credentials_configured(self):
        self.assertIsNone(vault_client._read_vault_secret("some/path", "key"))

    @override_settings(**TOKEN_ONLY)
    @patch("hvac.Client")
    def test_returns_value_on_success(self, mock_client_cls):
        client = MagicMock()
        client.is_authenticated.return_value = True
        client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"key": "the-value"}}
        }
        mock_client_cls.return_value = client

        value = vault_client._read_vault_secret("some/path", "key")

        self.assertEqual(value, "the-value")
        client.secrets.kv.v2.read_secret_version.assert_called_once_with(
            path="some/path", mount_point=vault_client.VAULT_MOUNT_POINT
        )

    @override_settings(**TOKEN_ONLY)
    @patch("hvac.Client")
    def test_returns_none_when_key_missing_from_secret(self, mock_client_cls):
        client = MagicMock()
        client.is_authenticated.return_value = True
        client.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": {}}}
        mock_client_cls.return_value = client

        self.assertIsNone(vault_client._read_vault_secret("some/path", "key"))

    @override_settings(**TOKEN_ONLY)
    @patch("hvac.Client")
    def test_returns_none_when_vault_unreachable(self, mock_client_cls):
        mock_client_cls.side_effect = ConnectionError("boom")
        self.assertIsNone(vault_client._read_vault_secret("some/path", "key"))


class AuthenticatedClientTests(SimpleTestCase):
    @override_settings(**APPROLE)
    @patch("hvac.Client")
    def test_uses_approle_when_role_and_secret_id_set(self, mock_client_cls):
        client = MagicMock()
        client.auth.approle.login.return_value = {"auth": {"client_token": "tok"}}
        client.is_authenticated.return_value = True
        mock_client_cls.return_value = client

        result = vault_client._authenticated_client()

        client.auth.approle.login.assert_called_once_with(
            role_id="role-id", secret_id="secret-id"
        )
        self.assertEqual(result.token, "tok")

    @override_settings(**TOKEN_ONLY)
    @patch("hvac.Client")
    def test_falls_back_to_static_token(self, mock_client_cls):
        client = MagicMock()
        client.is_authenticated.return_value = True
        mock_client_cls.return_value = client

        result = vault_client._authenticated_client()

        client.auth.approle.login.assert_not_called()
        self.assertEqual(result.token, "static-token")

    @override_settings(**NO_VAULT)
    def test_raises_when_no_credentials_configured(self):
        with self.assertRaises(RuntimeError):
            vault_client._authenticated_client()

    @override_settings(**TOKEN_ONLY)
    @patch("hvac.Client")
    def test_raises_when_not_authenticated(self, mock_client_cls):
        client = MagicMock()
        client.is_authenticated.return_value = False
        mock_client_cls.return_value = client
        with self.assertRaises(RuntimeError):
            vault_client._authenticated_client()


class VCenterCredentialsTests(SimpleTestCase):
    @override_settings(**TOKEN_ONLY)
    @patch("hvac.Client")
    def test_returns_username_from_path_and_password_from_secret(self, mock_client_cls):
        client = MagicMock()
        client.is_authenticated.return_value = True
        client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"password": "hunter2"}}
        }
        mock_client_cls.return_value = client

        username, password = vault_client.get_vcenter_credentials("rcd09")

        self.assertEqual(username, "administrator@rcd09.c1.cll.cloud")
        self.assertEqual(password, "hunter2")

    def test_returns_none_none_for_unknown_site(self):
        self.assertEqual(vault_client.get_vcenter_credentials("nowhere"), (None, None))

    @override_settings(**NO_VAULT)
    def test_returns_none_none_when_vault_unreachable(self):
        self.assertEqual(vault_client.get_vcenter_credentials("rcd09"), (None, None))


class ServiceTokenLookupTests(SimpleTestCase):
    @override_settings(**TOKEN_ONLY)
    @patch("builds.vault_client._read_vault_secret")
    def test_get_netbox_api_token_reads_expected_path_and_key(self, mock_read):
        mock_read.return_value = "nb-token"
        self.assertEqual(vault_client.get_netbox_api_token(), "nb-token")
        mock_read.assert_called_once_with(vault_client.NETBOX_TOKEN_PATH, "api_key")

    @override_settings(**TOKEN_ONLY)
    @patch("builds.vault_client._read_vault_secret")
    def test_get_gitlab_api_token_reads_expected_path_and_key(self, mock_read):
        mock_read.return_value = "gl-token"
        self.assertEqual(vault_client.get_gitlab_api_token(), "gl-token")
        mock_read.assert_called_once_with(vault_client.GITLAB_TOKEN_PATH, "api_token")

    @patch("builds.vault_client._read_vault_secret")
    def test_get_ad_bind_credentials_reads_password_and_uses_fixed_bind_dn(
        self, mock_read
    ):
        mock_read.return_value = "svc-password"
        self.assertEqual(
            vault_client.get_ad_bind_credentials(),
            (vault_client.AD_BIND_ACCOUNT_DN, "svc-password"),
        )
        mock_read.assert_called_once_with(vault_client.AD_BIND_ACCOUNT_PATH, "password")

    @patch("builds.vault_client._read_vault_secret")
    def test_get_ad_bind_credentials_none_none_when_password_missing(self, mock_read):
        mock_read.return_value = None
        self.assertEqual(vault_client.get_ad_bind_credentials(), (None, None))

    @override_settings(**NO_VAULT)
    def test_get_service_vault_token_none_when_not_configured(self):
        self.assertIsNone(vault_client.get_service_vault_token())

    @override_settings(**APPROLE)
    @patch("hvac.Client")
    def test_get_service_vault_token_mints_scoped_child_token(self, mock_client_cls):
        client = MagicMock()
        client.auth.approle.login.return_value = {
            "auth": {"client_token": "live-token"}
        }
        client.is_authenticated.return_value = True
        client.auth.token.lookup_self.return_value = {
            "data": {"policies": ["default", "casbah-service"]}
        }
        client.auth.token.create.return_value = {
            "auth": {"client_token": "child-token"}
        }
        mock_client_cls.return_value = client

        self.assertEqual(vault_client.get_service_vault_token(), "child-token")
        client.auth.token.create.assert_called_once_with(
            policies=["default", "casbah-service"],
            ttl=vault_client.BUILD_PIPELINE_TOKEN_TTL,
            display_name="casbah-build-pipeline",
        )

    @override_settings(**TOKEN_ONLY)
    @patch("hvac.Client")
    def test_get_service_vault_token_none_on_failure(self, mock_client_cls):
        mock_client_cls.side_effect = ConnectionError("boom")
        self.assertIsNone(vault_client.get_service_vault_token())

    @override_settings(**TOKEN_ONLY)
    @patch("hvac.Client")
    def test_get_service_vault_token_none_when_child_token_creation_denied(
        self, mock_client_cls
    ):
        client = MagicMock()
        client.is_authenticated.return_value = True
        client.auth.token.lookup_self.return_value = {"data": {"policies": ["default"]}}
        client.auth.token.create.side_effect = Exception("permission denied")
        mock_client_cls.return_value = client

        self.assertIsNone(vault_client.get_service_vault_token())

