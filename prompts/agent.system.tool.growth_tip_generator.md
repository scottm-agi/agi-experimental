# growth_tip_generator

Generate actionable marketing and growth tips for small and medium businesses using the RACE framework (Reach, Act, Convert, Engage).

## Recommended Usage

**For best results, delegate this tool to the `researcher` agent.** The researcher has built-in search and analysis capabilities that complement this tool's trend research. Pass the request to the researcher and ask it to use `growth_tip_generator`, then return the result.

Example delegation: "Use the growth_tip_generator tool to create a growth tip for [industry], then return the full tip with your analysis."

This tool:
1. Searches current marketing trends via DuckDuckGo
2. Applies RACE framework analysis
3. Checks for duplicate tips using Jaccard similarity against recent tips in memory
4. Returns a structured tip with an actionable prompt suggestion

## Parameters

- **industry** (optional): Target industry focus. Default: "general SMB"
- **max_retries** (optional): Max generation attempts. Default: 1

## Output

Returns a JSON tip with:
- `tip_text`: The actionable growth hack with metrics
- `prompt_suggestion`: A ready-to-use prompt for implementing the tip

## Example Usage

```json
{
    "tool_name": "growth_tip_generator",
    "tool_args": {
        "industry": "e-commerce"
    }
}
```

## Scheduling

To run this daily, use the scheduler tool:

```json
{
    "tool_name": "scheduler",
    "tool_args": {
        "action": "add",
        "name": "daily_growth_tip",
        "cron": "0 0 * * *",
        "message": "Generate a new growth tip using growth_tip_generator for our business"
    }
}
```
