# Tool: settings_set

## Description
Updates global configuration settings in `settings.json`. Use this to change model configurations, UI behavior, or system defaults.

## Usage
Use this tool to update non-sensitive system-wide configurations.

- To update a single setting: `settings_set(settings={"chat_model_name": "gpt-4o"})`
- To update multiple settings: `settings_set(settings={"chat_model_name": "gpt-4o", "chat_model_provider": "openai"})`

## Security and Constraints
- **NO SECRETS**: Secrets (API keys, passwords, tokens) should **NOT** be stored using this tool. 
- **PROTECTED FIELDS**: The tool will reject updates to sensitive fields like `auth_password`, `root_password`, `rfc_password`, and `mcp_server_token`. 
- **CREDENTIALS**: For sensitive credentials, use the `secret_set` tool.
- **PROJECT PARAMETERS**: For project-specific configuration, use `parameter_set`.

## Guidelines
- Only use this tool when a system-wide configuration change is necessary.
- Ensure the keys provided exist or are valid configuration options for the system.
