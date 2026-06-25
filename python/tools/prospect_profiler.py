from __future__ import annotations
from python.helpers.tool import Tool, Response


PROFILE_SECTIONS = [
    "Company Overview",
    "Key Contacts",
    "Pain Points",
    "Budget Signals",
    "Competitive Landscape",
    "Recommended Approach",
]


def build_profile_template(company: str, industry: str = "General") -> str:
    """Build a structured prospect profile template in markdown."""
    return f"""# Prospect Profile: {company}

## Company Overview
| Field | Details |
|---|---|
| **Company** | {company} |
| **Industry** | {industry} |
| **Size** | [Research needed — use search_engine] |
| **Revenue** | [Research needed — use search_engine] |
| **Headquarters** | [Research needed] |
| **Tech Stack** | [Research needed — use scrape_url on company site] |
| **Recent News** | [Research needed — use search_engine] |

## Key Contacts
| Name | Role | Relevance | LinkedIn |
|---|---|---|---|
| [Research needed] | [Role] | [Decision maker / Influencer / Champion] | [URL] |

## Pain Points
Map each pain point to your value proposition:

| Pain Point | Evidence | Our Solution | Value |
|---|---|---|---|
| [Identified pain] | [Source/signal] | [How we solve it] | [Quantified impact] |

## Budget Signals
- **Funding**: [Recent funding rounds, revenue growth signals]
- **Spending Patterns**: [Tech purchases, vendor changes, job postings]
- **Budget Cycle**: [Fiscal year end, budget planning timeline]

## Competitive Landscape
| Current Solution | Strengths | Weaknesses | Our Advantage |
|---|---|---|---|
| [Current vendor/tool] | [What they do well] | [Gaps] | [How we win] |

## Recommended Approach
1. **Entry Strategy**: [How to get in — referral, cold outreach, event]
2. **Key Message**: [Tailored value prop for this prospect]
3. **Proof Points**: [Relevant case studies, metrics]
4. **Timeline**: [Suggested outreach cadence]
5. **Risk Factors**: [Potential blockers and mitigation]
"""


class ProspectProfiler(Tool):
    """Generate structured prospect profiles from company research data.

    This tool creates a comprehensive, structured template for prospect profiling.
    The agent should then use search_engine, scrape_url, and Researcher to fill
    in the template with real data.
    """

    async def execute(self, **kwargs) -> Response:
        company = self.args.get("company")
        industry = self.args.get("industry", "General")
        context = self.args.get("context", "")

        if not company:
            return Response(
                message="Error: Missing required 'company' argument. Provide the company name to profile.",
                break_loop=False,
            )

        template = build_profile_template(company, industry)

        if context:
            template += f"\n## Additional Context\n{context}\n"

        instructions = (
            f"\n---\n"
            f"**Next Steps**: Use `search_engine` and `scrape_url` to research {company} "
            f"and fill in the template above. Delegate deep research to Researcher via "
            f"`call_subordinate` with profile `researcher`. Verify all data with `fact_check`."
        )

        return Response(
            message=f"Generated prospect profile template for {company}:\n\n{template}{instructions}",
            break_loop=False,
        )
