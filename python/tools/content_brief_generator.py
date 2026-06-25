from __future__ import annotations
from python.helpers.tool import Tool, Response


CONTENT_TYPES = ["blog_post", "social_media", "email", "case_study", "whitepaper", "landing_page"]


def build_content_brief(topic: str, content_type: str, audience: str, keywords: list[str] = None) -> str:
    """Build a structured content brief template."""

    keywords_str = ", ".join(keywords) if keywords else "[Research via search_engine]"

    type_labels = {
        "blog_post": "Blog Post",
        "social_media": "Social Media Post",
        "email": "Email Campaign",
        "case_study": "Case Study",
        "whitepaper": "Whitepaper",
        "landing_page": "Landing Page",
    }
    type_label = type_labels.get(content_type, content_type.replace("_", " ").title())

    result = f"# Content Brief: {type_label}\n\n"
    result += f"**Topic**: {topic}\n"
    result += f"**Target Audience**: {audience}\n"
    result += f"**Primary Keywords**: {keywords_str}\n\n"
    result += "---\n\n"

    result += "## Title Options\n"
    result += "1. [Option A — straightforward value prop]\n"
    result += "2. [Option B — curiosity/question-based]\n"
    result += "3. [Option C — data/number-driven]\n\n"

    result += "## Outline\n"

    if content_type == "blog_post":
        result += "1. **Introduction** (150 words)\n"
        result += "   - Hook with surprising stat or question\n"
        result += "   - State the problem clearly\n"
        result += "   - Preview the solution\n"
        result += "2. **Section 1: The Problem** (200 words)\n"
        result += "   - Current state and pain points\n"
        result += "   - Data to quantify the problem\n"
        result += "3. **Section 2: The Solution** (300 words)\n"
        result += "   - Your approach / framework\n"
        result += "   - Step-by-step implementation\n"
        result += "4. **Section 3: Real-World Example** (200 words)\n"
        result += "   - Case study or scenario\n"
        result += "   - Results and metrics\n"
        result += "5. **Conclusion** (100 words)\n"
        result += "   - Key takeaways\n"
        result += "   - Clear CTA\n\n"
    elif content_type == "case_study":
        result += "1. **Overview** — Client name, industry, challenge\n"
        result += "2. **The Challenge** — Specific problem and context\n"
        result += "3. **The Solution** — How we helped, implementation details\n"
        result += "4. **The Results** — Quantified outcomes with metrics\n"
        result += "5. **Testimonial** — Client quote\n"
        result += "6. **Key Takeaways** — Lessons for similar companies\n\n"
    else:
        result += "1. **Hook / Subject** — Attention-grabbing opener\n"
        result += "2. **Value Proposition** — Core message\n"
        result += "3. **Supporting Evidence** — Data or social proof\n"
        result += "4. **Call to Action** — Clear next step\n\n"

    result += "## Key Messages\n"
    result += "- **Primary**: [Core value message]\n"
    result += "- **Supporting 1**: [Proof point / metric]\n"
    result += "- **Supporting 2**: [Differentiation message]\n"
    result += "- **Supporting 3**: [Social proof / testimonial]\n\n"

    result += "## SEO Metadata\n"
    result += "| Field | Content |\n"
    result += "|---|---|\n"
    result += f"| **Title Tag** | {topic} — [Value prop] (60 chars max) |\n"
    result += f"| **Meta Description** | [Compelling summary, 155 chars max] |\n"
    result += f"| **Primary Keyword** | {keywords[0] if keywords else '[Research]'} |\n"
    result += f"| **Secondary Keywords** | {keywords_str} |\n"
    result += "| **URL Slug** | [topic-slug-here] |\n\n"

    result += "## CTA\n"
    result += "| Placement | CTA Text | Destination |\n"
    result += "|---|---|---|\n"
    result += "| End of content | [Primary CTA] | [URL] |\n"
    result += "| Mid-content | [Soft CTA] | [URL] |\n"
    result += "| Sidebar/banner | [Download CTA] | [URL] |\n\n"

    result += "## Distribution Channels\n"
    result += "| Channel | Format | Timing | Notes |\n"
    result += "|---|---|---|---|\n"
    result += "| Website/Blog | Full article | Publish day | SEO-optimized |\n"
    result += "| LinkedIn | Summary + link | Same day | Tag relevant people |\n"
    result += "| Email | Newsletter feature | Day +1 | Segment by audience |\n"
    result += "| Twitter/X | Thread + link | Day +1 | Key insights as thread |\n"

    return result


class ContentBriefGenerator(Tool):
    """Generate structured content briefs for blog posts, social media,
    email campaigns, case studies, and more.
    """

    async def execute(self, **kwargs) -> Response:
        topic = self.args.get("topic")
        content_type = self.args.get("content_type", "blog_post")
        audience = self.args.get("audience", "General audience")
        keywords = self.args.get("keywords", [])

        if not topic:
            return Response(
                message="Error: Missing required 'topic' argument.",
                break_loop=False,
            )

        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",")]

        if content_type not in CONTENT_TYPES:
            return Response(
                message=f"Error: Invalid content_type '{content_type}'. Valid: {', '.join(CONTENT_TYPES)}",
                break_loop=False,
            )

        brief = build_content_brief(topic, content_type, audience, keywords)

        return Response(
            message=f"Generated content brief:\n\n{brief}",
            break_loop=False,
        )
