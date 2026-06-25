# Tool: Campaign Planner

Create structured marketing campaign plans with channel strategy, content calendar, and KPIs.

## Usage

### Plan a Campaign
Generate a complete marketing campaign plan.
- **goal** (required): Campaign objective (e.g., "Generate 100 qualified leads").
- **audience** (optional): Target audience description.
- **budget** (optional): Budget range (e.g., "$5,000/month").
- **timeframe** (optional): Campaign timeframe (e.g., "Q2 2026").

## Output
Generates a structured campaign plan with:
- Campaign Brief (objective, audience, message, CTA)
- Channel Strategy with budget allocation percentages
- Weekly Content Calendar
- KPIs with baseline, target, and measurement method
- A/B Testing plan

## Notes
- Use `growth_scout` to find trending tactics for the target audience.
- Use `search_engine` to research competitor campaigns.
- Delegate market research to Researcher via `call_subordinate`.
- Best used by Marketing Lead agent.
