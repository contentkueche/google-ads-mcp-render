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

"""Guarded Google Ads budget and write tools."""

from datetime import datetime
from typing import Any, Dict, Iterable, List, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from mcp.types import ToolAnnotations

import ads_mcp.utils as utils

budget_mcp = FastMCP("budget")
writes_mcp = FastMCP("writes")

CampaignStatus = Literal["ENABLED", "PAUSED"]
EntityStatus = Literal["ENABLED", "PAUSED"]
KeywordMatchType = Literal["BROAD", "PHRASE", "EXACT"]
PositiveGeoTargetType = Literal[
    "PRESENCE",
    "PRESENCE_OR_INTEREST",
    "SEARCH_INTEREST",
]
NegativeGeoTargetType = Literal["PRESENCE", "PRESENCE_OR_INTEREST"]
ProximityRadiusUnit = Literal["KILOMETERS", "MILES"]
EuPoliticalAdvertisingStatus = Literal[
    "CONTAINS_EU_POLITICAL_ADVERTISING",
    "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
]
BiddingStrategyType = Literal["MANUAL_CPC", "TARGET_SPEND"]
ConversionActionStatus = Literal["ENABLED", "PAUSED"]
ConversionActionCategory = Literal[
    "SUBMIT_LEAD_FORM",
    "CONTACT",
    "QUALIFIED_LEAD",
]
ConversionActionCountingType = Literal["ONE_PER_CLICK", "MANY_PER_CLICK"]
ConversionGoalOrigin = Literal[
    "WEBSITE",
    "GOOGLE_HOSTED",
    "CALL_FROM_ADS",
    "YOUTUBE_HOSTED",
]

_TEMP_BUDGET_RESOURCE_NAME = "customers/{customer_id}/campaignBudgets/-1"
_TEMP_CAMPAIGN_RESOURCE_NAME = "customers/{customer_id}/campaigns/-2"
_TEMP_AD_GROUP_RESOURCE_NAME = "customers/{customer_id}/adGroups/-3"


def _normalize_customer_id(customer_id: str) -> str:
    normalized = customer_id.replace("-", "").strip()
    if not normalized.isdigit():
        raise ToolError("customer_id must contain only digits or hyphens.")
    return normalized


def _normalize_id(value: str, name: str) -> str:
    normalized = value.replace("-", "").strip()
    if not normalized.isdigit():
        raise ToolError(f"{name} must contain only digits or hyphens.")
    return normalized


def _validate_customer_resource_name(
    resource_name: str,
    name: str,
    customer_id: str,
    collection: str,
) -> str:
    normalized = resource_name.strip()
    prefix = f"customers/{customer_id}/{collection}/"
    if not normalized.startswith(prefix):
        raise ToolError(
            f"{name} must be a {collection} resource name under "
            f"customers/{customer_id}."
        )
    resource_id = normalized.removeprefix(prefix)
    if not resource_id or not resource_id.lstrip("-").isdigit():
        raise ToolError(f"{name} has an invalid resource ID.")
    return normalized


def _validate_customer_composite_resource_name(
    resource_name: str,
    name: str,
    customer_id: str,
    collection: str,
) -> str:
    normalized = resource_name.strip()
    prefix = f"customers/{customer_id}/{collection}/"
    if not normalized.startswith(prefix):
        raise ToolError(
            f"{name} must be a {collection} resource name under "
            f"customers/{customer_id}."
        )
    resource_id = normalized.removeprefix(prefix)
    parts = resource_id.split("~")
    if len(parts) != 2 or any(not part.isdigit() for part in parts):
        raise ToolError(f"{name} has an invalid composite resource ID.")
    return normalized


def _validate_constant_resource_name(
    resource_name: str,
    name: str,
    collection: str,
) -> str:
    normalized = resource_name.strip()
    prefix = f"{collection}/"
    if not normalized.startswith(prefix):
        raise ToolError(f"{name} must start with {prefix}.")
    resource_id = normalized.removeprefix(prefix)
    if not resource_id or not resource_id.isdigit():
        raise ToolError(f"{name} has an invalid resource ID.")
    return normalized


def _google_ads_tool_error(ex: GoogleAdsException) -> ToolError:
    lines = [f"Request ID: {ex.request_id}"]
    for error in ex.failure.errors:
        lines.append(f"Google Ads API Error: {error.message}")
        if error.location:
            for field_path_element in error.location.field_path_elements:
                lines.append(f"On field: {field_path_element.field_name}")
    return ToolError("\n".join(lines))


def _result_resource_names(response: Any) -> List[str]:
    resource_names = [
        result.resource_name
        for result in getattr(response, "results", [])
        if getattr(result, "resource_name", None)
    ]

    for operation_response in getattr(
        response, "mutate_operation_responses", []
    ):
        field_names = getattr(
            getattr(operation_response, "_meta", None), "fields", {}
        )
        if field_names:
            candidates: Iterable[str] = field_names.keys()
        else:
            candidates = (
                name
                for name in dir(operation_response)
                if name.endswith("_result") and not name.startswith("_")
            )
        for field_name in candidates:
            result = getattr(operation_response, field_name, None)
            resource_name = getattr(result, "resource_name", None)
            if resource_name:
                resource_names.append(resource_name)

    return resource_names


def _require_confirmed_write(
    dry_run: bool, confirm_write: bool, action: str
) -> None:
    if not dry_run and not confirm_write:
        raise ToolError(
            f"Set confirm_write=true with dry_run=false to {action}."
        )


def _validate_name(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ToolError(f"{name} must not be empty.")
    if len(normalized) > 255:
        raise ToolError(f"{name} must be 255 characters or fewer.")
    return normalized


def _validate_positive_micros(value: int, name: str) -> None:
    if value <= 0:
        raise ToolError(f"{name} must be greater than 0.")


def _validate_budget_cap(
    amount_micros: int,
    max_daily_budget_amount_micros: int,
    monthly_spend_cap_amount_micros: int | None,
) -> None:
    _validate_positive_micros(amount_micros, "amount_micros")
    _validate_positive_micros(
        max_daily_budget_amount_micros,
        "max_daily_budget_amount_micros",
    )
    if amount_micros > max_daily_budget_amount_micros:
        raise ToolError(
            "Budget creation blocked: amount_micros exceeds "
            "max_daily_budget_amount_micros."
        )

    projected_monthly_limit = round(amount_micros * 30.4)
    if (
        monthly_spend_cap_amount_micros is not None
        and projected_monthly_limit > monthly_spend_cap_amount_micros
    ):
        raise ToolError(
            "Budget creation blocked: Google Ads can charge up to roughly "
            "30.4x the average daily budget in a month, and this budget exceeds "
            "monthly_spend_cap_amount_micros."
        )


def _normalize_date_time(
    value: str | None, name: str, end_of_day: bool
) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            parsed = datetime.strptime(normalized, fmt)
            suffix = "23:59:59" if end_of_day else "00:00:00"
            return f"{parsed:%Y%m%d} {suffix}"
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(normalized, fmt)
            return parsed.strftime("%Y%m%d %H:%M:%S")
        except ValueError:
            pass
    raise ToolError(
        f"{name} must be YYYY-MM-DD, YYYYMMDD, or include HH:MM:SS time."
    )


def _validate_final_urls(final_urls: List[str]) -> List[str]:
    if not final_urls:
        raise ToolError("final_urls must include at least one URL.")
    normalized = []
    for final_url in final_urls:
        value = final_url.strip()
        if not value.startswith(("http://", "https://")):
            raise ToolError(
                "final_urls entries must start with http:// or https://."
            )
        normalized.append(value)
    return normalized


def _validate_responsive_search_ad_assets(
    headlines: List[str],
    descriptions: List[str],
) -> tuple[List[str], List[str]]:
    clean_headlines = [
        headline.strip() for headline in headlines if headline.strip()
    ]
    clean_descriptions = [
        description.strip()
        for description in descriptions
        if description.strip()
    ]
    if len(clean_headlines) < 3:
        raise ToolError("Responsive search ads require at least 3 headlines.")
    if len(clean_headlines) > 15:
        raise ToolError("Responsive search ads support at most 15 headlines.")
    if len(clean_descriptions) < 2:
        raise ToolError(
            "Responsive search ads require at least 2 descriptions."
        )
    if len(clean_descriptions) > 4:
        raise ToolError("Responsive search ads support at most 4 descriptions.")
    return clean_headlines, clean_descriptions


def _append_ad_text_assets(
    client: Any, repeated_field: Any, texts: List[str]
) -> None:
    for text in texts:
        ad_text_asset = client.get_type("AdTextAsset")
        ad_text_asset.text = text
        repeated_field.append(ad_text_asset)


def _set_bidding_strategy(
    client: Any, campaign: Any, strategy: BiddingStrategyType
) -> None:
    if strategy == "MANUAL_CPC":
        campaign.manual_cpc = client.get_type("ManualCpc")
    elif strategy == "TARGET_SPEND":
        campaign.target_spend.target_spend_micros = 0
    else:
        raise ToolError(f"Unsupported bidding_strategy_type: {strategy}.")


def _build_campaign_budget_create_operation(
    client: Any,
    customer_id: str,
    name: str,
    amount_micros: int,
    explicitly_shared: bool,
    temporary_resource_name: str | None = None,
) -> Any:
    operation = client.get_type("CampaignBudgetOperation")
    budget = operation.create
    if temporary_resource_name:
        budget.resource_name = temporary_resource_name
    budget.name = name
    budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
    budget.amount_micros = amount_micros
    budget.explicitly_shared = explicitly_shared
    return operation


def _build_webpage_conversion_action_create_operation(
    client: Any,
    *,
    name: str,
    category: ConversionActionCategory,
    status: ConversionActionStatus,
    counting_type: ConversionActionCountingType,
    default_value: float,
    default_currency_code: str,
    always_use_default_value: bool,
    primary_for_goal: bool,
    click_through_lookback_window_days: int,
    view_through_lookback_window_days: int,
) -> Any:
    operation = client.get_type("ConversionActionOperation")
    conversion_action = operation.create
    conversion_action.name = name
    conversion_action.type_ = client.enums.ConversionActionTypeEnum.WEBPAGE
    conversion_action.category = getattr(
        client.enums.ConversionActionCategoryEnum,
        category,
    )
    conversion_action.status = getattr(
        client.enums.ConversionActionStatusEnum,
        status,
    )
    conversion_action.counting_type = getattr(
        client.enums.ConversionActionCountingTypeEnum,
        counting_type,
    )
    conversion_action.primary_for_goal = primary_for_goal
    conversion_action.click_through_lookback_window_days = (
        click_through_lookback_window_days
    )
    conversion_action.view_through_lookback_window_days = (
        view_through_lookback_window_days
    )
    conversion_action.value_settings.default_value = default_value
    conversion_action.value_settings.default_currency_code = (
        default_currency_code
    )
    conversion_action.value_settings.always_use_default_value = (
        always_use_default_value
    )
    return operation


def _build_campaign_create_operation(
    client: Any,
    customer_id: str,
    name: str,
    budget_resource_name: str,
    status: EntityStatus,
    contains_eu_political_advertising: EuPoliticalAdvertisingStatus,
    bidding_strategy_type: BiddingStrategyType,
    target_google_search: bool,
    target_search_network: bool,
    target_partner_search_network: bool,
    target_content_network: bool,
    start_date_time: str | None,
    end_date_time: str | None,
    temporary_resource_name: str | None = None,
) -> Any:
    operation = client.get_type("CampaignOperation")
    campaign = operation.create
    if temporary_resource_name:
        campaign.resource_name = temporary_resource_name
    campaign.name = name
    campaign.advertising_channel_type = (
        client.enums.AdvertisingChannelTypeEnum.SEARCH
    )
    campaign.status = getattr(client.enums.CampaignStatusEnum, status)
    campaign.campaign_budget = budget_resource_name
    _set_bidding_strategy(client, campaign, bidding_strategy_type)
    campaign.network_settings.target_google_search = target_google_search
    campaign.network_settings.target_search_network = target_search_network
    campaign.network_settings.target_partner_search_network = (
        target_partner_search_network
    )
    campaign.network_settings.target_content_network = target_content_network
    campaign.contains_eu_political_advertising = getattr(
        client.enums.EuPoliticalAdvertisingStatusEnum,
        contains_eu_political_advertising,
    )
    if start_date_time:
        campaign.start_date_time = start_date_time
    if end_date_time:
        campaign.end_date_time = end_date_time
    return operation


def _build_ad_group_create_operation(
    client: Any,
    name: str,
    campaign_resource_name: str,
    status: EntityStatus,
    cpc_bid_micros: int | None,
    temporary_resource_name: str | None = None,
) -> Any:
    operation = client.get_type("AdGroupOperation")
    ad_group = operation.create
    if temporary_resource_name:
        ad_group.resource_name = temporary_resource_name
    ad_group.name = name
    ad_group.campaign = campaign_resource_name
    ad_group.status = getattr(client.enums.AdGroupStatusEnum, status)
    ad_group.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
    if cpc_bid_micros is not None:
        _validate_positive_micros(cpc_bid_micros, "cpc_bid_micros")
        ad_group.cpc_bid_micros = cpc_bid_micros
    return operation


def _build_keyword_create_operation(
    client: Any,
    ad_group_resource_name: str,
    text: str,
    match_type: KeywordMatchType,
    status: EntityStatus,
    negative: bool,
) -> Any:
    operation = client.get_type("AdGroupCriterionOperation")
    criterion = operation.create
    criterion.ad_group = ad_group_resource_name
    criterion.status = getattr(client.enums.AdGroupCriterionStatusEnum, status)
    criterion.keyword.text = text.strip()
    criterion.keyword.match_type = getattr(
        client.enums.KeywordMatchTypeEnum, match_type
    )
    criterion.negative = negative
    return operation


def _build_responsive_search_ad_create_operation(
    client: Any,
    ad_group_resource_name: str,
    final_urls: List[str],
    headlines: List[str],
    descriptions: List[str],
    status: EntityStatus,
    path1: str | None,
    path2: str | None,
) -> Any:
    operation = client.get_type("AdGroupAdOperation")
    ad_group_ad = operation.create
    ad_group_ad.ad_group = ad_group_resource_name
    ad_group_ad.status = getattr(client.enums.AdGroupAdStatusEnum, status)
    ad_group_ad.ad.final_urls.extend(final_urls)
    _append_ad_text_assets(
        client,
        ad_group_ad.ad.responsive_search_ad.headlines,
        headlines,
    )
    _append_ad_text_assets(
        client,
        ad_group_ad.ad.responsive_search_ad.descriptions,
        descriptions,
    )
    if path1 is not None:
        ad_group_ad.ad.responsive_search_ad.path1 = path1.strip()
    if path2 is not None:
        ad_group_ad.ad.responsive_search_ad.path2 = path2.strip()
    return operation


def _build_campaign_location_operation(
    client: Any,
    campaign_resource_name: str,
    geo_target_constant_resource_name: str,
) -> Any:
    operation = client.get_type("CampaignCriterionOperation")
    criterion = operation.create
    criterion.campaign = campaign_resource_name
    criterion.location.geo_target_constant = geo_target_constant_resource_name
    return operation


def _build_campaign_language_operation(
    client: Any,
    campaign_resource_name: str,
    language_constant_resource_name: str,
) -> Any:
    operation = client.get_type("CampaignCriterionOperation")
    criterion = operation.create
    criterion.campaign = campaign_resource_name
    criterion.language.language_constant = language_constant_resource_name
    return operation


def _google_ads_mutate(
    client: Any,
    customer_id: str,
    operations: List[Any],
    dry_run: bool,
) -> Any:
    request = client.get_type("MutateGoogleAdsRequest")
    request.customer_id = customer_id
    request.mutate_operations.extend(operations)
    request.partial_failure = False
    request.validate_only = dry_run
    request.response_content_type = (
        client.enums.ResponseContentTypeEnum.RESOURCE_NAME_ONLY
    )

    google_ads_service = utils.get_googleads_service("GoogleAdsService")
    return google_ads_service.mutate(request=request)


def _wrap_mutate_operation(client: Any, field_name: str, operation: Any) -> Any:
    mutate_operation = client.get_type("MutateOperation")
    setattr(mutate_operation, field_name, operation)
    return mutate_operation


def _fetch_campaign_budget_by_resource_name(
    customer_id: str,
    budget_resource_name: str,
) -> Dict[str, Any]:
    budget_resource_name = _validate_customer_resource_name(
        budget_resource_name,
        "budget_resource_name",
        customer_id,
        "campaignBudgets",
    )
    ga_service = utils.get_googleads_service("GoogleAdsService")
    query = (
        "SELECT "
        "campaign_budget.resource_name, "
        "campaign_budget.name, "
        "campaign_budget.amount_micros, "
        "campaign_budget.explicitly_shared, "
        "campaign_budget.period "
        "FROM campaign_budget "
        f"WHERE campaign_budget.resource_name = '{budget_resource_name}' "
        "LIMIT 1"
    )

    try:
        stream = ga_service.search_stream(customer_id=customer_id, query=query)
        for batch in stream:
            for row in batch.results:
                return {
                    "budget_resource_name": row.campaign_budget.resource_name,
                    "budget_name": row.campaign_budget.name,
                    "amount_micros": int(row.campaign_budget.amount_micros),
                    "explicitly_shared": bool(
                        row.campaign_budget.explicitly_shared
                    ),
                    "period": utils.format_output_value(
                        row.campaign_budget.period
                    ),
                }
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    raise ToolError(
        f"No campaign budget found for customer_id={customer_id}, "
        f"budget_resource_name={budget_resource_name}."
    )


def _fetch_campaign_budget(
    customer_id: str, campaign_id: str
) -> Dict[str, Any]:
    ga_service = utils.get_googleads_service("GoogleAdsService")
    query = (
        "SELECT "
        "campaign.id, "
        "campaign.name, "
        "campaign.status, "
        "campaign_budget.resource_name, "
        "campaign_budget.name, "
        "campaign_budget.amount_micros, "
        "campaign_budget.explicitly_shared, "
        "campaign_budget.period "
        "FROM campaign "
        f"WHERE campaign.id = {campaign_id} "
        "LIMIT 1"
    )

    try:
        stream = ga_service.search_stream(customer_id=customer_id, query=query)
        for batch in stream:
            for row in batch.results:
                return {
                    "campaign_id": str(row.campaign.id),
                    "campaign_name": row.campaign.name,
                    "campaign_status": utils.format_output_value(
                        row.campaign.status
                    ),
                    "budget_resource_name": row.campaign_budget.resource_name,
                    "budget_name": row.campaign_budget.name,
                    "amount_micros": int(row.campaign_budget.amount_micros),
                    "explicitly_shared": bool(
                        row.campaign_budget.explicitly_shared
                    ),
                    "period": utils.format_output_value(
                        row.campaign_budget.period
                    ),
                }
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    raise ToolError(
        f"No campaign budget found for customer_id={customer_id}, "
        f"campaign_id={campaign_id}."
    )


def _validate_budget_guardrails(
    current_amount_micros: int,
    target_amount_micros: int,
    explicitly_shared: bool,
    allow_shared_budget: bool,
    max_daily_budget_amount_micros: int,
    monthly_spend_cap_amount_micros: int | None,
    max_increase_percent: float,
    confirm_budget_increase: bool,
) -> List[str]:
    warnings: List[str] = []

    if target_amount_micros <= 0:
        raise ToolError("amount_micros must be greater than 0.")

    if explicitly_shared and not allow_shared_budget:
        raise ToolError(
            "This campaign uses an explicitly shared budget. Updating it can "
            "affect multiple campaigns. Set allow_shared_budget=true only after "
            "checking all campaigns that share this budget."
        )

    if target_amount_micros > max_daily_budget_amount_micros:
        raise ToolError(
            "Budget update blocked: amount_micros exceeds "
            "max_daily_budget_amount_micros."
        )

    projected_monthly_limit = round(target_amount_micros * 30.4)
    if (
        monthly_spend_cap_amount_micros is not None
        and projected_monthly_limit > monthly_spend_cap_amount_micros
    ):
        raise ToolError(
            "Budget update blocked: Google Ads can charge up to roughly "
            "30.4x the average daily budget in a month, and this target exceeds "
            "monthly_spend_cap_amount_micros."
        )

    if target_amount_micros > current_amount_micros:
        increase_percent = (
            (target_amount_micros - current_amount_micros)
            / current_amount_micros
            * 100
            if current_amount_micros
            else 100
        )
        if increase_percent > max_increase_percent:
            raise ToolError(
                "Budget update blocked: increase exceeds "
                "max_increase_percent."
            )
        if not confirm_budget_increase:
            raise ToolError(
                "Budget update blocked: increases require "
                "confirm_budget_increase=true."
            )
        warnings.append(f"Budget increase confirmed: +{increase_percent:.2f}%.")

    if explicitly_shared and allow_shared_budget:
        warnings.append(
            "Shared budget update allowed by caller; verify all linked campaigns."
        )

    return warnings


@budget_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def audit_budget_pitfalls(
    customer_id: str,
    lookback_days: int = 7,
    monthly_spend_cap_amount_micros: int | None = None,
    min_wasted_cost_micros: int = 10000000,
    only_enabled: bool = True,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    """Audit campaign budgets for common budget pitfalls.

    Flags shared budgets, daily costs that exceed 2x average daily budget,
    projected monthly spend above a caller-provided cap, and spend with no
    conversions. This tool only reads Google Ads data.

    Args:
        customer_id: The Google Ads customer ID.
        lookback_days: Number of recent days to inspect, from 1 to 30.
        monthly_spend_cap_amount_micros: Optional account or portfolio spend cap.
        min_wasted_cost_micros: Minimum lookback cost before no-conversion spend is flagged.
        only_enabled: Whether to inspect only enabled campaigns.
        limit: Maximum GAQL rows to inspect.
    """
    customer_id = _normalize_customer_id(customer_id)
    if lookback_days < 1 or lookback_days > 30:
        raise ToolError("lookback_days must be between 1 and 30.")
    if limit < 1 or limit > 10000:
        raise ToolError("limit must be between 1 and 10000.")

    conditions = [f"segments.date DURING LAST_{lookback_days}_DAYS"]
    if only_enabled:
        conditions.append("campaign.status = 'ENABLED'")

    query = (
        "SELECT "
        "segments.date, "
        "campaign.id, "
        "campaign.name, "
        "campaign.status, "
        "campaign_budget.resource_name, "
        "campaign_budget.name, "
        "campaign_budget.amount_micros, "
        "campaign_budget.explicitly_shared, "
        "metrics.cost_micros, "
        "metrics.conversions, "
        "metrics.conversions_value "
        "FROM campaign "
        f"WHERE {' AND '.join(conditions)} "
        "ORDER BY metrics.cost_micros DESC "
        f"LIMIT {limit}"
    )

    ga_service = utils.get_googleads_service("GoogleAdsService")
    campaigns: Dict[str, Dict[str, Any]] = {}

    try:
        stream = ga_service.search_stream(customer_id=customer_id, query=query)
        for batch in stream:
            for row in batch.results:
                campaign_key = str(row.campaign.id)
                budget_amount = int(row.campaign_budget.amount_micros)
                cost_micros = int(row.metrics.cost_micros)
                entry = campaigns.setdefault(
                    campaign_key,
                    {
                        "campaign_id": campaign_key,
                        "campaign_name": row.campaign.name,
                        "campaign_status": utils.format_output_value(
                            row.campaign.status
                        ),
                        "budget_resource_name": (
                            row.campaign_budget.resource_name
                        ),
                        "budget_name": row.campaign_budget.name,
                        "daily_budget_amount_micros": budget_amount,
                        "explicitly_shared_budget": bool(
                            row.campaign_budget.explicitly_shared
                        ),
                        "lookback_days": lookback_days,
                        "days_seen": set(),
                        "total_cost_micros": 0,
                        "max_daily_cost_micros": 0,
                        "conversions": 0.0,
                        "conversion_value": 0.0,
                    },
                )
                entry["days_seen"].add(row.segments.date)
                entry["total_cost_micros"] += cost_micros
                entry["max_daily_cost_micros"] = max(
                    entry["max_daily_cost_micros"], cost_micros
                )
                entry["conversions"] += float(row.metrics.conversions)
                entry["conversion_value"] += float(
                    row.metrics.conversions_value
                )
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    audits: List[Dict[str, Any]] = []
    for entry in campaigns.values():
        budget_amount = entry["daily_budget_amount_micros"]
        days_seen = max(len(entry["days_seen"]), 1)
        average_daily_cost = round(entry["total_cost_micros"] / days_seen)
        projected_monthly_cost = round(average_daily_cost * 30.4)

        flags = []
        if entry["explicitly_shared_budget"]:
            flags.append("shared_budget_requires_extra_care")
        if budget_amount and entry["max_daily_cost_micros"] > budget_amount * 2:
            flags.append("single_day_cost_over_2x_average_daily_budget")
        if budget_amount and average_daily_cost > budget_amount * 1.05:
            flags.append("average_daily_cost_above_average_daily_budget")
        if (
            monthly_spend_cap_amount_micros is not None
            and projected_monthly_cost > monthly_spend_cap_amount_micros
        ):
            flags.append("projected_monthly_cost_above_cap")
        if (
            entry["total_cost_micros"] >= min_wasted_cost_micros
            and entry["conversions"] == 0
        ):
            flags.append("cost_without_conversions")

        entry["days_seen"] = sorted(entry["days_seen"])
        entry["average_daily_cost_micros"] = average_daily_cost
        entry["projected_monthly_cost_micros"] = projected_monthly_cost
        entry["flags"] = flags
        audits.append(entry)

    return sorted(
        audits,
        key=lambda item: (
            len(item["flags"]),
            item["total_cost_micros"],
        ),
        reverse=True,
    )


@writes_mcp.tool()
def update_campaign_status(
    customer_id: str,
    campaign_id: str,
    status: CampaignStatus,
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Update a campaign's serving status.

    Defaults to validate-only mode. A real write requires dry_run=false and
    confirm_write=true.
    """
    customer_id = _normalize_customer_id(customer_id)
    campaign_id = _normalize_id(campaign_id, "campaign_id")

    if not dry_run and not confirm_write:
        raise ToolError(
            "Set confirm_write=true with dry_run=false to update a campaign."
        )

    client = utils.get_googleads_client()
    campaign_service = utils.get_googleads_service("CampaignService")
    operation = client.get_type("CampaignOperation")
    campaign = operation.update
    campaign.resource_name = campaign_service.campaign_path(
        customer_id, campaign_id
    )
    campaign.status = getattr(client.enums.CampaignStatusEnum, status)
    operation.update_mask.paths.append("status")

    request = client.get_type("MutateCampaignsRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = dry_run

    try:
        response = campaign_service.mutate_campaigns(request=request)
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "update_campaign_status",
        "customer_id": customer_id,
        "campaign_id": campaign_id,
        "status": status,
        "resource_names": _result_resource_names(response),
    }


@writes_mcp.tool()
def update_campaign_name(
    customer_id: str,
    campaign_id: str,
    name: str,
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Update a campaign name.

    Defaults to validate-only mode. A real write requires dry_run=false and
    confirm_write=true.
    """
    customer_id = _normalize_customer_id(customer_id)
    campaign_id = _normalize_id(campaign_id, "campaign_id")
    name = _validate_name(name, "name")

    if not dry_run and not confirm_write:
        raise ToolError(
            "Set confirm_write=true with dry_run=false to update a campaign name."
        )

    client = utils.get_googleads_client()
    campaign_service = utils.get_googleads_service("CampaignService")
    operation = client.get_type("CampaignOperation")
    campaign = operation.update
    campaign.resource_name = campaign_service.campaign_path(
        customer_id, campaign_id
    )
    campaign.name = name
    operation.update_mask.paths.append("name")

    request = client.get_type("MutateCampaignsRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = dry_run

    try:
        response = campaign_service.mutate_campaigns(request=request)
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "update_campaign_name",
        "customer_id": customer_id,
        "campaign_id": campaign_id,
        "name": name,
        "resource_names": _result_resource_names(response),
    }


@writes_mcp.tool()
def replace_campaign_geo_targeting_with_proximity(
    customer_id: str,
    campaign_id: str,
    remove_campaign_criterion_resource_names: List[str],
    latitude: float,
    longitude: float,
    radius: float,
    radius_units: ProximityRadiusUnit = "KILOMETERS",
    positive_geo_target_type: PositiveGeoTargetType = "PRESENCE",
    negative_geo_target_type: NegativeGeoTargetType = "PRESENCE",
    city_name: str = "Munich",
    country_code: str = "DE",
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Replace campaign location targeting with a geo-radius proximity target.

    Also updates the campaign's positive/negative geo target type settings.
    Defaults to validate-only mode. A real write requires dry_run=false and
    confirm_write=true.
    """
    customer_id = _normalize_customer_id(customer_id)
    campaign_id = _normalize_id(campaign_id, "campaign_id")
    if not (-90 <= latitude <= 90):
        raise ToolError("latitude must be between -90 and 90.")
    if not (-180 <= longitude <= 180):
        raise ToolError("longitude must be between -180 and 180.")
    if radius <= 0 or radius > 500:
        raise ToolError("radius must be greater than 0 and at most 500.")
    city_name = city_name.strip()
    country_code = country_code.strip().upper()
    if len(country_code) != 2 or not country_code.isalpha():
        raise ToolError("country_code must be a 2-letter country code.")
    remove_campaign_criterion_resource_names = [
        _validate_customer_composite_resource_name(
            resource_name,
            "campaign_criterion_resource_name",
            customer_id,
            "campaignCriteria",
        )
        for resource_name in remove_campaign_criterion_resource_names
    ]
    _require_confirmed_write(
        dry_run,
        confirm_write,
        "replace campaign geo targeting with proximity targeting",
    )

    client = utils.get_googleads_client()
    campaign_service = utils.get_googleads_service("CampaignService")
    campaign_resource_name = campaign_service.campaign_path(
        customer_id, campaign_id
    )

    campaign_operation = client.get_type("CampaignOperation")
    campaign = campaign_operation.update
    campaign.resource_name = campaign_resource_name
    campaign.geo_target_type_setting.positive_geo_target_type = getattr(
        client.enums.PositiveGeoTargetTypeEnum, positive_geo_target_type
    )
    campaign.geo_target_type_setting.negative_geo_target_type = getattr(
        client.enums.NegativeGeoTargetTypeEnum, negative_geo_target_type
    )
    campaign_operation.update_mask.paths.extend(
        [
            "geo_target_type_setting.positive_geo_target_type",
            "geo_target_type_setting.negative_geo_target_type",
        ]
    )

    operations = [
        _wrap_mutate_operation(
            client,
            "campaign_operation",
            campaign_operation,
        )
    ]

    for resource_name in remove_campaign_criterion_resource_names:
        remove_operation = client.get_type("CampaignCriterionOperation")
        remove_operation.remove = resource_name
        operations.append(
            _wrap_mutate_operation(
                client,
                "campaign_criterion_operation",
                remove_operation,
            )
        )

    proximity_operation = client.get_type("CampaignCriterionOperation")
    criterion = proximity_operation.create
    criterion.campaign = campaign_resource_name
    criterion.proximity.geo_point.latitude_in_micro_degrees = int(
        round(latitude * 1_000_000)
    )
    criterion.proximity.geo_point.longitude_in_micro_degrees = int(
        round(longitude * 1_000_000)
    )
    criterion.proximity.radius = radius
    criterion.proximity.radius_units = getattr(
        client.enums.ProximityRadiusUnitsEnum, radius_units
    )
    if city_name:
        criterion.proximity.address.city_name = city_name
    criterion.proximity.address.country_code = country_code
    operations.append(
        _wrap_mutate_operation(
            client,
            "campaign_criterion_operation",
            proximity_operation,
        )
    )

    try:
        response = _google_ads_mutate(client, customer_id, operations, dry_run)
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "replace_campaign_geo_targeting_with_proximity",
        "customer_id": customer_id,
        "campaign_id": campaign_id,
        "removed_campaign_criteria_count": len(
            remove_campaign_criterion_resource_names
        ),
        "latitude": latitude,
        "longitude": longitude,
        "radius": radius,
        "radius_units": radius_units,
        "positive_geo_target_type": positive_geo_target_type,
        "negative_geo_target_type": negative_geo_target_type,
        "resource_names": _result_resource_names(response),
    }


@writes_mcp.tool()
def update_ad_group_status(
    customer_id: str,
    ad_group_resource_name: str,
    status: EntityStatus,
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Update a Search ad group's status."""
    customer_id = _normalize_customer_id(customer_id)
    ad_group_resource_name = _validate_customer_resource_name(
        ad_group_resource_name,
        "ad_group_resource_name",
        customer_id,
        "adGroups",
    )
    _require_confirmed_write(dry_run, confirm_write, "update an ad group")

    client = utils.get_googleads_client()
    ad_group_service = utils.get_googleads_service("AdGroupService")
    operation = client.get_type("AdGroupOperation")
    ad_group = operation.update
    ad_group.resource_name = ad_group_resource_name
    ad_group.status = getattr(client.enums.AdGroupStatusEnum, status)
    operation.update_mask.paths.append("status")

    request = client.get_type("MutateAdGroupsRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = dry_run

    try:
        response = ad_group_service.mutate_ad_groups(request=request)
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "update_ad_group_status",
        "customer_id": customer_id,
        "ad_group_resource_name": ad_group_resource_name,
        "status": status,
        "resource_names": _result_resource_names(response),
    }


@writes_mcp.tool()
def update_ad_group_ad_status(
    customer_id: str,
    ad_group_ad_resource_name: str,
    status: EntityStatus,
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Update an ad group's ad status."""
    customer_id = _normalize_customer_id(customer_id)
    ad_group_ad_resource_name = _validate_customer_composite_resource_name(
        ad_group_ad_resource_name,
        "ad_group_ad_resource_name",
        customer_id,
        "adGroupAds",
    )
    _require_confirmed_write(dry_run, confirm_write, "update an ad group ad")

    client = utils.get_googleads_client()
    ad_group_ad_service = utils.get_googleads_service("AdGroupAdService")
    operation = client.get_type("AdGroupAdOperation")
    ad_group_ad = operation.update
    ad_group_ad.resource_name = ad_group_ad_resource_name
    ad_group_ad.status = getattr(client.enums.AdGroupAdStatusEnum, status)
    operation.update_mask.paths.append("status")

    request = client.get_type("MutateAdGroupAdsRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = dry_run

    try:
        response = ad_group_ad_service.mutate_ad_group_ads(request=request)
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "update_ad_group_ad_status",
        "customer_id": customer_id,
        "ad_group_ad_resource_name": ad_group_ad_resource_name,
        "status": status,
        "resource_names": _result_resource_names(response),
    }


@writes_mcp.tool()
def update_ad_group_criteria_status(
    customer_id: str,
    ad_group_criterion_resource_names: List[str],
    status: EntityStatus,
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Update one or more ad group criterion statuses."""
    customer_id = _normalize_customer_id(customer_id)
    if not ad_group_criterion_resource_names:
        raise ToolError(
            "ad_group_criterion_resource_names must include at least one item."
        )
    if len(ad_group_criterion_resource_names) > 200:
        raise ToolError(
            "ad_group_criterion_resource_names supports at most 200 items."
        )
    resource_names = [
        _validate_customer_composite_resource_name(
            resource_name,
            "ad_group_criterion_resource_name",
            customer_id,
            "adGroupCriteria",
        )
        for resource_name in ad_group_criterion_resource_names
    ]
    _require_confirmed_write(
        dry_run,
        confirm_write,
        "update ad group criteria",
    )

    client = utils.get_googleads_client()
    criterion_service = utils.get_googleads_service(
        "AdGroupCriterionService"
    )
    operations = []
    for resource_name in resource_names:
        operation = client.get_type("AdGroupCriterionOperation")
        criterion = operation.update
        criterion.resource_name = resource_name
        criterion.status = getattr(
            client.enums.AdGroupCriterionStatusEnum,
            status,
        )
        operation.update_mask.paths.append("status")
        operations.append(operation)

    request = client.get_type("MutateAdGroupCriteriaRequest")
    request.customer_id = customer_id
    request.operations.extend(operations)
    request.validate_only = dry_run

    try:
        response = criterion_service.mutate_ad_group_criteria(
            request=request
        )
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "update_ad_group_criteria_status",
        "customer_id": customer_id,
        "criterion_count": len(resource_names),
        "status": status,
        "resource_names": _result_resource_names(response),
    }


@writes_mcp.tool()
def update_campaign_budget(
    customer_id: str,
    campaign_id: str,
    amount_micros: int,
    max_daily_budget_amount_micros: int,
    monthly_spend_cap_amount_micros: int | None = None,
    max_increase_percent: float = 10.0,
    allow_shared_budget: bool = False,
    confirm_budget_increase: bool = False,
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Update a campaign's average daily budget with budget guardrails.

    Defaults to validate-only mode. A real write requires dry_run=false and
    confirm_write=true. Increases require confirm_budget_increase=true, cannot
    exceed max_increase_percent, cannot exceed max_daily_budget_amount_micros,
    and can optionally be checked against a 30.4x monthly cap.
    """
    customer_id = _normalize_customer_id(customer_id)
    campaign_id = _normalize_id(campaign_id, "campaign_id")

    if max_daily_budget_amount_micros <= 0:
        raise ToolError(
            "max_daily_budget_amount_micros must be greater than 0."
        )
    if max_increase_percent < 0 or max_increase_percent > 100:
        raise ToolError("max_increase_percent must be between 0 and 100.")
    if not dry_run and not confirm_write:
        raise ToolError(
            "Set confirm_write=true with dry_run=false to update a budget."
        )

    current = _fetch_campaign_budget(customer_id, campaign_id)
    warnings = _validate_budget_guardrails(
        current_amount_micros=current["amount_micros"],
        target_amount_micros=amount_micros,
        explicitly_shared=current["explicitly_shared"],
        allow_shared_budget=allow_shared_budget,
        max_daily_budget_amount_micros=max_daily_budget_amount_micros,
        monthly_spend_cap_amount_micros=monthly_spend_cap_amount_micros,
        max_increase_percent=max_increase_percent,
        confirm_budget_increase=confirm_budget_increase,
    )

    client = utils.get_googleads_client()
    budget_service = utils.get_googleads_service("CampaignBudgetService")
    operation = client.get_type("CampaignBudgetOperation")
    budget = operation.update
    budget.resource_name = current["budget_resource_name"]
    budget.amount_micros = amount_micros
    operation.update_mask.paths.append("amount_micros")

    request = client.get_type("MutateCampaignBudgetsRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = dry_run

    try:
        response = budget_service.mutate_campaign_budgets(request=request)
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "update_campaign_budget",
        "customer_id": customer_id,
        "campaign_id": campaign_id,
        "campaign_name": current["campaign_name"],
        "budget_resource_name": current["budget_resource_name"],
        "previous_amount_micros": current["amount_micros"],
        "new_amount_micros": amount_micros,
        "explicitly_shared_budget": current["explicitly_shared"],
        "warnings": warnings,
        "resource_names": _result_resource_names(response),
    }


@writes_mcp.tool()
def update_customer_conversion_goal_biddable(
    customer_id: str,
    category: ConversionActionCategory,
    origin: ConversionGoalOrigin,
    biddable: bool,
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Update whether an account-level conversion goal is biddable.

    Defaults to validate-only mode. A real write requires dry_run=false and
    confirm_write=true. Use carefully: account-level goals can affect bidding
    behavior for campaigns that use the account default conversion goals.
    """
    customer_id = _normalize_customer_id(customer_id)
    _require_confirmed_write(
        dry_run,
        confirm_write,
        "update a customer conversion goal",
    )

    client = utils.get_googleads_client()
    goal_service = utils.get_googleads_service(
        "CustomerConversionGoalService"
    )
    operation = client.get_type("CustomerConversionGoalOperation")
    goal = operation.update
    goal.resource_name = (
        f"customers/{customer_id}/customerConversionGoals/"
        f"{category}~{origin}"
    )
    goal.biddable = biddable
    operation.update_mask.paths.append("biddable")

    request = client.get_type("MutateCustomerConversionGoalsRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = dry_run

    try:
        response = goal_service.mutate_customer_conversion_goals(
            request=request
        )
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "update_customer_conversion_goal_biddable",
        "customer_id": customer_id,
        "resource_name": goal.resource_name,
        "category": category,
        "origin": origin,
        "biddable": biddable,
        "resource_names": _result_resource_names(response),
    }


@writes_mcp.tool()
def create_campaign_budget(
    customer_id: str,
    name: str,
    amount_micros: int,
    max_daily_budget_amount_micros: int,
    monthly_spend_cap_amount_micros: int | None = None,
    explicitly_shared: bool = False,
    allow_shared_budget: bool = False,
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Create an average daily campaign budget with spend guardrails.

    Defaults to validate-only mode. A real create requires dry_run=false and
    confirm_write=true. Shared budgets are blocked unless
    allow_shared_budget=true.
    """
    customer_id = _normalize_customer_id(customer_id)
    name = _validate_name(name, "name")
    _require_confirmed_write(dry_run, confirm_write, "create a campaign budget")
    _validate_budget_cap(
        amount_micros,
        max_daily_budget_amount_micros,
        monthly_spend_cap_amount_micros,
    )
    if explicitly_shared and not allow_shared_budget:
        raise ToolError(
            "Shared budget creation blocked. Set allow_shared_budget=true only "
            "after confirming the budget may be attached to multiple campaigns."
        )

    client = utils.get_googleads_client()
    budget_service = utils.get_googleads_service("CampaignBudgetService")
    operation = _build_campaign_budget_create_operation(
        client=client,
        customer_id=customer_id,
        name=name,
        amount_micros=amount_micros,
        explicitly_shared=explicitly_shared,
    )

    request = client.get_type("MutateCampaignBudgetsRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = dry_run

    try:
        response = budget_service.mutate_campaign_budgets(request=request)
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "create_campaign_budget",
        "customer_id": customer_id,
        "name": name,
        "amount_micros": amount_micros,
        "explicitly_shared": explicitly_shared,
        "resource_names": _result_resource_names(response),
    }


@writes_mcp.tool()
def create_search_campaign(
    customer_id: str,
    name: str,
    budget_resource_name: str,
    max_daily_budget_amount_micros: int,
    monthly_spend_cap_amount_micros: int | None = None,
    allow_shared_budget: bool = False,
    status: EntityStatus = "PAUSED",
    contains_eu_political_advertising: EuPoliticalAdvertisingStatus = (
        "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"
    ),
    bidding_strategy_type: BiddingStrategyType = "MANUAL_CPC",
    target_google_search: bool = True,
    target_search_network: bool = True,
    target_partner_search_network: bool = False,
    target_content_network: bool = False,
    start_date_time: str | None = None,
    end_date_time: str | None = None,
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Create a Search campaign attached to an existing budget.

    Defaults to a paused campaign in validate-only mode. The referenced budget
    is read first so daily cap and shared-budget guardrails still apply.
    """
    customer_id = _normalize_customer_id(customer_id)
    name = _validate_name(name, "name")
    _require_confirmed_write(dry_run, confirm_write, "create a search campaign")

    current_budget = _fetch_campaign_budget_by_resource_name(
        customer_id, budget_resource_name
    )
    _validate_budget_guardrails(
        current_amount_micros=current_budget["amount_micros"],
        target_amount_micros=current_budget["amount_micros"],
        explicitly_shared=current_budget["explicitly_shared"],
        allow_shared_budget=allow_shared_budget,
        max_daily_budget_amount_micros=max_daily_budget_amount_micros,
        monthly_spend_cap_amount_micros=monthly_spend_cap_amount_micros,
        max_increase_percent=0,
        confirm_budget_increase=True,
    )
    start_date_time = _normalize_date_time(
        start_date_time,
        "start_date_time",
        end_of_day=False,
    )
    end_date_time = _normalize_date_time(
        end_date_time,
        "end_date_time",
        end_of_day=True,
    )

    client = utils.get_googleads_client()
    campaign_service = utils.get_googleads_service("CampaignService")
    operation = _build_campaign_create_operation(
        client=client,
        customer_id=customer_id,
        name=name,
        budget_resource_name=current_budget["budget_resource_name"],
        status=status,
        contains_eu_political_advertising=contains_eu_political_advertising,
        bidding_strategy_type=bidding_strategy_type,
        target_google_search=target_google_search,
        target_search_network=target_search_network,
        target_partner_search_network=target_partner_search_network,
        target_content_network=target_content_network,
        start_date_time=start_date_time,
        end_date_time=end_date_time,
    )

    request = client.get_type("MutateCampaignsRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = dry_run

    try:
        response = campaign_service.mutate_campaigns(request=request)
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "create_search_campaign",
        "customer_id": customer_id,
        "name": name,
        "status": status,
        "budget_resource_name": current_budget["budget_resource_name"],
        "budget_amount_micros": current_budget["amount_micros"],
        "contains_eu_political_advertising": contains_eu_political_advertising,
        "resource_names": _result_resource_names(response),
    }


@writes_mcp.tool()
def create_ad_group(
    customer_id: str,
    campaign_resource_name: str,
    name: str,
    status: EntityStatus = "PAUSED",
    cpc_bid_micros: int | None = None,
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Create a Search standard ad group under an existing campaign."""
    customer_id = _normalize_customer_id(customer_id)
    campaign_resource_name = _validate_customer_resource_name(
        campaign_resource_name,
        "campaign_resource_name",
        customer_id,
        "campaigns",
    )
    name = _validate_name(name, "name")
    _require_confirmed_write(dry_run, confirm_write, "create an ad group")

    client = utils.get_googleads_client()
    ad_group_service = utils.get_googleads_service("AdGroupService")
    operation = _build_ad_group_create_operation(
        client=client,
        name=name,
        campaign_resource_name=campaign_resource_name,
        status=status,
        cpc_bid_micros=cpc_bid_micros,
    )

    request = client.get_type("MutateAdGroupsRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = dry_run

    try:
        response = ad_group_service.mutate_ad_groups(request=request)
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "create_ad_group",
        "customer_id": customer_id,
        "campaign_resource_name": campaign_resource_name,
        "name": name,
        "status": status,
        "resource_names": _result_resource_names(response),
    }


@writes_mcp.tool()
def create_keywords(
    customer_id: str,
    ad_group_resource_name: str,
    keyword_texts: List[str],
    match_type: KeywordMatchType = "BROAD",
    status: EntityStatus = "PAUSED",
    negative: bool = False,
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Create one or more keyword criteria under an existing ad group."""
    customer_id = _normalize_customer_id(customer_id)
    ad_group_resource_name = _validate_customer_resource_name(
        ad_group_resource_name,
        "ad_group_resource_name",
        customer_id,
        "adGroups",
    )
    keyword_texts = [text.strip() for text in keyword_texts if text.strip()]
    if not keyword_texts:
        raise ToolError("keyword_texts must include at least one keyword.")
    if len(keyword_texts) > 100:
        raise ToolError("keyword_texts supports at most 100 keywords per call.")
    _require_confirmed_write(dry_run, confirm_write, "create keywords")

    client = utils.get_googleads_client()
    criterion_service = utils.get_googleads_service("AdGroupCriterionService")
    operations = [
        _build_keyword_create_operation(
            client=client,
            ad_group_resource_name=ad_group_resource_name,
            text=text,
            match_type=match_type,
            status=status,
            negative=negative,
        )
        for text in keyword_texts
    ]

    request = client.get_type("MutateAdGroupCriteriaRequest")
    request.customer_id = customer_id
    request.operations.extend(operations)
    request.validate_only = dry_run

    try:
        response = criterion_service.mutate_ad_group_criteria(request=request)
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "create_keywords",
        "customer_id": customer_id,
        "ad_group_resource_name": ad_group_resource_name,
        "keyword_count": len(keyword_texts),
        "match_type": match_type,
        "status": status,
        "negative": negative,
        "resource_names": _result_resource_names(response),
    }


@writes_mcp.tool()
def create_responsive_search_ad(
    customer_id: str,
    ad_group_resource_name: str,
    final_urls: List[str],
    headlines: List[str],
    descriptions: List[str],
    status: EntityStatus = "PAUSED",
    path1: str | None = None,
    path2: str | None = None,
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Create a responsive search ad under an existing ad group."""
    customer_id = _normalize_customer_id(customer_id)
    ad_group_resource_name = _validate_customer_resource_name(
        ad_group_resource_name,
        "ad_group_resource_name",
        customer_id,
        "adGroups",
    )
    final_urls = _validate_final_urls(final_urls)
    headlines, descriptions = _validate_responsive_search_ad_assets(
        headlines, descriptions
    )
    _require_confirmed_write(
        dry_run, confirm_write, "create a responsive search ad"
    )

    client = utils.get_googleads_client()
    ad_group_ad_service = utils.get_googleads_service("AdGroupAdService")
    operation = _build_responsive_search_ad_create_operation(
        client=client,
        ad_group_resource_name=ad_group_resource_name,
        final_urls=final_urls,
        headlines=headlines,
        descriptions=descriptions,
        status=status,
        path1=path1,
        path2=path2,
    )

    request = client.get_type("MutateAdGroupAdsRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = dry_run

    try:
        response = ad_group_ad_service.mutate_ad_group_ads(request=request)
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "create_responsive_search_ad",
        "customer_id": customer_id,
        "ad_group_resource_name": ad_group_resource_name,
        "headline_count": len(headlines),
        "description_count": len(descriptions),
        "status": status,
        "resource_names": _result_resource_names(response),
    }


@writes_mcp.tool()
def create_search_campaign_bundle(
    customer_id: str,
    campaign_name: str,
    budget_name: str,
    budget_amount_micros: int,
    max_daily_budget_amount_micros: int,
    ad_group_name: str,
    final_urls: List[str],
    headlines: List[str],
    descriptions: List[str],
    keyword_texts: List[str] | None = None,
    keyword_match_type: KeywordMatchType = "BROAD",
    monthly_spend_cap_amount_micros: int | None = None,
    campaign_status: EntityStatus = "PAUSED",
    ad_group_status: EntityStatus = "PAUSED",
    keyword_status: EntityStatus = "PAUSED",
    ad_status: EntityStatus = "PAUSED",
    contains_eu_political_advertising: EuPoliticalAdvertisingStatus = (
        "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"
    ),
    bidding_strategy_type: BiddingStrategyType = "MANUAL_CPC",
    cpc_bid_micros: int | None = None,
    target_google_search: bool = True,
    target_search_network: bool = True,
    target_partner_search_network: bool = False,
    target_content_network: bool = False,
    start_date_time: str | None = None,
    end_date_time: str | None = None,
    geo_target_constant_resource_names: List[str] | None = None,
    language_constant_resource_names: List[str] | None = None,
    path1: str | None = None,
    path2: str | None = None,
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Create a complete paused Search campaign structure atomically.

    Creates a non-shared budget, Search campaign, Search standard ad group,
    optional location and language criteria, optional keywords, and one
    responsive search ad in a single GoogleAdsService.Mutate request using
    temporary resource names. Defaults to validate-only mode.
    """
    customer_id = _normalize_customer_id(customer_id)
    campaign_name = _validate_name(campaign_name, "campaign_name")
    budget_name = _validate_name(budget_name, "budget_name")
    ad_group_name = _validate_name(ad_group_name, "ad_group_name")
    _require_confirmed_write(
        dry_run,
        confirm_write,
        "create a complete search campaign bundle",
    )
    _validate_budget_cap(
        budget_amount_micros,
        max_daily_budget_amount_micros,
        monthly_spend_cap_amount_micros,
    )
    final_urls = _validate_final_urls(final_urls)
    headlines, descriptions = _validate_responsive_search_ad_assets(
        headlines, descriptions
    )
    keyword_texts = [
        text.strip() for text in (keyword_texts or []) if text.strip()
    ]
    if len(keyword_texts) > 100:
        raise ToolError(
            "keyword_texts supports at most 100 keywords per bundle."
        )
    start_date_time = _normalize_date_time(
        start_date_time,
        "start_date_time",
        end_of_day=False,
    )
    end_date_time = _normalize_date_time(
        end_date_time,
        "end_date_time",
        end_of_day=True,
    )
    geo_target_constant_resource_names = [
        _validate_constant_resource_name(
            resource_name,
            "geo_target_constant_resource_name",
            "geoTargetConstants",
        )
        for resource_name in (geo_target_constant_resource_names or [])
    ]
    language_constant_resource_names = [
        _validate_constant_resource_name(
            resource_name,
            "language_constant_resource_name",
            "languageConstants",
        )
        for resource_name in (language_constant_resource_names or [])
    ]

    client = utils.get_googleads_client()
    budget_resource_name = _TEMP_BUDGET_RESOURCE_NAME.format(
        customer_id=customer_id
    )
    campaign_resource_name = _TEMP_CAMPAIGN_RESOURCE_NAME.format(
        customer_id=customer_id
    )
    ad_group_resource_name = _TEMP_AD_GROUP_RESOURCE_NAME.format(
        customer_id=customer_id
    )

    budget_operation = _build_campaign_budget_create_operation(
        client=client,
        customer_id=customer_id,
        name=budget_name,
        amount_micros=budget_amount_micros,
        explicitly_shared=False,
        temporary_resource_name=budget_resource_name,
    )
    campaign_operation = _build_campaign_create_operation(
        client=client,
        customer_id=customer_id,
        name=campaign_name,
        budget_resource_name=budget_resource_name,
        status=campaign_status,
        contains_eu_political_advertising=contains_eu_political_advertising,
        bidding_strategy_type=bidding_strategy_type,
        target_google_search=target_google_search,
        target_search_network=target_search_network,
        target_partner_search_network=target_partner_search_network,
        target_content_network=target_content_network,
        start_date_time=start_date_time,
        end_date_time=end_date_time,
        temporary_resource_name=campaign_resource_name,
    )
    ad_group_operation = _build_ad_group_create_operation(
        client=client,
        name=ad_group_name,
        campaign_resource_name=campaign_resource_name,
        status=ad_group_status,
        cpc_bid_micros=cpc_bid_micros,
        temporary_resource_name=ad_group_resource_name,
    )

    campaign_criterion_operations = [
        _wrap_mutate_operation(
            client,
            "campaign_criterion_operation",
            _build_campaign_location_operation(
                client,
                campaign_resource_name,
                resource_name,
            ),
        )
        for resource_name in geo_target_constant_resource_names
    ]
    campaign_criterion_operations.extend(
        _wrap_mutate_operation(
            client,
            "campaign_criterion_operation",
            _build_campaign_language_operation(
                client,
                campaign_resource_name,
                resource_name,
            ),
        )
        for resource_name in language_constant_resource_names
    )
    keyword_operations = [
        _wrap_mutate_operation(
            client,
            "ad_group_criterion_operation",
            _build_keyword_create_operation(
                client=client,
                ad_group_resource_name=ad_group_resource_name,
                text=text,
                match_type=keyword_match_type,
                status=keyword_status,
                negative=False,
            ),
        )
        for text in keyword_texts
    ]

    operations = [
        _wrap_mutate_operation(
            client,
            "campaign_budget_operation",
            budget_operation,
        ),
        _wrap_mutate_operation(
            client,
            "campaign_operation",
            campaign_operation,
        ),
        *campaign_criterion_operations,
        _wrap_mutate_operation(
            client,
            "ad_group_operation",
            ad_group_operation,
        ),
        *keyword_operations,
    ]
    operations.append(
        _wrap_mutate_operation(
            client,
            "ad_group_ad_operation",
            _build_responsive_search_ad_create_operation(
                client=client,
                ad_group_resource_name=ad_group_resource_name,
                final_urls=final_urls,
                headlines=headlines,
                descriptions=descriptions,
                status=ad_status,
                path1=path1,
                path2=path2,
            ),
        )
    )

    try:
        response = _google_ads_mutate(client, customer_id, operations, dry_run)
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "create_search_campaign_bundle",
        "customer_id": customer_id,
        "campaign_name": campaign_name,
        "budget_name": budget_name,
        "budget_amount_micros": budget_amount_micros,
        "campaign_status": campaign_status,
        "ad_group_status": ad_group_status,
        "keyword_status": keyword_status,
        "ad_status": ad_status,
        "operation_count": len(operations),
        "operation_count_by_type": {
            "campaign_budget_operation": 1,
            "campaign_operation": 1,
            "ad_group_operation": 1,
            "campaign_criterion_operation": (
                len(geo_target_constant_resource_names)
                + len(language_constant_resource_names)
            ),
            "ad_group_criterion_operation": len(keyword_texts),
            "ad_group_ad_operation": 1,
        },
        "temporary_resource_names": {
            "budget": budget_resource_name,
            "campaign": campaign_resource_name,
            "ad_group": ad_group_resource_name,
        },
        "resource_names": _result_resource_names(response),
    }


@writes_mcp.tool()
def create_webpage_conversion_action(
    customer_id: str,
    name: str,
    category: ConversionActionCategory = "SUBMIT_LEAD_FORM",
    status: ConversionActionStatus = "ENABLED",
    counting_type: ConversionActionCountingType = "ONE_PER_CLICK",
    default_value: float = 1.0,
    default_currency_code: str = "EUR",
    always_use_default_value: bool = False,
    primary_for_goal: bool = True,
    click_through_lookback_window_days: int = 90,
    view_through_lookback_window_days: int = 1,
    dry_run: bool = True,
    confirm_write: bool = False,
) -> Dict[str, Any]:
    """Create a guarded website conversion action for Google Ads tags.

    Defaults to validate-only mode. A real write requires dry_run=false and
    confirm_write=true. The conversion action is created as type WEBPAGE so it
    can be fired from GTM via a Google Ads conversion tag or gtag event.
    """
    customer_id = _normalize_customer_id(customer_id)
    name = _validate_name(name, "name")
    _require_confirmed_write(
        dry_run,
        confirm_write,
        "create a webpage conversion action",
    )
    if default_value < 0:
        raise ToolError("default_value must not be negative.")
    currency = default_currency_code.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ToolError("default_currency_code must be a 3-letter ISO code.")
    if not 1 <= click_through_lookback_window_days <= 90:
        raise ToolError(
            "click_through_lookback_window_days must be between 1 and 90."
        )
    if not 1 <= view_through_lookback_window_days <= 30:
        raise ToolError(
            "view_through_lookback_window_days must be between 1 and 30."
        )

    client = utils.get_googleads_client()
    conversion_action_service = utils.get_googleads_service(
        "ConversionActionService"
    )
    operation = _build_webpage_conversion_action_create_operation(
        client,
        name=name,
        category=category,
        status=status,
        counting_type=counting_type,
        default_value=default_value,
        default_currency_code=currency,
        always_use_default_value=always_use_default_value,
        primary_for_goal=primary_for_goal,
        click_through_lookback_window_days=(
            click_through_lookback_window_days
        ),
        view_through_lookback_window_days=view_through_lookback_window_days,
    )

    request = client.get_type("MutateConversionActionsRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = dry_run

    try:
        response = conversion_action_service.mutate_conversion_actions(
            request=request
        )
    except GoogleAdsException as ex:
        raise _google_ads_tool_error(ex)

    return {
        "applied": not dry_run,
        "validated_only": dry_run,
        "operation": "create_webpage_conversion_action",
        "customer_id": customer_id,
        "name": name,
        "category": category,
        "status": status,
        "counting_type": counting_type,
        "default_value": default_value,
        "default_currency_code": currency,
        "primary_for_goal": primary_for_goal,
        "resource_names": _result_resource_names(response),
    }
