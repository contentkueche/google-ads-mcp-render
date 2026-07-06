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

"""Tests for persistent FastMCP client OAuth storage."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp.client.auth.oauth import TokenStorageAdapter
from key_value.aio.stores.memory import MemoryStore

from ads_mcp import fastmcp_client


class TestFastMCPClientHelpers(unittest.TestCase):
    def test_default_mcp_url_prefers_client_url(self):
        with patch.dict(
            "os.environ",
            {
                fastmcp_client.CLIENT_URL_ENV: "https://example.com/mcp",
                fastmcp_client.SERVER_BASE_URL_ENV: "https://ignored.example.com",
            },
            clear=True,
        ):
            self.assertEqual(
                fastmcp_client.default_mcp_url(), "https://example.com/mcp"
            )

    def test_default_mcp_url_uses_server_base_url(self):
        with patch.dict(
            "os.environ",
            {fastmcp_client.SERVER_BASE_URL_ENV: "https://example.com/"},
            clear=True,
        ):
            self.assertEqual(
                fastmcp_client.default_mcp_url(), "https://example.com/mcp"
            )

    def test_oauth_uses_persistent_store_and_requested_scopes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            oauth = fastmcp_client.create_google_ads_oauth(
                "https://ck-google-ads-mcp.onrender.com/mcp",
                token_store_dir=temp_dir,
                callback_port=8765,
                callback_timeout=15,
            )

            self.assertEqual(oauth._scopes, fastmcp_client.GOOGLE_ADS_MCP_OAUTH_SCOPES)
            self.assertEqual(oauth._callback_port, 8765)
            self.assertEqual(oauth._callback_timeout, 15)
            self.assertIsInstance(oauth.token_storage_adapter, TokenStorageAdapter)
            self.assertNotIsInstance(
                oauth.token_storage_adapter._key_value_store, MemoryStore
            )


class TestPersistentTokenStore(unittest.IsolatedAsyncioTestCase):
    async def test_store_reuses_values_with_hashed_file_names(self):
        url_key = "https://ck-google-ads-mcp.onrender.com/mcp/tokens"

        with tempfile.TemporaryDirectory() as temp_dir:
            store = fastmcp_client.build_oauth_token_store(temp_dir)

            await store.put(
                url_key,
                {"access_token": "secret-token"},
                collection="mcp-oauth-token",
                ttl=3600,
            )

            self.assertEqual(
                await store.get(url_key, collection="mcp-oauth-token"),
                {"access_token": "secret-token"},
            )

            json_files = [path.name for path in Path(temp_dir).rglob("*.json")]
            self.assertGreater(len(json_files), 0)
            for file_name in json_files:
                self.assertNotIn("ck-google-ads-mcp", file_name)
                self.assertNotIn("onrender", file_name)
                self.assertNotIn("mcp-oauth-token", file_name)
                self.assertNotIn("tokens", file_name)

    async def test_store_directory_is_private_on_posix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            token_store_dir = Path(temp_dir) / "oauth"
            fastmcp_client.build_oauth_token_store(token_store_dir)

            if fastmcp_client.os.name != "nt":
                self.assertEqual(
                    token_store_dir.stat().st_mode & 0o777,
                    0o700,
                )
