from __future__ import annotations
from python.helpers.tool import Tool, Response


MEETING_TYPES = ["discovery", "demo", "negotiation"]


def build_meeting_brief(meeting_type: str, company: str, context: str = "") -> str:
    """Build a structured pre-meeting brief template."""

    agendas = {
        "discovery": {
            "objective": "Understand the prospect's pain points, current state, and decision-making process",
            "duration": "30 minutes",
            "items": [
                ("5 min", "Introduction & rapport building", "Share brief context, find common ground"),
                ("10 min", "Pain point deep-dive", "Ask open-ended questions about current challenges"),
                ("5 min", "Current state assessment", "Understand existing solutions, workflows, and gaps"),
                ("5 min", "Decision-making process", "Who's involved, timeline, budget considerations"),
                ("5 min", "Next steps & close", "Agree on follow-up actions and timeline"),
            ],
            "key_questions": [
                "What's the biggest challenge your team faces with [area]?",
                "How are you currently solving this? What's working / not working?",
                "What would success look like 6 months from now?",
                "Who else on your team would be involved in evaluating solutions?",
                "What's your timeline for making a decision?",
            ],
        },
        "demo": {
            "objective": "Demonstrate product value aligned to the prospect's specific pain points",
            "duration": "45 minutes",
            "items": [
                ("5 min", "Recap discovery findings", "Confirm pain points and priorities"),
                ("20 min", "Product demonstration", "Show features mapped to their specific needs"),
                ("10 min", "Q&A and objection handling", "Address concerns with evidence"),
                ("5 min", "ROI discussion", "Present value quantification"),
                ("5 min", "Next steps & close", "Propose evaluation or pilot plan"),
            ],
            "key_questions": [
                "Before I dive in — has anything changed since our last conversation?",
                "Which of your team members would be using this day-to-day?",
                "How does this compare to what you've seen from other solutions?",
                "What would need to be true for you to move forward?",
                "What's the best way to get this in front of [decision maker]?",
            ],
        },
        "negotiation": {
            "objective": "Reach a mutually beneficial agreement on terms, pricing, and timeline",
            "duration": "30 minutes",
            "items": [
                ("5 min", "Relationship check", "Confirm mutual interest and enthusiasm"),
                ("10 min", "Term discussion", "Walk through proposal, address each concern"),
                ("10 min", "Value reinforcement", "Tie back to ROI and business outcomes"),
                ("5 min", "Close & next steps", "Agree on terms or identify remaining blockers"),
            ],
            "key_questions": [
                "Is there anything in the proposal that doesn't align with your expectations?",
                "What would make this a no-brainer for you?",
                "Are there any internal approvals we should be preparing for?",
                "What's the ideal start date for your team?",
                "Is there a preferred contract structure (annual vs. multi-year)?",
            ],
        },
    }

    meeting = agendas.get(meeting_type, agendas["discovery"])

    result = f"# Pre-Meeting Brief: {meeting_type.title()} with {company}\n\n"
    result += f"**Objective**: {meeting['objective']}\n"
    result += f"**Duration**: {meeting['duration']}\n"
    if context:
        result += f"**Context**: {context}\n"
    result += "\n---\n\n"

    result += "## Company Snapshot\n"
    result += f"| Field | Details |\n|---|---|\n"
    result += f"| **Company** | {company} |\n"
    result += "| **Industry** | [Use search_engine to research] |\n"
    result += "| **Size** | [Use search_engine to research] |\n"
    result += "| **Recent News** | [Use search_engine to research] |\n"
    result += "| **Current Solution** | [From discovery / CRM notes] |\n\n"

    result += "## Attendee Profiles\n"
    result += "| Name | Role | Key Interests | Engagement Notes |\n"
    result += "|---|---|---|---|\n"
    result += "| [Research via LinkedIn] | [Title] | [What they care about] | [Previous interactions] |\n\n"

    result += "## Agenda\n"
    result += "| Time | Topic | Talking Points |\n"
    result += "|---|---|---|\n"
    for time, topic, points in meeting["items"]:
        result += f"| {time} | {topic} | {points} |\n"
    result += "\n"

    result += "## Key Questions to Ask\n"
    for i, q in enumerate(meeting["key_questions"], 1):
        result += f"{i}. {q}\n"
    result += "\n"

    result += "## Objection Handling\n"
    result += "| Likely Objection | Response Strategy | Supporting Evidence |\n"
    result += "|---|---|---|\n"
    result += "| \"Too expensive\" | Focus on ROI and cost of inaction | [Case study metrics] |\n"
    result += "| \"We're already using X\" | Highlight gaps and switching benefits | [Competitive comparison] |\n"
    result += "| \"Not the right time\" | Create urgency with market data | [Industry trends] |\n"
    result += "| \"Need more stakeholders\" | Offer to present to broader team | [Executive summary doc] |\n\n"

    result += "## Proposed Next Steps\n"
    if meeting_type == "discovery":
        result += "1. Send recap email within 24 hours\n"
        result += "2. Schedule product demo with expanded team\n"
        result += "3. Share relevant case study or ROI analysis\n"
    elif meeting_type == "demo":
        result += "1. Send recording & key slides within 24 hours\n"
        result += "2. Provide custom ROI analysis\n"
        result += "3. Schedule technical deep-dive or pilot setup\n"
        result += "4. Identify remaining stakeholders for next meeting\n"
    else:
        result += "1. Send updated proposal within 24 hours\n"
        result += "2. Set deadline for mutual decision\n"
        result += "3. Prepare implementation timeline\n"
        result += "4. Schedule kickoff call contingent on close\n"

    return result


class MeetingPrep(Tool):
    """Generate structured pre-meeting briefs with company snapshot, agenda,
    objection handling, and proposed next steps.

    Supports discovery, demo, and negotiation meeting types.
    """

    async def execute(self, **kwargs) -> Response:
        meeting_type = self.args.get("meeting_type", "discovery")
        company = self.args.get("company")
        context = self.args.get("context", "")

        if not company:
            return Response(
                message="Error: Missing required 'company' argument. Provide the company name for the meeting.",
                break_loop=False,
            )

        if meeting_type not in MEETING_TYPES:
            return Response(
                message=f"Error: Invalid meeting_type '{meeting_type}'. Valid types: {', '.join(MEETING_TYPES)}",
                break_loop=False,
            )

        brief = build_meeting_brief(meeting_type, company, context)

        instructions = (
            f"\n---\n"
            f"**Next Steps**: Use `search_engine` to research {company} and fill in the "
            f"Company Snapshot and Attendee Profiles. Delegate deep research to Researcher "
            f"via `call_subordinate` with profile `researcher`. Update CRM via `zoho_crm`."
        )

        return Response(
            message=f"Generated {meeting_type} meeting brief:\n\n{brief}{instructions}",
            break_loop=False,
        )
