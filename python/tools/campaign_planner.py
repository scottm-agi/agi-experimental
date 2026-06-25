from __future__ import annotations
from python.helpers.tool import Tool, Response


def build_campaign_template(goal: str, audience: str, budget: str, timeframe: str) -> str:
    """Build a structured marketing campaign plan template."""

    result = f"# Marketing Campaign Plan\n\n"

    result += "## Campaign Brief\n"
    result += "| Field | Details |\n"
    result += "|---|---|\n"
    result += f"| **Objective** | {goal} |\n"
    result += f"| **Target Audience** | {audience} |\n"
    result += f"| **Budget** | {budget} |\n"
    result += f"| **Timeframe** | {timeframe} |\n"
    result += "| **Key Message** | [Define core value proposition] |\n"
    result += "| **Call to Action** | [Primary CTA] |\n\n"

    result += "## Channel Strategy\n"
    result += "| Channel | Budget % | Expected Reach | Expected CPA | Priority |\n"
    result += "|---|---|---|---|---|\n"
    result += "| LinkedIn Ads | 30% | [Estimate] | [Estimate] | High |\n"
    result += "| Content Marketing | 25% | [Estimate] | [Estimate] | High |\n"
    result += "| Email Marketing | 15% | [Estimate] | [Estimate] | Medium |\n"
    result += "| Google Ads | 15% | [Estimate] | [Estimate] | Medium |\n"
    result += "| Social Media | 10% | [Estimate] | [Estimate] | Low |\n"
    result += "| Events/Webinars | 5% | [Estimate] | [Estimate] | Low |\n\n"

    result += "## Content Calendar\n"
    result += "| Week | Channel | Content Type | Topic | Status |\n"
    result += "|---|---|---|---|---|\n"
    result += "| Week 1 | Blog | Thought leadership | [Topic] | 📝 Draft |\n"
    result += "| Week 1 | LinkedIn | Post | [Topic] | 📝 Draft |\n"
    result += "| Week 2 | Email | Newsletter | [Topic] | 📝 Draft |\n"
    result += "| Week 2 | Blog | How-to guide | [Topic] | 📝 Draft |\n"
    result += "| Week 3 | LinkedIn | Case study | [Topic] | 📝 Draft |\n"
    result += "| Week 3 | Webinar | Live session | [Topic] | 📝 Draft |\n"
    result += "| Week 4 | Email | Campaign recap | [Topic] | 📝 Draft |\n"
    result += "| Week 4 | Social | Results share | [Topic] | 📝 Draft |\n\n"

    result += "## KPIs\n"
    result += "| Metric | Baseline | Target | Measurement Method |\n"
    result += "|---|---|---|---|\n"
    result += "| Leads generated | [Current] | [Target] | CRM / form fills |\n"
    result += "| Website traffic | [Current] | [Target] | Analytics |\n"
    result += "| Email open rate | [Current] | [Target] | ESP dashboard |\n"
    result += "| Cost per lead | [Current] | [Target] | Ad platforms |\n"
    result += "| Conversion rate | [Current] | [Target] | CRM pipeline |\n"
    result += "| Pipeline generated | [Current] | [Target] | CRM |\n\n"

    result += "## A/B Testing\n"
    result += "| Element | Variant A | Variant B | Success Metric | Duration |\n"
    result += "|---|---|---|---|---|\n"
    result += "| Email subject | [Option A] | [Option B] | Open rate | 1 week |\n"
    result += "| Landing page CTA | [Option A] | [Option B] | Click-through | 2 weeks |\n"
    result += "| Ad creative | [Option A] | [Option B] | CTR + CPA | 2 weeks |\n"
    result += "| Content format | [Long-form] | [Short-form] | Engagement | 2 weeks |\n"

    return result


class CampaignPlanner(Tool):
    """Create structured marketing campaign plans with channel strategy,
    content calendar, KPIs, and A/B testing plans.
    """

    async def execute(self, **kwargs) -> Response:
        goal = self.args.get("goal")
        audience = self.args.get("audience", "General audience")
        budget = self.args.get("budget", "TBD")
        timeframe = self.args.get("timeframe", "Q1")

        if not goal:
            return Response(
                message="Error: Missing required 'goal' argument. Describe the campaign objective.",
                break_loop=False,
            )

        plan = build_campaign_template(goal, audience, budget, timeframe)

        instructions = (
            "\n---\n"
            "**Next Steps**: Use `growth_scout` to find trending tactics for this audience. "
            "Use `search_engine` to research competitor campaigns. "
            "Delegate market research to Researcher via `call_subordinate` with profile `researcher`."
        )

        return Response(
            message=f"Generated campaign plan:\n\n{plan}{instructions}",
            break_loop=False,
        )
