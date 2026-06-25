# Tool: settings_get

## Description
Retrieves global configuration settings from `settings.json`. These settings include model providers, model names, UI preferences, and system-wide defaults.

## Usage
Use this tool when you need to understand current system configurations or model settings.

- To retrieve all settings: `settings_get()`
- To retrieve a specific setting: `settings_get(key="chat_model_provider")`
- To retrieve multiple specific settings: `settings_get(keys=["chat_model_provider", "chat_model_name"])`

## Security
Sensitive fields such as passwords (`auth_password`, `root_password`, etc.) are automatically masked with `***` by the tool before being returned.

## Guidelines
- Always prefer this tool over direct file reads or environment variable checks for global settings.
- If you need project-specific parameters, use `parameter_get` instead.
- If you need sensitive credentials that are not in `settings.json`, use `secret_get`.
