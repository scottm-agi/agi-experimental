# Tool: Deal Scorecard

Generate structured deal qualification scorecards using BANT + MEDDIC frameworks.

## Usage

### Score a Deal
Create a deal qualification scorecard.
- **company** (required): Company/prospect name.
- **context** (optional): Deal context (e.g., "Enterprise SaaS deal, $50K ARR").

## Output
Generates a structured scorecard with:
- BANT Criteria (Budget, Authority, Need, Timeline) with score columns
- MEDDIC Criteria (Metrics, Economic Buyer, Decision Process, Identified Pain, Champion)
- Overall Assessment (total score, deal stage, probability, expected close)
- Recommendation Matrix (Pursue / Nurture / Qualify Further / Disqualify based on score range)
- Risk Factors table
- Prioritized Next Steps

## Notes
- Fill in scores (1-5) and evidence from discovery and research.
- Use `zoho_crm` to check existing lead/deal data.
- Delegate prospect research to Researcher via `call_subordinate`.
- Best used by Account Leader agent.
