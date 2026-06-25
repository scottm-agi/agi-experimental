### `secret_set`
Use this tool to securely store sensitive credentials like API keys, tokens, and passwords. Secrets are stored encrypted in the database and masked in all logs and chat history.

🔴 **MANDATORY — TOOL CALLS ONLY**: You MUST call `secret_set` as a direct tool call.
NEVER attempt to set secrets via `code_execution_tool`, subprocess, Python scripts, or bash.
These bypass the framework's secure storage and will **SILENTLY FAIL** (exit code 0, no error, no data stored).

**Supports two modes:**

#### Single-key mode (1-2 secrets):
- **key**: The secret name (e.g., `"GITHUB_TOKEN"`). Will be converted to uppercase.
- **value**: The secret value.
- **scope**: `"project"` (default) or `"global"`.

#### Batch mode (3+ secrets — PREFERRED):
- **secrets**: A JSON object of key-value pairs: `{"KEY1": "val1", "KEY2": "val2", ...}`
- **scope**: `"project"` (default) or `"global"`.
- All keys stored atomically. Each key is verified after storage.

**When to use:**
- Storing API keys, tokens, passwords
- Saving credentials that agents need for external service calls
- Setting project-specific or global authentication credentials

**Security Notes:**
- Values are masked as `§§secret(KEY_NAME)` in logs and chat
- Project-scoped secrets override global secrets with the same key
- Use `request_secret` if you need to ask the user for a secret

**Example - Single key:**
```json
{
  "tool_name": "secret_set",
  "tool_args": {
    "key": "RAILWAY_TOKEN",
    "value": "rlwy_abc123...",
    "scope": "project"
  }
}
```

**Example - Batch (preferred for 3+ secrets):**
```json
{
  "tool_name": "secret_set",
  "tool_args": {
    "secrets": {
      "GOOGLE_PLACES_API_KEY": "AIzaSy...",
      "OPENROUTER_API_KEY": "sk-or-...",
      "RESEND_API_KEY": "re_...",
      "STRIPE_SECRET_KEY": "sk_live_...",
      "PERPLEXITY_API_KEY": "pplx-..."
    },
    "scope": "project"
  }
}
```
