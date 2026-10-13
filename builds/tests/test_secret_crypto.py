import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from casbah import secret_crypto


class SecretCryptoTests(SimpleTestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        key_path = Path(self._tmpdir.name) / "secret.key"
        self.enterContext(patch("casbah.secret_crypto.KEY_PATH", key_path))

    def test_encrypt_then_decrypt_round_trips(self):
        ciphertext = secret_crypto.encrypt("super-secret-value")
        self.assertNotEqual(ciphertext, "super-secret-value")
        self.assertEqual(secret_crypto.decrypt(ciphertext), "super-secret-value")

    def test_key_file_created_with_restrictive_permissions(self):
        secret_crypto.encrypt("value")
        mode = secret_crypto.KEY_PATH.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_reuses_existing_key_across_calls(self):
        first = secret_crypto.encrypt("value-one")
        second = secret_crypto.encrypt("value-two")
        # Both must decrypt correctly under the one key that was generated once.
        self.assertEqual(secret_crypto.decrypt(first), "value-one")
        self.assertEqual(secret_crypto.decrypt(second), "value-two")

    def test_decrypt_returns_none_for_garbage_input(self):
        secret_crypto.encrypt("bootstrap the key file")  # ensure a key exists
        self.assertIsNone(secret_crypto.decrypt("not-a-valid-token"))

    def test_decrypt_returns_none_for_empty_string(self):
        secret_crypto.encrypt("bootstrap the key file")
        self.assertIsNone(secret_crypto.decrypt(""))
