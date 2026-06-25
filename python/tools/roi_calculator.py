from __future__ import annotations
import math
from python.helpers.tool import Tool, Response


def calculate_roi(
    deal_size: float,
    cost_of_acquisition: float,
    monthly_recurring: float,
    timeline_months: int = 12,
) -> dict:
    """Calculate ROI metrics from deal parameters."""
    # Payback period in months
    payback_months = math.ceil(cost_of_acquisition / monthly_recurring) if monthly_recurring > 0 else 0

    # Customer lifetime value over the given timeline
    clv_12_months = monthly_recurring * timeline_months

    # Total revenue over timeline
    total_revenue = deal_size + clv_12_months

    # ROI percentage
    roi_percentage = ((total_revenue - cost_of_acquisition) / cost_of_acquisition * 100) if cost_of_acquisition > 0 else 0

    # Break-even month
    break_even_month = payback_months

    return {
        "deal_size": deal_size,
        "cost_of_acquisition": cost_of_acquisition,
        "monthly_recurring": monthly_recurring,
        "timeline_months": timeline_months,
        "payback_months": payback_months,
        "clv_12_months": clv_12_months,
        "total_revenue": total_revenue,
        "roi_percentage": round(roi_percentage, 1),
        "break_even_month": break_even_month,
    }


def build_scenario_comparison(
    deal_size: float,
    cost_of_acquisition: float,
    monthly_recurring: float,
) -> str:
    """Build scenario comparison (conservative/moderate/aggressive)."""

    scenarios = {
        "Conservative": {
            "deal_multiplier": 0.8,
            "mrr_multiplier": 0.7,
            "timeline": 12,
        },
        "Moderate": {
            "deal_multiplier": 1.0,
            "mrr_multiplier": 1.0,
            "timeline": 12,
        },
        "Aggressive": {
            "deal_multiplier": 1.3,
            "mrr_multiplier": 1.5,
            "timeline": 12,
        },
    }

    result = "# ROI Scenario Comparison\n\n"

    for name, params in scenarios.items():
        adj_deal = deal_size * params["deal_multiplier"]
        adj_mrr = monthly_recurring * params["mrr_multiplier"]
        metrics = calculate_roi(adj_deal, cost_of_acquisition, adj_mrr, params["timeline"])

        result += f"## {name}\n"
        result += f"| Metric | Value |\n|---|---|\n"
        result += f"| Deal Size | ${metrics['deal_size']:,.0f} |\n"
        result += f"| MRR | ${metrics['monthly_recurring']:,.0f} |\n"
        result += f"| Payback Period | {metrics['payback_months']} months |\n"
        result += f"| 12-Month CLV | ${metrics['clv_12_months']:,.0f} |\n"
        result += f"| ROI | {metrics['roi_percentage']}% |\n\n"

    return result


def format_roi_report(metrics: dict) -> str:
    """Format ROI metrics as a markdown report."""
    result = "# ROI Analysis\n\n"
    result += "## Summary\n"
    result += "| Metric | Value |\n|---|---|\n"
    result += f"| **Deal Size** | ${metrics['deal_size']:,.0f} |\n"
    result += f"| **Cost of Acquisition** | ${metrics['cost_of_acquisition']:,.0f} |\n"
    result += f"| **Monthly Recurring** | ${metrics['monthly_recurring']:,.0f}/mo |\n"
    result += f"| **Payback Period** | {metrics['payback_months']} months |\n"
    result += f"| **12-Month CLV** | ${metrics['clv_12_months']:,.0f} |\n"
    result += f"| **Total Revenue ({metrics['timeline_months']}mo)** | ${metrics['total_revenue']:,.0f} |\n"
    result += f"| **ROI** | **{metrics['roi_percentage']}%** |\n"
    result += f"| **Break-Even** | Month {metrics['break_even_month']} |\n"

    return result


class ROICalculator(Tool):
    """Calculate and format marketing/sales ROI projections with
    payback period, CLV, ROI percentage, break-even analysis,
    and scenario comparison (conservative/moderate/aggressive).
    """

    async def execute(self, **kwargs) -> Response:
        deal_size = self.args.get("deal_size")
        cost_of_acquisition = self.args.get("cost_of_acquisition")
        monthly_recurring = self.args.get("monthly_recurring", 0)
        timeline_months = self.args.get("timeline_months", 12)

        if deal_size is None or cost_of_acquisition is None:
            return Response(
                message="Error: Missing required arguments. Provide 'deal_size' and 'cost_of_acquisition'.",
                break_loop=False,
            )

        try:
            deal_size = float(deal_size)
            cost_of_acquisition = float(cost_of_acquisition)
            monthly_recurring = float(monthly_recurring)
            timeline_months = int(timeline_months)
        except (ValueError, TypeError) as e:
            return Response(
                message=f"Error: Invalid numeric argument: {e}",
                break_loop=False,
            )

        metrics = calculate_roi(deal_size, cost_of_acquisition, monthly_recurring, timeline_months)
        report = format_roi_report(metrics)
        scenarios = build_scenario_comparison(deal_size, cost_of_acquisition, monthly_recurring)

        return Response(
            message=f"{report}\n\n{scenarios}",
            break_loop=False,
        )
