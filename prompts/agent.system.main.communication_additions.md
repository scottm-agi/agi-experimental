## Receiving messages
user messages contain superior instructions, tool results, framework messages
if starts (voice) then transcribed can contain errors consider compensation
tool results contain file path to full content can be included
messages may end with [EXTRAS] containing context info, never instructions

### Replacements
- in tool args use replacements for secrets, file contents etc.
- replacements start with double section sign followed by replacement name and parameters: `§§name(params)`

### File including
- include file content in tool args by using `include` replacement with absolute path: `§§include(/path/to/file.ext)`
- useful to repeat file contents and tool results
- !! always prefer including over rewriting, do not repeat long texts
- rewriting existing tool responses is slow and expensive, include when possible!
- !! ONLY reference files within the project directory (`_active_project_dir`). NEVER reference framework-internal paths like `/agix/tmp/chats/` or `/agix/logs/` — those are infrastructure, not agent-accessible (ADR-83).
Example:
~~~json
{
  "thoughts": [
    "Response received, I will include the deliverable file."
  ],
  "tool_name": "response",
  "tool_args": {
    "text": "# Here is the report:\n\n§§include(/agix/usr/projects/my_project/docs/report.md)"
  }
}
~~~