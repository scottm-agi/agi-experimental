from __future__ import annotations
from python.helpers.tool import Tool, Response
from python.helpers.print_style import PrintStyle


def build_analysis_prompt(problem: str, context: str = "", attempts_so_far: str = "") -> str:
    """Build a structured 5-Whys + First Principles analysis prompt.
    
    This is a pure function (no agent dependency) so it can be tested independently.
    Returns structured guidance that the agent reasons through in its next turn.
    """
    return f"""## 🔍 5-Whys + First Principles Analysis

### The Problem
{problem}

### Context
{context or 'No additional context provided.'}

### What Has Been Tried So Far
{attempts_so_far or 'No previous attempts documented.'}

---

### Step 1: 5-Whys Root Cause Drill-Down
Work through these in your thoughts. Don't stop at symptoms — find the DESIGN flaw:
- **Why #1**: Why did this fail? What was the immediate cause?
- **Why #2**: Why did THAT happen? What enabled the failure?
- **Why #3**: Why was that the case? What assumption was wrong?
- **Why #4**: Why did you make that assumption? What constraint drove it?
- **Why #5**: Why haven't you worked around that constraint? What alternatives exist?

### Step 2: First Principles Decomposition
Strip away ALL assumptions. Answer these:
- **Fundamental Goal**: What is the OUTCOME you need? (not the method — the result)
- **Hard Constraints**: What physical/logical constraints CANNOT be changed?
- **Soft Assumptions**: What are you assuming that CAN be challenged?
- **Smallest Unit of Progress**: What is the simplest next action that moves you forward?

### Step 3: Alternative Tool Approaches
Consider these concrete alternatives — pick the BEST one for your situation:

| Tool | Use When | Example |
|------|----------|---------|
| `scrape_url` (Crawl4AI) | Need real data from a specific webpage | Scrape layoffs.fyi, news articles, WARN databases |
| `tavily-mcp.tavily_search` | Need fresh search results | Search for current events, news, data |
| `tavily-mcp.tavily_extract` | Need content from specific URLs | Extract structured data from known pages |
| `tavily-mcp.tavily_crawl` | Need to discover pages across a site | Crawl a domain for relevant subpages |
| `code_execution_tool` | Need to fetch/parse data programmatically | Python scripts to curl APIs, parse CSV/HTML, fetch RSS |
| `browser` | Need interactive JS sites or login-required pages | Navigate SPAs, fill forms, take screenshots |
| `search_engine` | Need web search with fallback chain | General queries (NOT for PII/names) |
| `perplexity_ask` | Need knowledge/analysis (NOT PII) | "What companies had layoffs in Q1 2026?" |

### Step 4: Action Plan
**⚡ You MUST pick ONE alternative approach and execute it IMMEDIATELY in your next tool call.**
Do NOT go back to the approach that failed. The definition of insanity is trying the same thing and expecting different results.

Your next tool call should be:
1. **Tool**: [pick from the table above]
2. **Arguments**: [be specific — exact URL, query, or code]
3. **Expected outcome**: [what data you expect to get]
4. **Fallback**: [if this also fails, what's plan C?]
"""


class FiveWhys(Tool):
    """
    Universal 5-Whys + First Principles error resolution tool.
    
    Agents invoke this when they hit errors, can't make forward progress,
    or have retried the same approach 2+ times without success.
    
    Returns a structured analysis framework that guides the agent through
    root cause analysis and generates an actionable pivot plan.
    
    Issue #1130: https://your-forgejo-instance.example.com/your-org/agi-experimental/issues/1130
    """
    
    async def execute(self, problem: str, context: str = "", attempts_so_far: str = "", **kwargs):
        if not problem:
            return Response(
                message="Please describe the problem or error you're experiencing.",
                break_loop=False
            )
        
        PrintStyle.standard(f"[5-WHYS] Analyzing: {problem[:100]}...")
        
        # Build the structured analysis — the agent reasons through this itself
        analysis = build_analysis_prompt(problem, context, attempts_so_far)
        
        output = analysis
        output += "\n---\n"
        output += "**⚡ ACTION REQUIRED**: Work through the 5-Whys in your thoughts, then execute your action plan IMMEDIATELY. "
        output += "Do NOT call `five_whys` again — ACT on the analysis above."
        
        PrintStyle.standard(f"[5-WHYS] Analysis framework delivered. Agent must pivot now.")
        # FIX-6: Structured signal to prevent tool_failure_tracker false-positives.
        # five_whys output inherently discusses errors/failures (that's its purpose).
        # Without this, the tracker's Layer 3 regex matches "failed", "error:", etc.
        # in the analysis text and falsely flags the tool as broken.
        # Layer 1 (structured signal) is checked BEFORE Layer 3 (regex), so this
        # short-circuits the false detection cleanly.
        return Response(message=output, break_loop=False, additional={"success": True})
