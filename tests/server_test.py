# Copyright 2026 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test cases for the server module."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mcp.server.auth.provider import TokenError

from ads_mcp import coordinator


class TestUtils(unittest.TestCase):
    """Test cases for the server module."""

    def test_server_initialization(self):
        """Tests that the MCP server instance is initialized.

        This servers as a smoke test to confirm there are no obvious issues
        with initialization, such as missing imports.
        """
        from ads_mcp import server

        self.assertIsNotNone(server.mcp, "MCP server instance not initialized")

    @patch.dict(
        "os.environ",
        {"GOOGLE_ADS_MCP_BASE_URL": "https://ck-google-ads-mcp.onrender.com"},
    )
    def test_http_allowed_hosts_include_public_base_url(self):
        from ads_mcp import server

        self.assertIn(
            "ck-google-ads-mcp.onrender.com", server._http_allowed_hosts()
        )
        self.assertIn(
            "https://ck-google-ads-mcp.onrender.com",
            server._http_allowed_origins(),
        )

    @patch.dict(
        "os.environ",
        {
            "GOOGLE_ADS_MCP_BASE_URL": "https://ck-google-ads-mcp.onrender.com",
            "GOOGLE_ADS_MCP_ALLOWED_HOSTS": "custom.example.com",
            "GOOGLE_ADS_MCP_ALLOWED_ORIGINS": "https://custom.example.com",
        },
    )
    def test_http_allowed_hosts_preserve_env_overrides(self):
        from ads_mcp import server

        self.assertEqual(
            server._http_allowed_hosts(),
            ["custom.example.com", "ck-google-ads-mcp.onrender.com"],
        )
        self.assertEqual(
            server._http_allowed_origins(),
            [
                "https://custom.example.com",
                "https://ck-google-ads-mcp.onrender.com",
            ],
        )

    def test_missing_required_google_scopes_detects_ads_scope(self):
        missing = coordinator._missing_required_google_scopes(
            required_scopes=coordinator._REQUIRED_GOOGLE_SCOPES,
            granted_scope_value=(
                "email profile "
                "https://www.googleapis.com/auth/userinfo.email openid"
            ),
            requested_scopes=coordinator._REQUIRED_GOOGLE_SCOPES,
        )

        self.assertEqual(
            missing,
            [
                "https://www.googleapis.com/auth/adwords",
            ],
        )

    def test_missing_required_google_scopes_accepts_aliases(self):
        missing = coordinator._missing_required_google_scopes(
            required_scopes=coordinator._REQUIRED_GOOGLE_SCOPES,
            granted_scope_value=(
                "email profile openid "
                "https://www.googleapis.com/auth/adwords"
            ),
            requested_scopes=[],
        )

        self.assertEqual(missing, [])


class AsyncStore:
    def __init__(self, value):
        self.value = value
        self.deleted_key = None

    async def get(self, key):
        return self.value

    async def delete(self, key):
        self.deleted_key = key


class TestGoogleAdsProvider(unittest.IsolatedAsyncioTestCase):
    async def test_exchange_authorization_code_rejects_partial_grant(self):
        provider = object.__new__(coordinator.GoogleAdsProvider)
        provider.required_scopes = coordinator._REQUIRED_GOOGLE_SCOPES
        provider._code_store = AsyncStore(
            SimpleNamespace(
                idp_tokens={
                    "scope": (
                        "email profile "
                        "https://www.googleapis.com/auth/userinfo.email openid"
                    )
                }
            )
        )

        with self.assertRaises(TokenError) as raised:
            await provider.exchange_authorization_code(
                client=SimpleNamespace(),
                authorization_code=SimpleNamespace(
                    code="auth-code",
                    scopes=coordinator._REQUIRED_GOOGLE_SCOPES,
                ),
            )

        self.assertEqual(raised.exception.error, "invalid_scope")
        self.assertIn("auth/adwords", raised.exception.error_description)
        self.assertEqual(provider._code_store.deleted_key, "auth-code")
