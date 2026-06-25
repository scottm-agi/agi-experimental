# Tool: oauth_configure

## Description
Configures OAuth 2.0 profiles for a specific vendor. This tool automatically creates both `WEB` and `DESKTOP` profiles for the integration.

## Parameters
- `vendor_name`: (required) The name of the vendor (e.g., "Zoho"). Must exist in `conf/oauth_providers.yaml`.
- `client_id`: (required) The OAuth Client ID provided by the vendor.
- `client_secret`: (required) The OAuth Client Secret provided by the vendor.
- `custom_scopes`: (optional) A list of specific scopes to use, overriding the defaults in the registry.

## Instructions for Agent
1. Use this tool after the user has provided their `client_id` and `client_secret`.
2. Ensure the vendor exists in the registry first (use `oauth_search` if needed).
3. Inform the user that both Web and Desktop profiles have been created and are ready for authorization in the UI.

## Example Usage
`oauth_configure(vendor_name="Zoho", client_id="...", client_secret="...")`