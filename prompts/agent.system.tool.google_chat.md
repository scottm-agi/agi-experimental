# Google Chat MCP — Tool Selection Guide

When working with Google Chat, choose the most efficient tool for the task:

| Task | Tool | Notes |
|------|------|-------|
| Read messages from a **known space ID** | `google_chat_list_messages(space_id=...)` | **Use this first.** Directly pass the space ID. |
| Find which spaces exist | `google_chat_list_spaces()` | Only if you don't know the space ID yet. |
| Search messages with time/thread filters | `google_chat_search_messages(space_id=..., query_filter=...)` | For filtered queries within a single space. |
| Search across ALL spaces at once | `google_chat_search_all_spaces(output_dir=..., since_hours=...)` | For cross-space discovery. Writes markdown files per space. |
| Send a message | `google_chat_send_message(space_id=..., text=...)` | |
| Reply to a thread | `google_chat_create_thread_reply(space_id=..., thread_id=..., text=...)` | |
| Check auth status | `google_chat_get_connection_status()` | Quick health check. |

## Rules

1. **If you have a space ID, NEVER call `google_chat_list_spaces` first.** Go directly to `google_chat_list_messages`.
2. Space IDs look like `AAQAUHBrvGs` or `spaces/AAQAUHBrvGs` — both formats work.
3. If a tool returns a permission error (403), report it and move on. Do NOT retry.
4. If you cannot find messages after one attempt, report what you tried. Do NOT loop or retry the same call.
5. **Present message content EXACTLY as returned.** Never paraphrase or fabricate messages.
6. For "pull recent messages" requests, use `google_chat_list_messages` with default sort (newest first).
7. For "search for messages from [person]" requests, use `google_chat_search_all_spaces` with `sender_name`.
