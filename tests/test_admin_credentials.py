import unittest
from unittest.mock import Mock, patch

from openai_tunnel_kit.admin_credentials import (
    ACCOUNT_NAME, SERVICE_NAME, forget_admin_key, load_admin_key, save_admin_key,
)
from openai_tunnel_kit.config import ConfigError


class FakeKeyringError(Exception):
    pass


class AdminCredentialTests(unittest.TestCase):
    @patch("openai_tunnel_kit.admin_credentials._keyring")
    def test_round_trip_uses_one_global_wallet_entry(self, keyring_factory):
        keyring = Mock()
        keyring.get_password.return_value = "sk-admin"
        keyring_factory.return_value = (keyring, FakeKeyringError)

        save_admin_key("sk-admin")
        self.assertEqual(load_admin_key(), "sk-admin")
        self.assertTrue(forget_admin_key())

        keyring.set_password.assert_called_once_with(SERVICE_NAME, ACCOUNT_NAME, "sk-admin")
        keyring.delete_password.assert_called_once_with(SERVICE_NAME, ACCOUNT_NAME)

    @patch("openai_tunnel_kit.admin_credentials._keyring")
    def test_missing_saved_key_has_actionable_error(self, keyring_factory):
        keyring = Mock()
        keyring.get_password.return_value = None
        keyring_factory.return_value = (keyring, FakeKeyringError)
        with self.assertRaisesRegex(ConfigError, "no admin key is saved"):
            load_admin_key()

    def test_rejects_multiline_key_before_wallet_write(self):
        with self.assertRaisesRegex(ConfigError, "one non-empty line"):
            save_admin_key("sk-admin\nsecond-line")


if __name__ == "__main__":
    unittest.main()
