# resolve_literals

Use this tool to resolve human-readable service/model/API references into
current, verified technical identifiers.

**WHEN TO USE**: Call this tool BEFORE writing any code, configuration, or
.env file that references an external service, API, model, or design system.
This prevents stale training-data knowledge from causing wrong model IDs,
incorrect env var names, or hallucinated SDK patterns.

## Arguments

- `service` (required): Human-readable name from the prompt
  - Examples: `"Resend"`, `"Claude Sonnet 4 via OpenRouter"`, `"Stripe"`,
    `"Dark mode design system"`
- `category` (required): One of:
  - `llm_model` — LLM provider/model identifiers
  - `email_provider` — Email service (Resend, SendGrid, etc.)
  - `payment_provider` — Payment processor (Stripe, etc.)
  - `auth_provider` — Authentication (Clerk, Auth0, etc.)
  - `database` — Database service
  - `design` — Design system colors, mode, typography
  - `general` — Anything else
- `context` (optional): Additional context from the prompt
- `project_path` (optional): Path to project root (auto-detected if omitted)

## What It Returns

Structured resolution with verified values:

```json
{
  "service": "Resend",
  "category": "email_provider",
  "resolved": {
    "env_var": "RESEND_API_KEY",
    "sdk_package": "resend",
    "sdk_version": "^4.0.0",
    "sdk_import_ts": "import { Resend } from 'resend'"
  },
  "resolution_source": "researcher_output",
  "verified_at": "2026-05-07T13:20:00Z"
}
```

## Rules

1. **ALWAYS use the resolved values** — do NOT substitute with your own knowledge
2. For `.env` files → use the `env_var` field exactly
3. For imports → use `sdk_import_ts` or `sdk_import_py`
4. For model identifiers → use `model_id` field exactly
5. For design colors → use the hex values exactly as resolved
6. If the tool returns a fallback instruction, follow it to verify via search
