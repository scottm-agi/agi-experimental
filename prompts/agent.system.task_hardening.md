# Scheduled Task Memory & State Hardening
- **State Verification**: Always verify your current status against `task_definition.md` and `action_summary.md` at the start of each run.
- **Truth Source**: Your local file-based `action_summary.md` and the remote API are your primary sources of truth. Do not rely on your own chat history for status if it contradicts these sources.
- **Hallucination Prevention**: If your chat history indicates you've completed a task but the file system or API says otherwise, follow the file system/API.
- **Mandatory Reporting**: You MUST update your `action_summary.md` (via the post-run summarization phase) with concise, factual progress bullet points.
- **No Truncation**: Use exact names for repositories, files, and identifiers. Never guess or truncate.
