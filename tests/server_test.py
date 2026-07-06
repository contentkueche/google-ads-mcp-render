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
from unittest.mock import patch


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

        self.assertIn("ck-google-ads-mcp.onrender.com", server._http_allowed_hosts())
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
