"""Active Directory authentication backend (LDAP simple bind via ldap3) — see
ai_prompt_revised.txt "AUTH / ACCESS CONTROL". Built as a pluggable Django
authentication backend (see AUTHENTICATION_BACKENDS in casbah/settings.py) so the
real mechanism can be swapped out later (OIDC/SAML/IWA) without touching views.

Bind-search-rebind flow on every login attempt:
  1. Bind as CASBAH's own LDAP service account (Vault-sourced, see
     builds.vault_client.get_ad_bind_credentials) to search AD for the submitted
     username's DN (AD_SEARCH_ATTRIBUTE, e.g. sAMAccountName) and group memberships.
  2. Re-bind as that user's own DN with the submitted password — the service account's
     bind never proves the *user's* password is correct, only this second bind does.
  3. Deny unless the user is a member of AD_ALLOWED_GROUP_DN.

FAILS CLOSED: an unconfigured AD_ALLOWED_GROUP_DN, an unreachable Vault/AD, or any
LDAP error denies the login attempt — it does NOT mean "allow everyone". This is the
opposite of the "can't verify, don't block" fallback policy used elsewhere in this
codebase (vm_name/IP uniqueness, vCenter/NetBox dropdown data) — those gate a build
form nicety, this gates who can use the app at all.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend

from .vault_client import get_ad_bind_credentials

logger = logging.getLogger(__name__)


def _attr(entry, name: str) -> str:
    return str(entry[name]) if name in entry and entry[name] else ""


class ADBackend(BaseBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        import ldap3
        from ldap3.core.exceptions import LDAPBindError, LDAPException

        if not username or not password:
            return None
        if not settings.AD_ALLOWED_GROUP_DN:
            logger.error(
                "AD_ALLOWED_GROUP_DN isn't configured — refusing all AD logins "
                "(fail closed, see builds/ad_auth.py)."
            )
            return None

        bind_dn, bind_password = get_ad_bind_credentials()
        if not bind_dn or not bind_password:
            return None

        # cll.cloud's DNS round-robins across multiple domain controllers, and at
        # least one (as of 2026-09-29) silently drops packets instead of refusing —
        # an explicit connect_timeout keeps a single bad DC from stalling a whole
        # login for the OS-default TCP timeout (30s+) before ldap3 tries the next one.
        server = ldap3.Server(
            settings.AD_LDAP_URL,
            port=settings.AD_LDAP_PORT,
            use_ssl=True,
            connect_timeout=settings.AD_CONNECT_TIMEOUT,
        )
        escaped_username = ldap3.utils.conv.escape_filter_chars(username)
        try:
            with ldap3.Connection(
                server, user=bind_dn, password=bind_password, auto_bind=True
            ) as conn:
                conn.search(
                    settings.AD_BASE_DN,
                    f"({settings.AD_SEARCH_ATTRIBUTE}={escaped_username})",
                    attributes=["distinguishedName", "mail", "givenName", "sn", "memberOf"],
                )
                if not conn.entries:
                    return None
                entry = conn.entries[0]
                user_dn = _attr(entry, "distinguishedName")
                member_of = list(entry["memberOf"]) if "memberOf" in entry else []
        except LDAPException:
            logger.warning("AD service-account bind/search failed.", exc_info=True)
            return None

        if settings.AD_ALLOWED_GROUP_DN not in member_of:
            return None

        try:
            with ldap3.Connection(server, user=user_dn, password=password, auto_bind=True):
                pass  # a successful bind IS the credential check, nothing else to do
        except LDAPBindError:
            return None
        except LDAPException:
            logger.warning("AD user bind failed unexpectedly.", exc_info=True)
            return None

        User = get_user_model()
        user, _created = User.objects.get_or_create(username=username.lower())
        email = _attr(entry, "mail")
        first_name = _attr(entry, "givenName")
        last_name = _attr(entry, "sn")
        if (user.email, user.first_name, user.last_name) != (email, first_name, last_name):
            user.email = email
            user.first_name = first_name
            user.last_name = last_name
            user.save(update_fields=["email", "first_name", "last_name"])
        return user

    def get_user(self, user_id):
        User = get_user_model()
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
