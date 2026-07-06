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

"""Test cases for guarded write tools."""

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from fastmcp.exceptions import ToolError

from ads_mcp.tools import writes


class Repeated(list):
    """Small helper matching protobuf repeated fields used in tests."""


def operation_with_update():
    return SimpleNamespace(
        update=SimpleNamespace(),
        update_mask=SimpleNamespace(paths=Repeated()),
    )


def mutate_request():
    return SimpleNamespace(
        customer_id="",
        operations=Repeated(),
        validate_only=False,
    )


class TestWrites(unittest.TestCase):
    @patch("ads_mcp.tools.writes.utils.get_googleads_service")
    @patch("ads_mcp.tools.writes.utils.get_googleads_client")
    def test_update_campaign_status_validate_only(
        self, mock_get_client, mock_get_service
    ):
        client = MagicMock()
        client.get_type.side_effect = lambda name: {
            "CampaignOperation": operation_with_update(),
            "MutateCampaignsRequest": mutate_request(),
        }[name]
        client.enums.CampaignStatusEnum.PAUSED = "PAUSED_ENUM"
        mock_get_client.return_value = client

        service = MagicMock()
        service.campaign_path.return_value = "customers/123/campaigns/456"
        service.mutate_campaigns.return_value = SimpleNamespace(results=[])
        mock_get_service.return_value = service

        result = writes.update_campaign_status(
            customer_id="123-456-7890",
            campaign_id="456",
            status="PAUSED",
        )

        self.assertFalse(result["applied"])
        self.assertTrue(result["validated_only"])
        request = service.mutate_campaigns.call_args.kwargs["request"]
        self.assertEqual(request.customer_id, "1234567890")
        self.assertTrue(request.validate_only)
        self.assertEqual(request.operations[0].update.status, "PAUSED_ENUM")
        self.assertEqual(request.operations[0].update_mask.paths, ["status"])

    def test_update_campaign_status_real_write_requires_confirmation(self):
        with self.assertRaisesRegex(ToolError, "confirm_write=true"):
            writes.update_campaign_status(
                customer_id="123",
                campaign_id="456",
                status="PAUSED",
                dry_run=False,
                confirm_write=False,
            )

    @patch("ads_mcp.tools.writes.utils.get_googleads_service")
    @patch("ads_mcp.tools.writes.utils.get_googleads_client")
    def test_update_campaign_budget_decrease_validate_only(
        self, mock_get_client, mock_get_service
    ):
        search_service = MagicMock()
        row = SimpleNamespace(
            campaign=SimpleNamespace(id=456, name="Campaign", status="ENABLED"),
            campaign_budget=SimpleNamespace(
                resource_name="customers/123/campaignBudgets/99",
                name="Budget",
                amount_micros=20000000,
                explicitly_shared=False,
                period="DAILY",
            ),
        )
        search_service.search_stream.return_value = [SimpleNamespace(results=[row])]

        budget_service = MagicMock()
        budget_service.mutate_campaign_budgets.return_value = SimpleNamespace(
            results=[SimpleNamespace(resource_name="customers/123/campaignBudgets/99")]
        )
        mock_get_service.side_effect = lambda name: {
            "GoogleAdsService": search_service,
            "CampaignBudgetService": budget_service,
        }[name]

        client = MagicMock()
        client.get_type.side_effect = lambda name: {
            "CampaignBudgetOperation": operation_with_update(),
            "MutateCampaignBudgetsRequest": mutate_request(),
        }[name]
        mock_get_client.return_value = client

        result = writes.update_campaign_budget(
            customer_id="123",
            campaign_id="456",
            amount_micros=15000000,
            max_daily_budget_amount_micros=20000000,
        )

        self.assertFalse(result["applied"])
        self.assertTrue(result["validated_only"])
        self.assertEqual(result["previous_amount_micros"], 20000000)
        self.assertEqual(result["new_amount_micros"], 15000000)
        request = budget_service.mutate_campaign_budgets.call_args.kwargs["request"]
        self.assertTrue(request.validate_only)
        self.assertEqual(
            request.operations[0].update.resource_name,
            "customers/123/campaignBudgets/99",
        )
        self.assertEqual(request.operations[0].update.amount_micros, 15000000)
        self.assertEqual(request.operations[0].update_mask.paths, ["amount_micros"])

    @patch("ads_mcp.tools.writes._fetch_campaign_budget")
    def test_budget_increase_requires_confirmation(self, mock_fetch):
        mock_fetch.return_value = {
            "campaign_name": "Campaign",
            "budget_resource_name": "customers/123/campaignBudgets/99",
            "amount_micros": 10000000,
            "explicitly_shared": False,
        }

        with self.assertRaisesRegex(ToolError, "confirm_budget_increase=true"):
            writes.update_campaign_budget(
                customer_id="123",
                campaign_id="456",
                amount_micros=10500000,
                max_daily_budget_amount_micros=20000000,
            )

    @patch("ads_mcp.tools.writes._fetch_campaign_budget")
    def test_shared_budget_blocked_by_default(self, mock_fetch):
        mock_fetch.return_value = {
            "campaign_name": "Campaign",
            "budget_resource_name": "customers/123/campaignBudgets/99",
            "amount_micros": 10000000,
            "explicitly_shared": True,
        }

        with self.assertRaisesRegex(ToolError, "explicitly shared budget"):
            writes.update_campaign_budget(
                customer_id="123",
                campaign_id="456",
                amount_micros=9000000,
                max_daily_budget_amount_micros=20000000,
            )

    @patch("ads_mcp.tools.writes.utils.get_googleads_service")
    def test_audit_budget_pitfalls_flags_budget_risks(self, mock_get_service):
        row = SimpleNamespace(
            segments=SimpleNamespace(date="2026-07-05"),
            campaign=SimpleNamespace(id=456, name="Campaign", status="ENABLED"),
            campaign_budget=SimpleNamespace(
                resource_name="customers/123/campaignBudgets/99",
                name="Budget",
                amount_micros=10000000,
                explicitly_shared=True,
            ),
            metrics=SimpleNamespace(
                cost_micros=25000000,
                conversions=0,
                conversions_value=0,
            ),
        )
        service = MagicMock()
        service.search_stream.return_value = [SimpleNamespace(results=[row])]
        mock_get_service.return_value = service

        result = writes.audit_budget_pitfalls(
            customer_id="123",
            lookback_days=7,
            monthly_spend_cap_amount_micros=100000000,
        )

        self.assertEqual(len(result), 1)
        flags = result[0]["flags"]
        self.assertIn("shared_budget_requires_extra_care", flags)
        self.assertIn("single_day_cost_over_2x_average_daily_budget", flags)
        self.assertIn("projected_monthly_cost_above_cap", flags)
        self.assertIn("cost_without_conversions", flags)
