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


def request_with_operations():
    return SimpleNamespace(
        customer_id="",
        operations=Repeated(),
        validate_only=False,
    )


def mutate_google_ads_request():
    return SimpleNamespace(
        customer_id="",
        mutate_operations=Repeated(),
        partial_failure=True,
        validate_only=False,
        response_content_type=None,
    )


def mutate_operation():
    return SimpleNamespace()


def operation_with_update():
    return SimpleNamespace(
        update=SimpleNamespace(),
        update_mask=SimpleNamespace(paths=Repeated()),
    )


def campaign_budget_operation_with_create():
    return SimpleNamespace(create=SimpleNamespace())


def campaign_operation_with_create():
    return SimpleNamespace(
        create=SimpleNamespace(
            network_settings=SimpleNamespace(),
            target_spend=SimpleNamespace(),
        )
    )


def ad_group_operation_with_create():
    return SimpleNamespace(create=SimpleNamespace())


def ad_group_criterion_operation_with_create():
    return SimpleNamespace(create=SimpleNamespace(keyword=SimpleNamespace()))


def ad_group_ad_operation_with_create():
    return SimpleNamespace(
        create=SimpleNamespace(
            ad=SimpleNamespace(
                final_urls=Repeated(),
                responsive_search_ad=SimpleNamespace(
                    headlines=Repeated(),
                    descriptions=Repeated(),
                ),
            )
        )
    )


def campaign_criterion_operation_with_create():
    return SimpleNamespace(
        create=SimpleNamespace(
            location=SimpleNamespace(),
            language=SimpleNamespace(),
        )
    )


def mutate_request():
    return SimpleNamespace(
        customer_id="",
        operations=Repeated(),
        validate_only=False,
    )


def fake_enums():
    return SimpleNamespace(
        CampaignStatusEnum=SimpleNamespace(
            ENABLED="CAMPAIGN_ENABLED",
            PAUSED="CAMPAIGN_PAUSED",
        ),
        AdGroupStatusEnum=SimpleNamespace(
            ENABLED="AD_GROUP_ENABLED",
            PAUSED="AD_GROUP_PAUSED",
        ),
        AdGroupAdStatusEnum=SimpleNamespace(
            ENABLED="AD_GROUP_AD_ENABLED",
            PAUSED="AD_GROUP_AD_PAUSED",
        ),
        AdGroupCriterionStatusEnum=SimpleNamespace(
            ENABLED="CRITERION_ENABLED",
            PAUSED="CRITERION_PAUSED",
        ),
        BudgetDeliveryMethodEnum=SimpleNamespace(STANDARD="STANDARD"),
        AdvertisingChannelTypeEnum=SimpleNamespace(SEARCH="SEARCH"),
        EuPoliticalAdvertisingStatusEnum=SimpleNamespace(
            CONTAINS_EU_POLITICAL_ADVERTISING="CONTAINS_EU",
            DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING="DOES_NOT_CONTAIN_EU",
        ),
        AdGroupTypeEnum=SimpleNamespace(SEARCH_STANDARD="SEARCH_STANDARD"),
        KeywordMatchTypeEnum=SimpleNamespace(
            BROAD="BROAD_MATCH",
            PHRASE="PHRASE_MATCH",
            EXACT="EXACT_MATCH",
        ),
        ResponseContentTypeEnum=SimpleNamespace(
            RESOURCE_NAME_ONLY="RESOURCE_NAME_ONLY"
        ),
    )


def fake_create_client():
    client = MagicMock()
    client.enums = fake_enums()

    def get_type(name):
        return {
            "CampaignBudgetOperation": campaign_budget_operation_with_create,
            "CampaignOperation": campaign_operation_with_create,
            "AdGroupOperation": ad_group_operation_with_create,
            "AdGroupCriterionOperation": ad_group_criterion_operation_with_create,
            "AdGroupAdOperation": ad_group_ad_operation_with_create,
            "CampaignCriterionOperation": campaign_criterion_operation_with_create,
            "AdTextAsset": SimpleNamespace,
            "ManualCpc": SimpleNamespace,
            "MutateCampaignBudgetsRequest": request_with_operations,
            "MutateCampaignsRequest": request_with_operations,
            "MutateAdGroupsRequest": request_with_operations,
            "MutateAdGroupCriteriaRequest": request_with_operations,
            "MutateAdGroupAdsRequest": request_with_operations,
            "MutateGoogleAdsRequest": mutate_google_ads_request,
            "MutateOperation": mutate_operation,
        }[name]()

    client.get_type.side_effect = get_type
    return client


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
        search_service.search_stream.return_value = [
            SimpleNamespace(results=[row])
        ]

        budget_service = MagicMock()
        budget_service.mutate_campaign_budgets.return_value = SimpleNamespace(
            results=[
                SimpleNamespace(
                    resource_name="customers/123/campaignBudgets/99"
                )
            ]
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
        request = budget_service.mutate_campaign_budgets.call_args.kwargs[
            "request"
        ]
        self.assertTrue(request.validate_only)
        self.assertEqual(
            request.operations[0].update.resource_name,
            "customers/123/campaignBudgets/99",
        )
        self.assertEqual(request.operations[0].update.amount_micros, 15000000)
        self.assertEqual(
            request.operations[0].update_mask.paths, ["amount_micros"]
        )

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

    @patch("ads_mcp.tools.writes.utils.get_googleads_service")
    @patch("ads_mcp.tools.writes.utils.get_googleads_client")
    def test_create_campaign_budget_validate_only(
        self, mock_get_client, mock_get_service
    ):
        client = fake_create_client()
        mock_get_client.return_value = client

        budget_service = MagicMock()
        budget_service.mutate_campaign_budgets.return_value = SimpleNamespace(
            results=[
                SimpleNamespace(
                    resource_name="customers/123/campaignBudgets/99"
                )
            ]
        )
        mock_get_service.return_value = budget_service

        result = writes.create_campaign_budget(
            customer_id="123-456-7890",
            name="Launch budget",
            amount_micros=10000000,
            max_daily_budget_amount_micros=20000000,
        )

        self.assertFalse(result["applied"])
        self.assertTrue(result["validated_only"])
        request = budget_service.mutate_campaign_budgets.call_args.kwargs[
            "request"
        ]
        self.assertEqual(request.customer_id, "1234567890")
        self.assertTrue(request.validate_only)
        budget = request.operations[0].create
        self.assertEqual(budget.name, "Launch budget")
        self.assertEqual(budget.amount_micros, 10000000)
        self.assertFalse(budget.explicitly_shared)

    def test_create_campaign_budget_blocks_amount_above_cap(self):
        with self.assertRaisesRegex(ToolError, "exceeds"):
            writes.create_campaign_budget(
                customer_id="123",
                name="Too high",
                amount_micros=30000000,
                max_daily_budget_amount_micros=20000000,
            )

    @patch("ads_mcp.tools.writes._fetch_campaign_budget_by_resource_name")
    @patch("ads_mcp.tools.writes.utils.get_googleads_service")
    @patch("ads_mcp.tools.writes.utils.get_googleads_client")
    def test_create_search_campaign_uses_existing_budget_guardrails(
        self,
        mock_get_client,
        mock_get_service,
        mock_fetch_budget,
    ):
        mock_fetch_budget.return_value = {
            "budget_resource_name": "customers/123/campaignBudgets/99",
            "budget_name": "Budget",
            "amount_micros": 10000000,
            "explicitly_shared": False,
            "period": "DAILY",
        }
        client = fake_create_client()
        mock_get_client.return_value = client

        campaign_service = MagicMock()
        campaign_service.mutate_campaigns.return_value = SimpleNamespace(
            results=[]
        )
        mock_get_service.return_value = campaign_service

        result = writes.create_search_campaign(
            customer_id="123",
            name="Launch campaign",
            budget_resource_name="customers/123/campaignBudgets/99",
            max_daily_budget_amount_micros=20000000,
            start_date_time="2026-07-07",
            end_date_time="2026-08-07",
        )

        self.assertFalse(result["applied"])
        request = campaign_service.mutate_campaigns.call_args.kwargs["request"]
        campaign = request.operations[0].create
        self.assertEqual(campaign.name, "Launch campaign")
        self.assertEqual(campaign.status, "CAMPAIGN_PAUSED")
        self.assertEqual(
            campaign.campaign_budget, "customers/123/campaignBudgets/99"
        )
        self.assertEqual(campaign.start_date_time, "20260707 00:00:00")
        self.assertEqual(campaign.end_date_time, "20260807 23:59:59")
        self.assertFalse(campaign.network_settings.target_content_network)

    def test_create_responsive_search_ad_requires_minimum_assets(self):
        with self.assertRaisesRegex(ToolError, "at least 3 headlines"):
            writes.create_responsive_search_ad(
                customer_id="123",
                ad_group_resource_name="customers/123/adGroups/456",
                final_urls=["https://example.com"],
                headlines=["One", "Two"],
                descriptions=["Desc one", "Desc two"],
            )

    @patch("ads_mcp.tools.writes.utils.get_googleads_service")
    @patch("ads_mcp.tools.writes.utils.get_googleads_client")
    def test_create_search_campaign_bundle_builds_atomic_mutate(
        self, mock_get_client, mock_get_service
    ):
        client = fake_create_client()
        mock_get_client.return_value = client

        google_ads_service = MagicMock()
        google_ads_service.mutate.return_value = SimpleNamespace(
            mutate_operation_responses=[]
        )
        mock_get_service.return_value = google_ads_service

        result = writes.create_search_campaign_bundle(
            customer_id="123-456-7890",
            campaign_name="Launch",
            budget_name="Launch budget",
            budget_amount_micros=10000000,
            max_daily_budget_amount_micros=20000000,
            ad_group_name="Core",
            final_urls=["https://example.com"],
            headlines=["Alpha", "Beta", "Gamma"],
            descriptions=["First description", "Second description"],
            keyword_texts=["alpha service", "beta service"],
            geo_target_constant_resource_names=["geoTargetConstants/2276"],
            language_constant_resource_names=["languageConstants/1001"],
            path1="services",
            path2="launch",
        )

        self.assertFalse(result["applied"])
        self.assertEqual(result["operation_count"], 8)
        self.assertEqual(
            result["temporary_resource_names"]["campaign"],
            "customers/1234567890/campaigns/-2",
        )
        request = google_ads_service.mutate.call_args.kwargs["request"]
        self.assertTrue(request.validate_only)
        self.assertFalse(request.partial_failure)
        self.assertEqual(len(request.mutate_operations), 8)

        budget = request.mutate_operations[0].campaign_budget_operation.create
        campaign = request.mutate_operations[1].campaign_operation.create
        location = request.mutate_operations[
            2
        ].campaign_criterion_operation.create
        language = request.mutate_operations[
            3
        ].campaign_criterion_operation.create
        ad_group = request.mutate_operations[4].ad_group_operation.create
        keyword = request.mutate_operations[
            5
        ].ad_group_criterion_operation.create
        ad_group_ad = request.mutate_operations[7].ad_group_ad_operation.create

        self.assertEqual(
            budget.resource_name, "customers/1234567890/campaignBudgets/-1"
        )
        self.assertFalse(budget.explicitly_shared)
        self.assertEqual(campaign.status, "CAMPAIGN_PAUSED")
        self.assertEqual(campaign.campaign_budget, budget.resource_name)
        self.assertEqual(ad_group.campaign, campaign.resource_name)
        self.assertEqual(location.campaign, campaign.resource_name)
        self.assertEqual(
            location.location.geo_target_constant, "geoTargetConstants/2276"
        )
        self.assertEqual(
            language.language.language_constant, "languageConstants/1001"
        )
        self.assertEqual(keyword.ad_group, ad_group.resource_name)
        self.assertEqual(keyword.keyword.match_type, "BROAD_MATCH")
        self.assertEqual(ad_group_ad.ad_group, ad_group.resource_name)
        self.assertEqual(ad_group_ad.ad.final_urls, ["https://example.com"])
        self.assertEqual(
            [
                asset.text
                for asset in ad_group_ad.ad.responsive_search_ad.headlines
            ],
            ["Alpha", "Beta", "Gamma"],
        )
