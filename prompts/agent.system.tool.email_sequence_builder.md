# Tool: Email Sequence Builder

Generate multi-step outreach email sequences with A/B subject lines and personalization.

## Usage

### Build Email Sequence
Create a structured email outreach sequence.
- **sequence_type** (optional, default: "cold"): Type of sequence — "cold", "warm", or "referral".
- **company** (optional, default: "Target Company"): Prospect company name.
- **product** (optional, default: "Our Solution"): Product/service being promoted.

## Output
Generates a complete email sequence with:
- Multiple emails with send timing
- A/B subject line variants per email
- Body templates with personalization tokens
- Follow-up trigger conditions
- Personalization token reference table

## Notes
- Personalization tokens (e.g., `{{first_name}}`, `{{trigger_event}}`) should be filled in from CRM data or research.
- Use `prospect_profiler` first to gather company intel, then feed into the email sequence.
- Best used by Sales Enabler agent.
