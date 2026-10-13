from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from builds import netbox_client
from builds.netbox_client import NetBoxBuildError


class NetBoxClientTestCase(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)


class TenantNameLookupTests(NetBoxClientTestCase):
    @patch("builds.netbox_client._client")
    def test_empty_list_when_client_unavailable(self, mock_client):
        mock_client.return_value = None
        self.assertEqual(netbox_client.get_all_tenant_names(), [])

    @patch("builds.netbox_client._client")
    def test_returns_sorted_unique_tenant_names(self, mock_client):
        nb = MagicMock()
        beta, alpha = MagicMock(), MagicMock()
        # MagicMock(name=...) in the constructor sets the mock's repr, not the `.name`
        # attribute value, so it must be assigned separately here.
        beta.name = "Beta"
        alpha.name = "Alpha"
        nb.tenancy.tenants.all.return_value = [beta, alpha]
        mock_client.return_value = nb

        self.assertEqual(netbox_client.get_all_tenant_names(), ["Alpha", "Beta"])

    @patch("builds.netbox_client._client")
    def test_result_is_cached(self, mock_client):
        nb = MagicMock()
        tenant = MagicMock()
        tenant.name = "Alpha"
        nb.tenancy.tenants.all.return_value = [tenant]
        mock_client.return_value = nb

        first = netbox_client.get_all_tenant_names()
        second = netbox_client.get_all_tenant_names()

        self.assertEqual(first, second)
        mock_client.assert_called_once()

    @patch("builds.netbox_client._client")
    def test_falls_back_to_empty_list_on_error(self, mock_client):
        nb = MagicMock()
        nb.tenancy.tenants.all.side_effect = RuntimeError("down")
        mock_client.return_value = nb
        self.assertEqual(netbox_client.get_all_tenant_names(), [])


class TenantGroupLookupTests(NetBoxClientTestCase):
    @patch("builds.netbox_client._client")
    def test_htd_always_included_when_client_unavailable(self, mock_client):
        mock_client.return_value = None
        self.assertEqual(netbox_client.get_all_tenant_group_names(), ["HTD"])

    @patch("builds.netbox_client._client")
    def test_htd_included_alongside_real_groups(self, mock_client):
        nb = MagicMock()
        group = MagicMock()
        group.name = "Engineering"
        nb.tenancy.tenant_groups.all.return_value = [group]
        mock_client.return_value = nb

        self.assertEqual(
            netbox_client.get_all_tenant_group_names(), ["Engineering", "HTD"]
        )

    @patch("builds.netbox_client._client")
    def test_falls_back_to_htd_only_on_error(self, mock_client):
        nb = MagicMock()
        nb.tenancy.tenant_groups.all.side_effect = RuntimeError("down")
        mock_client.return_value = nb
        self.assertEqual(netbox_client.get_all_tenant_group_names(), ["HTD"])


class VmNameExistsTests(NetBoxClientTestCase):
    @patch("builds.netbox_client._client")
    def test_none_when_client_unavailable(self, mock_client):
        mock_client.return_value = None
        self.assertIsNone(netbox_client.vm_name_exists("rcd09-d1-testvm01"))

    @patch("builds.netbox_client._client")
    def test_true_when_match_found(self, mock_client):
        nb = MagicMock()
        nb.virtualization.virtual_machines.filter.return_value = [MagicMock()]
        mock_client.return_value = nb
        self.assertTrue(netbox_client.vm_name_exists("rcd09-d1-testvm01"))

    @patch("builds.netbox_client._client")
    def test_false_when_no_match(self, mock_client):
        nb = MagicMock()
        nb.virtualization.virtual_machines.filter.return_value = []
        mock_client.return_value = nb
        self.assertFalse(netbox_client.vm_name_exists("rcd09-d1-testvm01"))

    @patch("builds.netbox_client._client")
    def test_none_on_error(self, mock_client):
        nb = MagicMock()
        nb.virtualization.virtual_machines.filter.side_effect = RuntimeError("down")
        mock_client.return_value = nb
        self.assertIsNone(netbox_client.vm_name_exists("rcd09-d1-testvm01"))


@override_settings(NETBOX_URL="https://netbox.example")
class FindVmUrlTests(NetBoxClientTestCase):
    @patch("builds.netbox_client._client")
    def test_returns_detail_url_when_exists(self, mock_client):
        nb = MagicMock()
        vm = MagicMock()
        vm.id = 3975
        nb.virtualization.virtual_machines.filter.return_value = [vm]
        mock_client.return_value = nb

        url = netbox_client.find_vm_url("rcd09-d1-testvm01")

        self.assertEqual(
            url, "https://netbox.example/virtualization/virtual-machines/3975/"
        )

    @patch("builds.netbox_client._client")
    def test_none_when_not_found(self, mock_client):
        nb = MagicMock()
        nb.virtualization.virtual_machines.filter.return_value = []
        mock_client.return_value = nb
        self.assertIsNone(netbox_client.find_vm_url("rcd09-d1-testvm01"))

    @patch("builds.netbox_client._client")
    def test_none_when_client_unavailable(self, mock_client):
        mock_client.return_value = None
        self.assertIsNone(netbox_client.find_vm_url("rcd09-d1-testvm01"))

    @patch("builds.netbox_client._client")
    def test_none_on_error(self, mock_client):
        nb = MagicMock()
        nb.virtualization.virtual_machines.filter.side_effect = RuntimeError("down")
        mock_client.return_value = nb
        self.assertIsNone(netbox_client.find_vm_url("rcd09-d1-testvm01"))


class IpAddressExistsTests(NetBoxClientTestCase):
    @patch("builds.netbox_client._client")
    def test_none_when_client_unavailable(self, mock_client):
        mock_client.return_value = None
        self.assertIsNone(netbox_client.ip_address_exists("10.0.0.5"))

    @patch("builds.netbox_client._client")
    def test_true_when_match_found_in_vrf(self, mock_client):
        nb = MagicMock()
        nb.ipam.vrfs.get.return_value = MagicMock(id=9)
        nb.ipam.ip_addresses.filter.return_value = [MagicMock()]
        mock_client.return_value = nb

        self.assertTrue(netbox_client.ip_address_exists("10.0.0.5"))
        nb.ipam.ip_addresses.filter.assert_called_once_with(
            address="10.0.0.5", vrf_id=9
        )

    @patch("builds.netbox_client._client")
    def test_none_on_error(self, mock_client):
        nb = MagicMock()
        nb.ipam.vrfs.get.side_effect = RuntimeError("down")
        mock_client.return_value = nb
        self.assertIsNone(netbox_client.ip_address_exists("10.0.0.5"))


class ResolveTenantTests(SimpleTestCase):
    def test_raises_when_group_missing(self):
        nb = MagicMock()
        nb.tenancy.tenant_groups.get.return_value = None
        with self.assertRaisesMessage(NetBoxBuildError, "tenant group 'HTD' not found"):
            netbox_client._resolve_tenant(nb, "HTD", "VMCloud Dev")

    def test_raises_when_no_tenant_match(self):
        nb = MagicMock()
        nb.tenancy.tenant_groups.get.return_value = MagicMock(id=1)
        nb.tenancy.tenants.filter.return_value = []
        with self.assertRaises(NetBoxBuildError):
            netbox_client._resolve_tenant(nb, "HTD", "VMCloud Dev")

    def test_raises_when_multiple_tenants_match(self):
        nb = MagicMock()
        nb.tenancy.tenant_groups.get.return_value = MagicMock(id=1)
        nb.tenancy.tenants.filter.return_value = [MagicMock(), MagicMock()]
        with self.assertRaises(NetBoxBuildError):
            netbox_client._resolve_tenant(nb, "HTD", "VMCloud Dev")

    def test_returns_single_match(self):
        nb = MagicMock()
        nb.tenancy.tenant_groups.get.return_value = MagicMock(id=1)
        tenant = MagicMock()
        nb.tenancy.tenants.filter.return_value = [tenant]
        self.assertIs(netbox_client._resolve_tenant(nb, "HTD", "VMCloud Dev"), tenant)


class ResolveClusterTests(SimpleTestCase):
    def test_raises_when_not_found(self):
        nb = MagicMock()
        nb.virtualization.clusters.filter.return_value = []
        with self.assertRaises(NetBoxBuildError):
            netbox_client._resolve_cluster(nb, "rcd09-infrastructure", "rcd09")

    def test_raises_when_ambiguous(self):
        nb = MagicMock()
        nb.virtualization.clusters.filter.return_value = [MagicMock(), MagicMock()]
        with self.assertRaises(NetBoxBuildError):
            netbox_client._resolve_cluster(nb, "rcd09-infrastructure", "rcd09")

    def test_returns_single_match(self):
        nb = MagicMock()
        cluster = MagicMock()
        nb.virtualization.clusters.filter.return_value = [cluster]
        self.assertIs(
            netbox_client._resolve_cluster(nb, "rcd09-infrastructure", "rcd09"), cluster
        )


class ResolveReferenceDataTests(SimpleTestCase):
    def test_resolve_role_id_raises_when_missing(self):
        nb = MagicMock()
        nb.dcim.device_roles.get.return_value = None
        with self.assertRaises(NetBoxBuildError):
            netbox_client._resolve_role_id(nb)

    def test_resolve_role_id_returns_id(self):
        nb = MagicMock()
        nb.dcim.device_roles.get.return_value = MagicMock(id=3)
        self.assertEqual(netbox_client._resolve_role_id(nb), 3)

    def test_resolve_platform_id_raises_when_missing(self):
        nb = MagicMock()
        nb.dcim.platforms.get.return_value = None
        with self.assertRaises(NetBoxBuildError):
            netbox_client._resolve_platform_id(nb)

    def test_resolve_vrf_id_raises_when_missing(self):
        nb = MagicMock()
        nb.ipam.vrfs.get.return_value = None
        with self.assertRaises(NetBoxBuildError):
            netbox_client._resolve_vrf_id(nb)


@override_settings(NETBOX_URL="https://netbox.example")
class CreateBuildVmTests(SimpleTestCase):
    def _make_nb(self):
        nb = MagicMock()
        nb.dcim.sites.get.return_value = MagicMock(id=1)
        nb.virtualization.clusters.filter.return_value = [MagicMock(id=2)]
        nb.tenancy.tenant_groups.get.return_value = MagicMock(id=3)
        tenant = MagicMock(id=4)
        nb.tenancy.tenants.filter.return_value = [tenant]
        nb.dcim.device_roles.get.return_value = MagicMock(id=5)
        nb.dcim.platforms.get.return_value = MagicMock(id=6)
        nb.ipam.vrfs.get.return_value = MagicMock(id=7)
        vm = MagicMock(id=100)
        nb.virtualization.virtual_machines.create.return_value = vm
        interface = MagicMock(id=200)
        nb.virtualization.interfaces.create.return_value = interface
        ip = MagicMock(id=300, address="10.0.0.5/24")
        nb.ipam.ip_addresses.create.return_value = ip
        return nb, vm, interface, ip

    def _kwargs(self):
        return dict(
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

    @patch("builds.netbox_client._client")
    def test_raises_when_client_unavailable(self, mock_client):
        mock_client.return_value = None
        with self.assertRaises(NetBoxBuildError):
            netbox_client.create_build_vm(**self._kwargs())

    @patch("builds.netbox_client._client")
    def test_happy_path_creates_vm_interface_and_ip(self, mock_client):
        nb, vm, interface, ip = self._make_nb()
        mock_client.return_value = nb

        result = netbox_client.create_build_vm(**self._kwargs())

        nb.virtualization.virtual_machines.create.assert_called_once_with(
            name="rcd09-d1-testvm01",
            site=1,
            cluster=2,
            role=5,
            tenant=4,
            platform=6,
            vcpus=2,
            memory=8192,
            disk=netbox_client.VM_DISK_MB,
        )
        nb.virtualization.interfaces.create.assert_called_once_with(
            virtual_machine=100, name="ens192"
        )
        nb.ipam.ip_addresses.create.assert_called_once_with(
            address="10.0.0.5/24",
            vrf=7,
            tenant=4,
            dns_name="rcd09-d1-testvm01",
            assigned_object_type="virtualization.vminterface",
            assigned_object_id=200,
            status="active",
        )
        self.assertEqual(vm.primary_ip4, 300)
        vm.save.assert_called_once()
        self.assertEqual(
            result["vm_url"],
            "https://netbox.example/virtualization/virtual-machines/100/",
        )

    @patch("builds.netbox_client._client")
    def test_site_not_found_raises_before_creating_anything(self, mock_client):
        nb, _, _, _ = self._make_nb()
        nb.dcim.sites.get.return_value = None
        mock_client.return_value = nb

        with self.assertRaises(NetBoxBuildError):
            netbox_client.create_build_vm(**self._kwargs())
        nb.virtualization.virtual_machines.create.assert_not_called()

    @patch("builds.netbox_client._client")
    def test_unexpected_reference_lookup_error_is_wrapped(self, mock_client):
        nb, _, _, _ = self._make_nb()
        nb.virtualization.clusters.filter.side_effect = RuntimeError("timeout")
        mock_client.return_value = nb

        with self.assertRaisesMessage(NetBoxBuildError, "nothing created yet"):
            netbox_client.create_build_vm(**self._kwargs())

    @patch("builds.netbox_client._client")
    def test_vm_create_failure_raises(self, mock_client):
        nb, _, _, _ = self._make_nb()
        nb.virtualization.virtual_machines.create.side_effect = RuntimeError("boom")
        mock_client.return_value = nb

        with self.assertRaisesMessage(NetBoxBuildError, "Failed to create NetBox VM"):
            netbox_client.create_build_vm(**self._kwargs())

    @patch("builds.netbox_client._client")
    def test_interface_create_failure_mentions_vm_already_created(self, mock_client):
        nb, vm, _, _ = self._make_nb()
        nb.virtualization.interfaces.create.side_effect = RuntimeError("boom")
        mock_client.return_value = nb

        with self.assertRaisesMessage(
            NetBoxBuildError, "was created, but adding interface"
        ):
            netbox_client.create_build_vm(**self._kwargs())

    @patch("builds.netbox_client._client")
    def test_ip_create_failure_mentions_vm_and_interface_already_created(
        self, mock_client
    ):
        nb, vm, interface, _ = self._make_nb()
        nb.ipam.ip_addresses.create.side_effect = RuntimeError("boom")
        mock_client.return_value = nb

        with self.assertRaisesMessage(
            NetBoxBuildError, "and its interface were created, but"
        ):
            netbox_client.create_build_vm(**self._kwargs())

    @patch("builds.netbox_client._client")
    def test_primary_ip_save_failure_mentions_everything_else_already_created(
        self, mock_client
    ):
        nb, vm, interface, ip = self._make_nb()
        vm.save.side_effect = RuntimeError("boom")
        mock_client.return_value = nb

        with self.assertRaisesMessage(NetBoxBuildError, "interface, and IP"):
            netbox_client.create_build_vm(**self._kwargs())
