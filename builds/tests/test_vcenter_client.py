from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from builds import vcenter_client
from builds.vcenter_stub import STUB_TEMPLATE


class VCenterClientTestCase(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)


class HostAndPrefixHelpersTests(SimpleTestCase):
    def test_vcenter_host_for_site(self):
        self.assertEqual(
            vcenter_client.vcenter_host_for_site("rcd09"),
            "rcd09-c1-vm-vcenter-01.cll.cloud",
        )

    def test_template_prefix_for_site(self):
        self.assertEqual(
            vcenter_client.template_prefix_for_site("rcd09"), "rcd09-c1-vmt-ubuntu2404-"
        )


class GetLatestUbuntuTemplateTests(VCenterClientTestCase):
    @patch("builds.vcenter_client.get_vcenter_credentials", return_value=(None, None))
    def test_falls_back_to_stub_when_no_credentials(self, _mock_creds):
        self.assertEqual(
            vcenter_client.get_latest_ubuntu_template("rcd09"), STUB_TEMPLATE["rcd09"]
        )

    @patch("builds.vcenter_client._get_newest_ubuntu_template")
    @patch(
        "builds.vcenter_client.get_vcenter_credentials", return_value=("user", "pass")
    )
    def test_uses_real_lookup_when_credentials_present(self, _mock_creds, mock_lookup):
        mock_lookup.return_value = "rcd09-c1-vmt-ubuntu2404-260901-0000"
        self.assertEqual(
            vcenter_client.get_latest_ubuntu_template("rcd09"),
            "rcd09-c1-vmt-ubuntu2404-260901-0000",
        )

    @patch("builds.vcenter_client._get_newest_ubuntu_template")
    @patch(
        "builds.vcenter_client.get_vcenter_credentials", return_value=("user", "pass")
    )
    def test_falls_back_to_stub_when_real_lookup_fails(self, _mock_creds, mock_lookup):
        mock_lookup.side_effect = RuntimeError("no templates found")
        self.assertEqual(
            vcenter_client.get_latest_ubuntu_template("rcd09"), STUB_TEMPLATE["rcd09"]
        )

    @patch("builds.vcenter_client._get_newest_ubuntu_template")
    @patch(
        "builds.vcenter_client.get_vcenter_credentials", return_value=("user", "pass")
    )
    def test_result_is_cached(self, _mock_creds, mock_lookup):
        mock_lookup.return_value = "rcd09-c1-vmt-ubuntu2404-260901-0000"
        vcenter_client.get_latest_ubuntu_template("rcd09")
        vcenter_client.get_latest_ubuntu_template("rcd09")
        mock_lookup.assert_called_once()


class CheckDatastoreCapacityWarningTests(VCenterClientTestCase):
    def test_returns_none_when_datastore_name_blank(self):
        self.assertIsNone(vcenter_client.check_datastore_capacity_warning("rcd09", ""))

    @patch("builds.vcenter_client.get_vcenter_credentials", return_value=(None, None))
    def test_returns_none_when_no_credentials(self, _mock_creds):
        self.assertIsNone(
            vcenter_client.check_datastore_capacity_warning("rcd09", "infra-01")
        )

    @patch("builds.vcenter_client._get_datastore_free_ratio")
    @patch(
        "builds.vcenter_client.get_vcenter_credentials", return_value=("user", "pass")
    )
    def test_warns_when_free_space_below_threshold(self, _mock_creds, mock_ratio):
        mock_ratio.return_value = 0.05
        warning = vcenter_client.check_datastore_capacity_warning("rcd09", "infra-01")
        self.assertIsNotNone(warning)
        self.assertIn("infra-01", warning)

    @patch("builds.vcenter_client._get_datastore_free_ratio")
    @patch(
        "builds.vcenter_client.get_vcenter_credentials", return_value=("user", "pass")
    )
    def test_no_warning_when_free_space_above_threshold(self, _mock_creds, mock_ratio):
        mock_ratio.return_value = 0.5
        self.assertIsNone(
            vcenter_client.check_datastore_capacity_warning("rcd09", "infra-01")
        )

    @patch("builds.vcenter_client._get_datastore_free_ratio")
    @patch(
        "builds.vcenter_client.get_vcenter_credentials", return_value=("user", "pass")
    )
    def test_no_warning_when_lookup_fails(self, _mock_creds, mock_ratio):
        mock_ratio.side_effect = RuntimeError("down")
        self.assertIsNone(
            vcenter_client.check_datastore_capacity_warning("rcd09", "infra-01")
        )

    @patch("builds.vcenter_client._get_datastore_free_ratio")
    @patch(
        "builds.vcenter_client.get_vcenter_credentials", return_value=("user", "pass")
    )
    def test_result_is_cached_per_site_and_datastore(self, _mock_creds, mock_ratio):
        mock_ratio.return_value = 0.5
        vcenter_client.check_datastore_capacity_warning("rcd09", "infra-01")
        vcenter_client.check_datastore_capacity_warning("rcd09", "infra-01")
        mock_ratio.assert_called_once()


class GetVCenterInventoryTests(VCenterClientTestCase):
    @patch("builds.vcenter_client.get_vcenter_credentials", return_value=(None, None))
    def test_falls_back_to_stub_when_no_credentials(self, _mock_creds):
        inventory = vcenter_client.get_vcenter_inventory("rcd09")
        self.assertIn("rcd09-infrastructure", inventory["clusters"])

    @patch("builds.vcenter_client._fetch_vcenter_inventory")
    @patch(
        "builds.vcenter_client.get_vcenter_credentials", return_value=("user", "pass")
    )
    def test_uses_real_inventory_when_credentials_present(
        self, _mock_creds, mock_fetch
    ):
        mock_fetch.return_value = {
            "datastores": ["real-ds"],
            "clusters": ["real-cluster"],
            "resource_pools": ["real-pool"],
            "folders": ["real-folder"],
            "portgroups": ["/rcd09/network/real-sw/real-pg"],
        }
        inventory = vcenter_client.get_vcenter_inventory("rcd09")
        self.assertEqual(inventory["clusters"], ["real-cluster"])

    @patch("builds.vcenter_client._fetch_vcenter_inventory")
    @patch(
        "builds.vcenter_client.get_vcenter_credentials", return_value=("user", "pass")
    )
    def test_falls_back_to_stub_when_real_fetch_fails(self, _mock_creds, mock_fetch):
        mock_fetch.side_effect = RuntimeError("down")
        inventory = vcenter_client.get_vcenter_inventory("rcd09")
        self.assertIn("rcd09-infrastructure", inventory["clusters"])

    @patch("builds.vcenter_client._fetch_vcenter_inventory")
    @patch(
        "builds.vcenter_client.get_vcenter_credentials", return_value=("user", "pass")
    )
    def test_result_is_cached(self, _mock_creds, mock_fetch):
        mock_fetch.return_value = {
            "datastores": [],
            "clusters": [],
            "resource_pools": [],
            "folders": [],
            "portgroups": [],
        }
        vcenter_client.get_vcenter_inventory("rcd09")
        vcenter_client.get_vcenter_inventory("rcd09")
        mock_fetch.assert_called_once()
