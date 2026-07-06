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

from typing import Any, Dict, List, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from mcp.types import ToolAnnotations

import ads_mcp.utils as utils

budget_mcp = FastMCP("budget")
writes_mcp = FastMCP("writes")

CampaignStatus = Literal["ENABLED", "PAUSED"]


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


def _google_ads_tool_error(ex: GoogleAdsException) -> ToolError:
    lines = [f"Request ID: {ex.request_id}"]
    for error in ex.failure.errors:
        lines.append(f"Google Ads API Error: {error.message}")
        if error.location:
            for field_path_element in error.location.field_path_elements:
                lines.append(f"On field: {field_path_element.field_name}")
    return ToolError("\n".join(lines))


def _result_resource_names(response: Any) -> List[str]:
    return [
        result.resource_name
        for result in getattr(response, "results", [])
        if getattr(result, "resource_name", None)
    ]


def _fetch_campaign_budget(customer_id: str, campaign_id: str) -> Dict[str, Any]:
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
                    "campaign_status": utils.format_output_value(row.campaign.status),
                    "budget_resource_name": row.campaign_budget.resource_name,
                    "budget_name": row.campaign_budget.name,
                    "amount_micros": int(row.campaign_budget.amount_micros),
                    "explicitly_shared": bool(row.campaign_budget.explicitly_shared),
                    "period": utils.format_output_value(row.campaign_budget.period),
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
            (target_amount_micros - current_amount_micros) / current_amount_micros * 100
            if current_amount_micros
            else 100
        )
        if increase_percent > max_increase_percent:
            raise ToolError(
                "Budget update blocked: increase exceeds " "max_increase_percent."
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
                        "budget_resource_name": (row.campaign_budget.resource_name),
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
                entry["conversion_value"] += float(row.metrics.conversions_value)
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
    campaign.resource_name = campaign_service.campaign_path(customer_id, campaign_id)
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
        raise ToolError("max_daily_budget_amount_micros must be greater than 0.")
    if max_increase_percent < 0 or max_increase_percent > 100:
        raise ToolError("max_increase_percent must be between 0 and 100.")
    if not dry_run and not confirm_write:
        raise ToolError("Set confirm_write=true with dry_run=false to update a budget.")

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
