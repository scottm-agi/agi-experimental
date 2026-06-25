# examine

Use this tool to explicitly catalog and vet a data source (file, URL, documentation, tool output) before citing it or using it as a primary grounding point. This tool ensures that you have critically examined the source and established its relevance.

## Arguments
- `source`: (required) The path to the file, the URL, or the name of the tool/resource.
- `rationale`: (required) Why this source is relevant and what facts it verifies.
- `content_summary`: (optional) A brief summary of the key information extracted from the source.

## Usage Guidelines
1. **Fact Checking**: Before presenting a critical fact, use `examine` to anchor it to a source.
2. **Step Verification**: Use `examine` after viewing a file or searching the web to "lock in" the information.
3. **Traceability**: All items passed to `examine` should ideally appear in your final `## Sources` footer.

## Example
```json
{
  "source": "agix/python/agent.py",
  "rationale": "Verifying how system prompts are assembled in get_system_prompt.",
  "content_summary": "Prompts are built via extensions in python/extensions/system_prompt/"
}
```
