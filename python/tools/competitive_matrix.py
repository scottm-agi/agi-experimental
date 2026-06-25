from __future__ import annotations
from python.helpers.tool import Tool, Response


def build_matrix_template(product: str, competitors: list[str]) -> str:
    """Build a structured competitive analysis matrix template."""

    competitor_headers = " | ".join(f"**{c}**" for c in competitors)

    result = f"# Competitive Analysis: {product}\n\n"

    # Feature Comparison
    result += "## Feature Comparison\n"
    result += f"| Feature | **{product}** | {competitor_headers} |\n"
    result += f"|---|---|{'---|' * len(competitors)}\n"
    result += f"| Core functionality | ✅ | {'[Research] | ' * len(competitors)}\n"
    result += f"| Integration depth | ✅ | {'[Research] | ' * len(competitors)}\n"
    result += f"| AI/Automation | ✅ | {'[Research] | ' * len(competitors)}\n"
    result += f"| Scalability | ✅ | {'[Research] | ' * len(competitors)}\n"
    result += f"| Customer support | ✅ | {'[Research] | ' * len(competitors)}\n\n"

    # Pricing Comparison
    result += "## Pricing Comparison\n"
    result += f"| Tier | **{product}** | {competitor_headers} |\n"
    result += f"|---|---|{'---|' * len(competitors)}\n"
    result += f"| Starter | [Price] | {'[Research] | ' * len(competitors)}\n"
    result += f"| Professional | [Price] | {'[Research] | ' * len(competitors)}\n"
    result += f"| Enterprise | [Price] | {'[Research] | ' * len(competitors)}\n\n"

    # Strengths & Weaknesses
    result += "## Strengths & Weaknesses\n\n"
    for comp in competitors:
        result += f"### {comp}\n"
        result += "| Strengths | Weaknesses |\n"
        result += "|---|---|\n"
        result += "| [Research needed] | [Research needed] |\n\n"

    # Win/Loss Themes
    result += "## Win/Loss Themes\n"
    result += "| Theme | When We Win | When We Lose | Mitigation |\n"
    result += "|---|---|---|---|\n"
    result += "| Price | [Scenario] | [Scenario] | [Strategy] |\n"
    result += "| Features | [Scenario] | [Scenario] | [Strategy] |\n"
    result += "| Integration | [Scenario] | [Scenario] | [Strategy] |\n"
    result += "| Support | [Scenario] | [Scenario] | [Strategy] |\n\n"

    # Market Positioning
    result += "## Market Positioning\n"
    result += f"| Dimension | **{product}** | {competitor_headers} |\n"
    result += f"|---|---|{'---|' * len(competitors)}\n"
    result += f"| Target Market | [Segment] | {'[Research] | ' * len(competitors)}\n"
    result += f"| Value Prop | [Core message] | {'[Research] | ' * len(competitors)}\n"
    result += f"| Differentiator | [Key differentiator] | {'[Research] | ' * len(competitors)}\n"

    return result


class CompetitiveMatrix(Tool):
    """Generate formatted competitive analysis matrices with feature comparison,
    pricing, strengths/weaknesses, win/loss themes, and market positioning.
    """

    async def execute(self, **kwargs) -> Response:
        product = self.args.get("product")
        competitors = self.args.get("competitors", [])

        if not product:
            return Response(
                message="Error: Missing required 'product' argument.",
                break_loop=False,
            )

        if isinstance(competitors, str):
            competitors = [c.strip() for c in competitors.split(",")]

        if not competitors:
            return Response(
                message="Error: Missing 'competitors' argument. Provide a list of competitor names.",
                break_loop=False,
            )

        matrix = build_matrix_template(product, competitors)

        instructions = (
            "\n---\n"
            "**Next Steps**: Use `search_engine` and `scrape_url` to research each competitor. "
            "Delegate deep research to Researcher via `call_subordinate` with profile `researcher`. "
            "Verify all pricing and feature claims with `fact_check`."
        )

        return Response(
            message=f"Generated competitive matrix:\n\n{matrix}{instructions}",
            break_loop=False,
        )
