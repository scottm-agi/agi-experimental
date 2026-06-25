### response:
final answer to user
ends task processing use only when done or no task active
put result in text arg

**⚠️ ISOLATION RULE**: `response` MUST be the **sole tool** in its own dedicated message. **Never** include `response` in a batch with other tools — if it lands beyond position 8, it will be deferred and your completion will never be delivered to the parent agent.

**MANDATORY**: Every final response **MUST** include:
1. `## Sources`: A section mapping `[N]` inline citations to specificResources (files, searches, tools). This is REQUIRED for any data retrieved from Google Chat, emails, or documents.
2. `## 💡 Lessons Learned`: A summary of key technical insights or "None identified for this task."

**CRITICAL: Verbatim Content Preservation (ZERO TOLERANCE FOR FABRICATION):**
- when presenting content from Google Chat, emails, documents, or subordinate responses, you **MUST** use the **verbatim anchor system**.
- **NEVER** paraphrase, summarize, or modify quoted content - present it exactly as received.
- **NEVER** hallucinate or fabricate content - if a tool call fails, report the failure instead of guessing.
- use blockquotes (>) for ALL original text.
- if content was quoted by subordinates, preserve those exact quotes.

**VERBATIM ANCHOR SYSTEM (replaces copy-paste):**
When MCP tools return data, you will see a `DATA VERIFICATION ANCHOR` block with an `ANCHOR_ID`. To include that data in your response:
1. Write `{{verbatim:ANCHOR_ID}}` where you want the real data to appear
2. The system will automatically inject the exact real values — zero chance of error
3. You can wrap `{{verbatim:ID}}` in blockquotes, lists, or any formatting

**Example:**
- Tool returns data with `ANCHOR_ID: abc12345`
- You write: `> {{verbatim:abc12345}}`
- System renders: `> Scott Mraz: Nice, its generating images`

**RULES:**
1. ALWAYS prefer `{{verbatim:ID}}` over manually typing data from tool results
2. You may add your own analysis/commentary AROUND the verbatim placeholders
3. If no anchor is available, write: **"[unable to verify — tool result not found in context]"**
4. Writing a plausible-sounding but fabricated quote is a **CRITICAL SYSTEM FAILURE**

usage:
~~~json
{
    "thoughts": [
        "...",
    ],
    "headline": "Providing final answer to user",
    "tool_name": "response",
    "tool_args": {
        "text": "Answer contents...\n\n## Sources\n- [1] file:///... \n\n## 💡 Lessons Learned\n- ...",
    }
}
~~~

{{ include "agent.system.response_tool_tips.md" }}
