### five_whys

Use this tool when you are **stuck, encountering errors, or have retried the same approach 2+ times without progress**. It performs a structured **5-Whys + First Principles** root cause analysis and generates an actionable pivot plan.

**WHEN TO USE THIS TOOL** (MANDATORY):
- Any tool call returns an error 2+ times in a row
- You realize you're repeating the same search/approach without new results
- You can't find the data the user requested using your current strategy
- An external service blocks, rate-limits, or refuses your request
- You've been working on the same sub-task for 5+ turns without progress

Args:
- **problem**: What went wrong or what you're stuck on (required)
- **context**: What you're ultimately trying to accomplish
- **attempts_so_far**: What approaches you've already tried and why they failed

The tool will analyze the root cause and suggest alternative tools/approaches. **You MUST execute the suggested action plan immediately after receiving the analysis.**

Usage:

~~~json
{
    "thoughts": [
        "I've searched 5 times for individual layoff names but Perplexity keeps hallucinating fake profiles.",
        "I need to break out of this loop and find a fundamentally different approach.",
        "I will use five_whys to analyze why my current strategy isn't working and pivot."
    ],
    "headline": "Analyzing research impasse with 5-Whys",
    "tool_name": "five_whys",
    "tool_args": {
        "problem": "Perplexity search returns fabricated names for Amazon layoff employees. Aggregator fidelity gate flags them as hallucinated.",
        "context": "User wants a list of 30,000 Amazon employees impacted by recent layoffs with names and LinkedIn profiles.",
        "attempts_so_far": "Used search_engine 5 times with different queries. All returned hallucinated tables. Have not tried scrape_url on news articles or code_execution_tool to parse WARN databases."
    }
}
~~~
