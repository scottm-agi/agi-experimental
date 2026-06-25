from __future__ import annotations
from python.helpers.tool import Tool, Response


SCORING_DIMENSIONS = {
    "budget": "Budget identified and allocated for this type of solution",
    "authority": "Decision-maker identified and engaged",
    "need": "Clear pain point that maps to our value proposition",
    "timeline": "Defined timeline or urgency signal for purchase decision",
    "metrics": "Success criteria and KPIs defined for the project",
    "economic_buyer": "Economic buyer (person who signs the check) identified",
    "decision_process": "Full decision-making process mapped (approvals, legal, procurement)",
    "identified_pain": "Pain quantified in business terms (cost, time, risk)",
    "champion": "Internal advocate identified who will push for our solution",
}


def build_scorecard_template(company: str, deal_context: str = "") -> str:
    """Build a structured deal qualification scorecard."""

    result = f"# Deal Scorecard: {company}\n\n"
    if deal_context:
        result += f"**Context**: {deal_context}\n\n"
    result += "---\n\n"

    result += "## Deal Qualification\n\n"

    result += "### BANT Criteria\n"
    result += "| Dimension | Score (1-5) | Evidence | Status |\n"
    result += "|---|---|---|---|\n"
    result += "| **Budget** | [1-5] | [What budget signals exist?] | 🔴🟡🟢 |\n"
    result += "| **Authority** | [1-5] | [Who is the decision maker?] | 🔴🟡🟢 |\n"
    result += "| **Need** | [1-5] | [What pain point alignment?] | 🔴🟡🟢 |\n"
    result += "| **Timeline** | [1-5] | [What urgency signals?] | 🔴🟡🟢 |\n\n"

    result += "### MEDDIC Criteria\n"
    result += "| Dimension | Score (1-5) | Evidence | Status |\n"
    result += "|---|---|---|---|\n"
    result += "| **Metrics** | [1-5] | [Success criteria defined?] | 🔴🟡🟢 |\n"
    result += "| **Economic Buyer** | [1-5] | [Identified and engaged?] | 🔴🟡🟢 |\n"
    result += "| **Decision Process** | [1-5] | [Fully mapped?] | 🔴🟡🟢 |\n"
    result += "| **Identified Pain** | [1-5] | [Quantified in business terms?] | 🔴🟡🟢 |\n"
    result += "| **Champion** | [1-5] | [Internal advocate active?] | 🔴🟡🟢 |\n\n"

    result += "### Overall Assessment\n"
    result += "| Field | Value |\n"
    result += "|---|---|\n"
    result += "| **Total Score** | [X / 45] |\n"
    result += "| **Deal Stage** | [Discovery / Qualification / Proposal / Negotiation / Closed] |\n"
    result += "| **Probability** | [%] |\n"
    result += "| **Expected Close** | [Date] |\n"
    result += "| **Deal Size** | [$X] |\n\n"

    result += "### Recommendation\n"
    result += "| Score Range | Action | Description |\n"
    result += "|---|---|---|\n"
    result += "| 36-45 | ✅ **PURSUE** | Strong deal — accelerate and allocate resources |\n"
    result += "| 25-35 | 🟡 **NURTURE** | Promising but gaps exist — address missing criteria |\n"
    result += "| 15-24 | 🟠 **QUALIFY FURTHER** | Too many unknowns — need more discovery |\n"
    result += "| 1-14 | 🔴 **DISQUALIFY** | Low probability — redirect effort to higher-value deals |\n\n"

    result += "### Risk Factors\n"
    result += "| Risk | Likelihood | Impact | Mitigation |\n"
    result += "|---|---|---|---|\n"
    result += "| [Risk 1] | High/Med/Low | High/Med/Low | [Strategy] |\n"
    result += "| [Risk 2] | High/Med/Low | High/Med/Low | [Strategy] |\n\n"

    result += "### Next Steps\n"
    result += "1. [Action item with owner and deadline]\n"
    result += "2. [Action item with owner and deadline]\n"
    result += "3. [Action item with owner and deadline]\n"

    return result


class DealScorecard(Tool):
    """Generate structured deal qualification scorecards using
    BANT + MEDDIC frameworks with scoring, evidence tracking,
    and pursue/nurture/disqualify recommendations.
    """

    async def execute(self, **kwargs) -> Response:
        company = self.args.get("company")
        deal_context = self.args.get("context", "")

        if not company:
            return Response(
                message="Error: Missing required 'company' argument.",
                break_loop=False,
            )

        scorecard = build_scorecard_template(company, deal_context)

        instructions = (
            "\n---\n"
            "**Next Steps**: Use `search_engine` to research the company and fill in evidence. "
            "Check `zoho_crm` for existing lead/deal data. "
            "Delegate deep prospect research to Researcher via `call_subordinate` with profile `researcher`."
        )

        return Response(
            message=f"Generated deal scorecard:\n\n{scorecard}{instructions}",
            break_loop=False,
        )
