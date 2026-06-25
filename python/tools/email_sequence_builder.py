from __future__ import annotations
from python.helpers.tool import Tool, Response


SEQUENCE_TYPES = ["cold", "warm", "referral"]


def build_email_sequence(sequence_type: str, company: str, product: str) -> str:
    """Build a multi-step email sequence template."""

    sequences = {
        "cold": [
            {
                "step": 1,
                "name": "The Opener",
                "timing": "Day 1",
                "subject_a": f"Quick question about {{{{company_name}}}}'s growth",
                "subject_b": f"Saw {{{{trigger_event}}}} — had an idea for {{{{company_name}}}}",
                "body": f"""Hi {{{{first_name}}}},

I noticed {{{{trigger_event}}}} at {company}. Congrats!

Companies like yours in {{{{industry}}}} often struggle with {{{{pain_point}}}}.

We built {product} specifically to solve this — {{{{proof_point}}}}.

Worth a 15-min call this week?

Best,
{{{{sender_name}}}}""",
                "follow_up_trigger": "No reply after 3 business days",
            },
            {
                "step": 2,
                "name": "The Value Add",
                "timing": "Day 4",
                "subject_a": f"Re: Quick question about {{{{company_name}}}}'s growth",
                "subject_b": f"{{{{industry}}}} insight for {{{{first_name}}}}",
                "body": f"""Hi {{{{first_name}}}},

Following up — wanted to share a quick insight:

{{{{industry_stat_or_case_study}}}}

{product} helped a similar company achieve {{{{result_metric}}}}. Happy to show you how.

Free for a quick call {{{{suggested_times}}}}?

{{{{sender_name}}}}""",
                "follow_up_trigger": "No reply after 3 business days",
            },
            {
                "step": 3,
                "name": "The Breakup",
                "timing": "Day 10",
                "subject_a": f"Should I close the file on {{{{company_name}}}}?",
                "subject_b": f"Last try — {{{{pain_point}}}} at {{{{company_name}}}}",
                "body": f"""Hi {{{{first_name}}}},

I know you're busy, so I'll keep this brief.

If {{{{pain_point}}}} isn't a priority right now, no worries at all. I'll close the loop on my end.

But if there's a better time or person to chat with, just point me in the right direction.

Either way, I wish you and {company} the best.

{{{{sender_name}}}}""",
                "follow_up_trigger": "End of sequence — move to nurture",
            },
        ],
        "warm": [
            {
                "step": 1,
                "name": "The Warm Reconnect",
                "timing": "Day 1",
                "subject_a": f"Great connecting at {{{{event_name}}}} — next steps?",
                "subject_b": f"Following up on our {{{{context}}}} conversation",
                "body": f"""Hi {{{{first_name}}}},

Great chatting at {{{{event_name}}}}! Your point about {{{{their_insight}}}} really resonated.

As promised, here's {{{{resource_link}}}} — I think it directly addresses what you mentioned about {{{{pain_point}}}}.

Would love to continue the conversation. Free for coffee next week?

{{{{sender_name}}}}""",
                "follow_up_trigger": "No reply after 5 business days",
            },
            {
                "step": 2,
                "name": "The Deep Dive",
                "timing": "Day 6",
                "subject_a": f"Thought of {{{{company_name}}}} when I saw this",
                "subject_b": f"{{{{industry}}}} case study — relevant to our conversation",
                "body": f"""Hi {{{{first_name}}}},

Came across this and immediately thought of {company}: {{{{case_study_link}}}}

They faced a similar challenge with {{{{pain_point}}}} and saw {{{{result_metric}}}} with {product}.

Worth a 20-min call to explore if this could work for you?

{{{{sender_name}}}}""",
                "follow_up_trigger": "No reply after 4 business days",
            },
        ],
        "referral": [
            {
                "step": 1,
                "name": "The Referral Intro",
                "timing": "Day 1",
                "subject_a": f"{{{{referrer_name}}}} suggested we connect",
                "subject_b": f"Introduction from {{{{referrer_name}}}} — {product}",
                "body": f"""Hi {{{{first_name}}}},

{{{{referrer_name}}}} mentioned you'd be a great person to speak with about {{{{pain_point}}}} at {company}.

We've been helping companies like {{{{similar_company}}}} achieve {{{{result_metric}}}} with {product}.

{{{{referrer_name}}}} thought it could be valuable for your team given {{{{context}}}}.

Would you be open to a quick 15-min intro call?

{{{{sender_name}}}}""",
                "follow_up_trigger": "No reply after 5 business days",
            },
        ],
    }

    emails = sequences.get(sequence_type, sequences["cold"])

    result = f"# {sequence_type.title()} Email Sequence: {product} → {company}\n\n"
    result += f"**Sequence Type**: {sequence_type.title()} | **Emails**: {len(emails)} | **Total Duration**: {emails[-1]['timing']}\n\n"
    result += "---\n\n"

    for email in emails:
        result += f"## Email {email['step']}: {email['name']}\n\n"
        result += f"**Send Timing**: {email['timing']}\n\n"
        result += f"### Subject Line A\n`{email['subject_a']}`\n\n"
        result += f"### Subject Line B\n`{email['subject_b']}`\n\n"
        result += f"### Body\n```\n{email['body']}\n```\n\n"
        result += f"**Follow-up Trigger**: {email['follow_up_trigger']}\n\n"
        result += "---\n\n"

    result += "## Personalization Tokens\n\n"
    result += "| Token | Description | How to Find |\n"
    result += "|---|---|---|\n"
    result += "| `{{first_name}}` | Prospect's first name | CRM / LinkedIn |\n"
    result += "| `{{company_name}}` | Company name | CRM |\n"
    result += "| `{{trigger_event}}` | Recent company event | search_engine / news |\n"
    result += "| `{{pain_point}}` | Key business challenge | Research / discovery |\n"
    result += "| `{{proof_point}}` | Relevant case study metric | Internal |\n"
    result += "| `{{industry}}` | Prospect's industry | CRM / LinkedIn |\n"

    return result


class EmailSequenceBuilder(Tool):
    """Generate multi-step outreach email sequences with A/B subject lines.

    Creates structured email sequences tailored for cold, warm, or referral
    outreach with personalization tokens, timing recommendations, and
    follow-up triggers.
    """

    async def execute(self, **kwargs) -> Response:
        sequence_type = self.args.get("sequence_type", "cold")
        company = self.args.get("company", "Target Company")
        product = self.args.get("product", "Our Solution")

        if sequence_type not in SEQUENCE_TYPES:
            return Response(
                message=f"Error: Invalid sequence_type '{sequence_type}'. Valid types: {', '.join(SEQUENCE_TYPES)}",
                break_loop=False,
            )

        result = build_email_sequence(sequence_type, company, product)

        return Response(
            message=f"Generated {sequence_type} email sequence:\n\n{result}",
            break_loop=False,
        )
