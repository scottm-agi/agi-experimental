# Tool: Meeting Prep

Generate structured pre-meeting briefs with agenda, talking points, and objection handling.

## Usage

### Prepare Meeting Brief
Create a comprehensive pre-meeting preparation document.
- **company** (required): Company name for the meeting.
- **meeting_type** (optional, default: "discovery"): Type — "discovery", "demo", or "negotiation".
- **context** (optional): Additional meeting context (e.g., "follow-up from trade show").

## Output
Generates a structured brief with:
- Company Snapshot (fill via research)
- Attendee Profiles table
- Timed Agenda with talking points per section
- Key Questions to ask
- Objection Handling matrix (objection → response → evidence)
- Proposed Next Steps

## Notes
- Use `search_engine` to research the company and fill in the Company Snapshot.
- Delegate deep research to Researcher via `call_subordinate`.
- Update CRM via `zoho_crm` after the meeting.
- Best used by Sales Enabler and Account Leader agents.
