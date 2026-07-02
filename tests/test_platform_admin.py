import unittest
from unittest.mock import Mock, patch

from openai_tunnel_kit.config import ConfigError
from openai_tunnel_kit.platform_admin import create_service_account, list_projects


class PlatformAdminTests(unittest.TestCase):
    @patch("openai_tunnel_kit.platform_admin._client")
    def test_lists_only_active_projects(self, client):
        response = Mock(status_code=200)
        response.json.return_value = {"data": [
            {"id": "proj_active", "name": "Active", "status": "active"},
            {"id": "proj_old", "name": "Old", "status": "archived"},
        ]}
        client.return_value.request.return_value = response
        self.assertEqual([item["id"] for item in list_projects("sk-admin")], ["proj_active"])

    @patch("openai_tunnel_kit.platform_admin._client")
    def test_service_account_extracts_one_time_key(self, client):
        create_response = Mock(status_code=200)
        create_response.json.return_value = {
            "id": "svc_1",
            "api_key": {"id": "key_1", "value": "sk-runtime"},
        }
        key_response = Mock(status_code=200)
        key_response.json.return_value = {
            "owner": {"service_account": {"id": "svc_canonical"}}
        }
        client.return_value.request.side_effect = [create_response, key_response]
        credential = create_service_account("sk-admin", "proj_1", "runtime")
        self.assertEqual(credential.api_key, "sk-runtime")
        self.assertEqual(credential.service_account_id, "svc_canonical")
        for call in client.return_value.request.call_args_list:
            self.assertNotIn("sk-admin", str(call.kwargs.get("json")))

    @patch("openai_tunnel_kit.platform_admin._client")
    def test_admin_error_is_actionable_without_echoing_key(self, client):
        response = Mock(status_code=403, text="forbidden")
        response.json.return_value = {"error": {"message": "owner role required"}}
        client.return_value.request.return_value = response
        with self.assertRaisesRegex(ConfigError, "owner role required") as raised:
            list_projects("sk-secret-admin")
        self.assertNotIn("sk-secret-admin", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
