# Tool: ROI Calculator

Calculate and format marketing/sales ROI projections with scenario comparison.

## Usage

### Calculate ROI
Generate a comprehensive ROI analysis.
- **deal_size** (required): Initial deal value in dollars.
- **cost_of_acquisition** (required): Total cost to acquire the customer.
- **monthly_recurring** (optional, default: 0): Monthly recurring revenue from the customer.
- **timeline_months** (optional, default: 12): Analysis timeline in months.

## Output
Generates a detailed ROI report with:
- Summary table (deal size, CAC, MRR, payback period, CLV, ROI %, break-even)
- Scenario Comparison: Conservative (0.8x), Moderate (1.0x), Aggressive (1.3x)

## Notes
- Use real data from CRM (`zoho_crm`) or discovery calls for accurate inputs.
- Present scenarios to help prospects understand value range.
- Best used by Account Leader agent.
