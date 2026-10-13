"""Read-only AD/LDAP diagnostic — verify connectivity, resolve a group name to its
exact DN, and check a user's membership BEFORE wiring real values into
AD_ALLOWED_GROUP_DN / Vault's AD_BIND_ACCOUNT_PATH secret (see builds/ad_auth.py).

Does NOT touch Vault and does NOT create/modify any Django User — this only binds to
AD with whatever credentials you provide at the prompt and prints what it finds.

Usage (run this yourself — it prompts for your password via getpass, never as a CLI
arg, and never echoes it):
    python manage.py ad_lookup --username jdoe
    python manage.py ad_lookup --username jdoe --group System_Administrator

Or, to test the REAL production bind account (builds.vault_client.
get_ad_bind_credentials) without ever typing/seeing its password in this session:
    python manage.py ad_lookup --username jdoe --from-vault
"""

from __future__ import annotations

import getpass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Read-only AD/LDAP diagnostic: binds with the given credentials, looks up a "
        "user (prints their DN + group memberships), and optionally resolves a named "
        "group to its DN and checks the user's membership against it."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            required=True,
            help="sAMAccountName to look up (also used as the bind account if --bind-dn isn't given).",
        )
        parser.add_argument(
            "--bind-dn",
            default=None,
            help="Identity to bind as for the search. Defaults to '<username>@cll.cloud'.",
        )
        parser.add_argument(
            "--group",
            default=None,
            help="Group CN to resolve to a full DN and check --username's membership against.",
        )
        parser.add_argument(
            "--from-vault",
            action="store_true",
            help=(
                "Source the bind account from Vault (builds.vault_client."
                "get_ad_bind_credentials) instead of prompting — tests the actual "
                "production bind account/code path. Ignores --bind-dn; never prompts "
                "for a password."
            ),
        )

    def handle(self, *args, **options):
        import ldap3
        from ldap3.core.exceptions import LDAPException

        username = options["username"]
        if options["from_vault"]:
            from builds.vault_client import AD_BIND_ACCOUNT_PATH, get_ad_bind_credentials

            bind_dn, password = get_ad_bind_credentials()
            if not bind_dn or not password:
                raise CommandError(
                    f"Vault has no usable secret at '{AD_BIND_ACCOUNT_PATH}' "
                    "(missing bind_dn/password, or Vault is unreachable — check VPN)."
                )
            self.stdout.write(f"Using bind account from Vault: {bind_dn}")
        else:
            bind_dn = options["bind_dn"] or f"{username}@cll.cloud"
            password = getpass.getpass(f"Password for bind account ({bind_dn}): ")

        self.stdout.write(
            f"Connecting to {settings.AD_LDAP_URL}:{settings.AD_LDAP_PORT}, "
            f"base DN '{settings.AD_BASE_DN}'..."
        )
        server = ldap3.Server(
            settings.AD_LDAP_URL,
            port=settings.AD_LDAP_PORT,
            use_ssl=True,
            connect_timeout=settings.AD_CONNECT_TIMEOUT,
        )
        try:
            conn = ldap3.Connection(
                server, user=bind_dn, password=password, auto_bind=True
            )
        except LDAPException as exc:
            raise CommandError(f"Bind failed: {exc}")

        with conn:
            conn.search(
                settings.AD_BASE_DN,
                f"({settings.AD_SEARCH_ATTRIBUTE}="
                f"{ldap3.utils.conv.escape_filter_chars(username)})",
                attributes=["distinguishedName", "mail", "memberOf"],
            )
            if not conn.entries:
                raise CommandError(
                    f"No entry found for '{username}' under base DN "
                    f"'{settings.AD_BASE_DN}' — is AD_BASE_DN correct for this user?"
                )
            entry = conn.entries[0]
            user_dn = str(entry["distinguishedName"])
            member_of = list(entry["memberOf"]) if "memberOf" in entry else []

            self.stdout.write(self.style.SUCCESS(f"Found: {user_dn}"))
            self.stdout.write(f"Member of {len(member_of)} group(s):")
            for dn in member_of:
                self.stdout.write(f"  {dn}")

            group_name = options["group"]
            if not group_name:
                return

            conn.search(
                settings.AD_BASE_DN,
                f"(cn={ldap3.utils.conv.escape_filter_chars(group_name)})",
                attributes=["distinguishedName"],
            )
            if not conn.entries:
                self.stdout.write(
                    self.style.WARNING(f"No group found with cn='{group_name}'.")
                )
                return
            group_dn = str(conn.entries[0]["distinguishedName"])
            self.stdout.write(f"Resolved group DN: {group_dn}")
            if group_dn in member_of:
                self.stdout.write(
                    self.style.SUCCESS(f"'{username}' IS a member of '{group_name}'.")
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"'{username}' is NOT a member of '{group_name}'."
                    )
                )
