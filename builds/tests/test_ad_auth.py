from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from builds.ad_auth import ADBackend

AD_SETTINGS = {
    "AD_LDAP_URL": "ldaps://cll.cloud",
    "AD_LDAP_PORT": 636,
    "AD_BASE_DN": "OU=CLL Team,DC=cll,DC=cloud",
    "AD_SEARCH_ATTRIBUTE": "sAMAccountName",
    "AD_ALLOWED_GROUP_DN": "CN=CASBAH Users,OU=Groups,DC=cll,DC=cloud",
}


def _make_entry(dn="CN=Jane Doe,OU=CLL Team,DC=cll,DC=cloud", member_of=None, **attrs):
    """Builds a fake ldap3 search-result entry (attribute access by name, `in` checks
    via __contains__), matching just enough of ldap3.abstract.entry.Entry's interface
    for ADBackend to work with."""
    values = {
        "distinguishedName": dn,
        "mail": attrs.get("mail", "jdoe@cll.cloud"),
        "givenName": attrs.get("givenName", "Jane"),
        "sn": attrs.get("sn", "Doe"),
        "memberOf": member_of if member_of is not None else [AD_SETTINGS["AD_ALLOWED_GROUP_DN"]],
    }

    class FakeEntry:
        def __contains__(self, name):
            return name in values and bool(values[name])

        def __getitem__(self, name):
            return values[name]

    return FakeEntry()


@override_settings(**AD_SETTINGS)
class ADBackendTests(TestCase):
    def setUp(self):
        self.backend = ADBackend()
        self.enterContext(
            patch(
                "builds.ad_auth.get_ad_bind_credentials",
                return_value=("CN=svc,DC=cll,DC=cloud", "svc-password"),
            )
        )

    def _patch_ldap3(self, search_entries, bind_should_succeed=True):
        """Patches ldap3.Connection so the first `with ldap3.Connection(...)` (the
        service-account bind+search) yields `search_entries`, and the second (the
        user's own credential-check bind) raises LDAPBindError unless
        bind_should_succeed."""
        import ldap3
        from ldap3.core.exceptions import LDAPBindError

        search_conn = MagicMock()
        search_conn.__enter__.return_value = search_conn
        search_conn.__exit__.return_value = False
        search_conn.entries = search_entries

        user_conn = MagicMock()
        user_conn.__enter__.return_value = user_conn
        user_conn.__exit__.return_value = False
        if not bind_should_succeed:
            user_conn.__enter__.side_effect = LDAPBindError("invalid credentials")

        return patch.object(
            ldap3, "Connection", side_effect=[search_conn, user_conn]
        )

    @override_settings(AD_ALLOWED_GROUP_DN="")
    def test_unconfigured_group_denies_everyone(self):
        result = self.backend.authenticate(None, username="jdoe", password="pw")
        self.assertIsNone(result)

    def test_missing_username_or_password_denies(self):
        self.assertIsNone(self.backend.authenticate(None, username="", password="pw"))
        self.assertIsNone(self.backend.authenticate(None, username="jdoe", password=""))

    @patch("builds.ad_auth.get_ad_bind_credentials", return_value=(None, None))
    def test_unreachable_vault_denies(self, _mock_creds):
        result = self.backend.authenticate(None, username="jdoe", password="pw")
        self.assertIsNone(result)

    def test_user_not_found_in_ad_denies(self):
        with self._patch_ldap3(search_entries=[]):
            result = self.backend.authenticate(None, username="jdoe", password="pw")
        self.assertIsNone(result)

    def test_user_not_in_allowed_group_denies(self):
        entry = _make_entry(member_of=["CN=Some Other Group,DC=cll,DC=cloud"])
        with self._patch_ldap3(search_entries=[entry]):
            result = self.backend.authenticate(None, username="jdoe", password="pw")
        self.assertIsNone(result)

    def test_wrong_password_denies(self):
        entry = _make_entry()
        with self._patch_ldap3(search_entries=[entry], bind_should_succeed=False):
            result = self.backend.authenticate(None, username="jdoe", password="wrong")
        self.assertIsNone(result)

    def test_successful_login_creates_user_with_ad_attributes(self):
        entry = _make_entry()
        with self._patch_ldap3(search_entries=[entry]):
            user = self.backend.authenticate(None, username="jdoe", password="correct")

        self.assertIsNotNone(user)
        self.assertEqual(user.username, "jdoe")
        self.assertEqual(user.email, "jdoe@cll.cloud")
        self.assertEqual(user.first_name, "Jane")
        self.assertEqual(user.last_name, "Doe")

    def test_successful_login_updates_stale_attributes_on_existing_user(self):
        get_user_model().objects.create(
            username="jdoe", email="old@cll.cloud", first_name="Old", last_name="Name"
        )
        entry = _make_entry()
        with self._patch_ldap3(search_entries=[entry]):
            user = self.backend.authenticate(None, username="jdoe", password="correct")

        self.assertEqual(user.email, "jdoe@cll.cloud")
        self.assertEqual(user.first_name, "Jane")

    def test_username_is_lowercased(self):
        entry = _make_entry()
        with self._patch_ldap3(search_entries=[entry]):
            user = self.backend.authenticate(None, username="JDoe", password="correct")
        self.assertEqual(user.username, "jdoe")


class GetUserTests(TestCase):
    def test_returns_existing_user(self):
        user = get_user_model().objects.create(username="jdoe")
        self.assertEqual(ADBackend().get_user(user.pk), user)

    def test_returns_none_for_missing_user(self):
        self.assertIsNone(ADBackend().get_user(999999))
