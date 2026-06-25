"""
Growth Tip Generator Tool (Issue #811)

Ported from old agix: marketing_tips_tasks.py (532 LOC).
Generates daily marketing/growth tips using RACE framework analysis
with Jaccard similarity dedup against recent tips stored in memory.

Can be used on-demand by agents or scheduled daily via the scheduler tool.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone

from python.helpers.tool import Tool, Response
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger("tool.growth_tip_generator")

# Stop words for keyword extraction (Jaccard dedup)
STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "with", "by", "about", "as", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "can", "could",
    "will", "would", "should", "may", "might", "must", "that", "this",
    "these", "those", "it", "its", "of", "from", "your", "their", "how",
    "what", "when", "use", "using", "make", "help",
})

JACCARD_THRESHOLD = 0.4
MIN_WORD_LENGTH = 4


def jaccard_similarity(text_a: str, text_b: str) -> float:
    """Calculate Jaccard similarity between two texts using significant words."""
    words_a = _extract_keywords(text_a)
    words_b = _extract_keywords(text_b)

    if not words_a or not words_b:
        return 0.0

    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union) if union else 0.0


def _extract_keywords(text: str) -> set[str]:
    """Extract significant keywords from text, filtering stop words."""
    words = re.findall(r"\b\w+\b", text.lower())
    return {w for w in words if w not in STOP_WORDS and len(w) >= MIN_WORD_LENGTH}


def extract_tip_json(raw: str) -> dict | None:
    """Extract and validate tip JSON from LLM response text.

    Handles common LLM response formats: raw JSON, ```json blocks,
    prefixed text, and trailing notes.
    """
    content = raw.strip()

    # Strip markdown code blocks
    if "```json" in content:
        parts = content.split("```json", 1)
        if len(parts) > 1:
            content = parts[1].split("```")[0].strip()
    elif "```" in content:
        parts = content.split("```")
        for part in parts:
            if "{" in part and "}" in part:
                content = part.strip()
                break

    # Strip trailing sections (References:, Notes:, etc.)
    for marker in ("References:", "Sources:", "Notes:", "Additional"):
        if marker in content:
            content = content.split(marker)[0].strip()

    # Extract outermost JSON object
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        return None

    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    # Validate required fields
    if "tip_text" not in data or "prompt_suggestion" not in data:
        return None

    # Ensure source_url exists (optional field)
    data.setdefault("source_url", "")
    return data


def build_race_prompt(recent_tip_texts: list[str]) -> str:
    """Build the RACE framework research prompt with recent tips for dedup."""
    if recent_tip_texts:
        avoidance = "\n".join(f"- {t}" for t in recent_tip_texts)
        keywords = set()
        for t in recent_tip_texts:
            keywords.update(_extract_keywords(t))
        keyword_csv = ", ".join(sorted(keywords)[:50])
        dedup_section = (
            f"DO NOT select any of the following topics covered recently:\n"
            f"{avoidance}\n\nKey topics to avoid: {keyword_csv}"
        )
    else:
        dedup_section = "No recent tips to avoid"

    return f"""Search across Reddit, news sites, and blogs for the latest growth hacks and \
marketing strategies specifically for small and medium businesses. Focus on actionable, \
proven tactics that have shown measurable results.

Research Process:
1. Search trending topics on Reddit (r/marketing, r/entrepreneur, r/growthmarketing)
2. Check recent marketing blogs and news sites (published THIS WEEK)
3. Look for case studies and success stories from SMBs
4. Analyze using the RACE framework:
   - Reach: How does it attract new audiences?
   - Act: How does it engage visitors?
   - Convert: How does it drive conversions?
   - Engage: How does it build loyalty?

IMPORTANT RESEARCH REQUIREMENTS:
1. Find at least 10 different growth hack ideas before selecting the best one
2. Ensure the growth hack is from THIS WEEK (last 7 days) if possible
3. {dedup_section}
4. Look for something truly innovative and trending right now
5. Prioritize tactics with measurable results or case studies

Create a response in this exact JSON format:

{{
    "tip_text": "A concise, actionable growth hack proven effective for SMBs. Include metrics.",
    "prompt_suggestion": "Help me implement [growth hack] for my business. Provide a plan: 1) Reach [channels], 2) Engage [tactics], 3) Convert [methods], 4) Retain [strategies]. Include metrics to track.",
    "source_url": ""
}}

IMPORTANT: Response must be ONLY the JSON object. No other text."""


class GrowthTipGenerator(Tool):
    """Generate daily marketing/growth tips using RACE framework analysis.

    Ported from old agix marketing_tips_tasks.py.
    Uses agent's search capabilities + memory for persistence and dedup.
    """

    async def execute(self, **kwargs) -> Response:
        industry = self.args.get("industry", "general SMB")
        max_retries = int(self.args.get("max_retries", 1))

        try:
            # 1. Load recent tips from memory for dedup
            recent_tips = await self._load_recent_tips()

            # 2. Build the RACE framework prompt
            prompt = build_race_prompt(recent_tips)

            # 3. Use DuckDuckGo search to find trending growth hacks
            from python.helpers.duckduckgo_search import search

            search_query = f"growth hacks marketing strategies SMB {industry} {datetime.now().strftime('%B %Y')}"
            search_results = search(search_query, results=5)

            # Enrich prompt with search results
            if search_results:
                sources = "\n".join(f"- {r[:200]}" for r in search_results[:5])
                prompt += f"\n\nRecent search results for context:\n{sources}"

            # 4. Have the agent's LLM generate the tip
            from python.helpers import tokens

            messages = [SystemMessage(content="You are a marketing research analyst. Respond only with valid JSON."), HumanMessage(content=prompt)]
            llm_response, _reasoning, _model, _provider = await self.agent.call_chat_model(messages=messages,
                )

            if not llm_response:
                return Response(
                    message="Error: LLM returned empty response for tip generation.",
                    break_loop=False,
                )

            # 5. Parse the response
            tip_data = extract_tip_json(llm_response)
            if not tip_data:
                return Response(
                    message=f"Error: Could not parse tip JSON from LLM response.\nRaw: {llm_response[:500]}",
                    break_loop=False,
                )

            # 6. Jaccard dedup check
            for existing_tip in recent_tips:
                sim = jaccard_similarity(tip_data["tip_text"], existing_tip)
                if sim > JACCARD_THRESHOLD:
                    return Response(
                        message=f"⚠️ Duplicate tip detected (similarity: {sim:.0%}). "
                        f"Try again for a different topic.\nGenerated: {tip_data['tip_text'][:100]}...\n"
                        f"Similar to: {existing_tip[:100]}...",
                        break_loop=False,
                    )

            # 7. Save to memory
            await self._save_tip(tip_data)

            # 8. Return the tip
            tip_text = tip_data["tip_text"]
            prompt_suggestion = tip_data["prompt_suggestion"]

            return Response(
                message=(
                    f"✅ **New Growth Tip Generated**\n\n"
                    f"**Tip:** {tip_text}\n\n"
                    f"**Try this prompt:** {prompt_suggestion}\n\n"
                    f"*Saved to memory for dedup tracking.*"
                ),
                break_loop=False,
            )

        except Exception as e:
            logger.error(f"Growth tip generation failed: {e}", exc_info=True)
            return Response(
                message=f"Error generating growth tip: {str(e)}", break_loop=False
            )

    async def _load_recent_tips(self) -> list[str]:
        """Load recent tip texts from agent memory for dedup."""
        try:
            from python.helpers import memory

            results = memory.search(
                self.agent,
                query="growth_tip marketing",
                count=20,
                threshold=0.3,
            )

            tips = []
            for result in results:
                if isinstance(result, dict):
                    text = result.get("metadata", {}).get("tip_text", "")
                elif hasattr(result, "page_content"):
                    text = result.page_content
                else:
                    text = str(result)
                if text and len(text) > 20:
                    tips.append(text[:200])
            return tips
        except Exception as e:
            logger.warning(f"Could not load recent tips from memory: {e}")
            return []

    async def _save_tip(self, tip_data: dict):
        """Persist tip to agent memory."""
        try:
            from python.helpers import memory

            content = (
                f"Growth Tip: {tip_data['tip_text']}\n"
                f"Prompt: {tip_data['prompt_suggestion']}"
            )

            memory.save(
                self.agent,
                text=content,
                metadata={
                    "type": "growth_tip",
                    "tip_text": tip_data["tip_text"],
                    "prompt_suggestion": tip_data["prompt_suggestion"],
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.info("Growth tip saved to memory")
        except Exception as e:
            logger.warning(f"Could not save tip to memory: {e}")
