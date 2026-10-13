"""Encrypts a Vault AppRole role_id/secret_id and stores them (as ciphertext) in
~/.api_variables_env as VAULT_ROLE_ID_ENC/VAULT_SECRET_ID_ENC, removing any prior
plaintext VAULT_ROLE_ID/VAULT_SECRET_ID entries. Run once:

    python manage.py set_vault_approle

See casbah/secret_crypto.py for how the encryption key is stored/managed.
"""

from __future__ import annotations

import getpass
import os
from pathlib import Path

from django.core.management.base import BaseCommand
from dotenv import set_key, unset_key

from casbah.secret_crypto import encrypt

API_VARS_PATH = Path.home() / ".api_variables_env"


class Command(BaseCommand):
    help = "Encrypt and store Vault AppRole role_id/secret_id in ~/.api_variables_env"

    def handle(self, *args, **options):
        role_id = getpass.getpass("Vault AppRole role_id: ").strip()
        secret_id = getpass.getpass("Vault AppRole secret_id: ").strip()
        if not role_id or not secret_id:
            self.stderr.write(
                self.style.ERROR("Both role_id and secret_id are required.")
            )
            return

        API_VARS_PATH.touch(exist_ok=True)
        os.chmod(API_VARS_PATH, 0o600)

        set_key(
            str(API_VARS_PATH),
            "VAULT_ROLE_ID_ENC",
            encrypt(role_id),
            quote_mode="never",
        )
        set_key(
            str(API_VARS_PATH),
            "VAULT_SECRET_ID_ENC",
            encrypt(secret_id),
            quote_mode="never",
        )
        unset_key(str(API_VARS_PATH), "VAULT_ROLE_ID")
        unset_key(str(API_VARS_PATH), "VAULT_SECRET_ID")

        self.stdout.write(
            self.style.SUCCESS(
                "Stored encrypted VAULT_ROLE_ID_ENC/VAULT_SECRET_ID_ENC in "
                f"{API_VARS_PATH}. Decryption key is at ~/.casbah/secret.key (chmod 600) "
                "— back up that key file too, since losing it makes these values "
                "unrecoverable."
            )
        )
