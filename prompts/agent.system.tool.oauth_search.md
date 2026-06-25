# Tool: oauth_search

## Description
Searches for OAuth 2.0 configuration details for a specific vendor using Perplexity. This tool identifies authorization endpoints, token endpoints, and required scopes.

## Parameters
- `vendor_name`: (required) The name of the service/vendor to research (e.g., "Zoho", "Salesforce").

## Instructions for Agent
1. Use this tool when a user asks to integrate with a new service via OAuth.
2. The tool will return the necessary URLs and scopes.
3. After receiving the results, present them to the user and ask if they would like to proceed with configuring the profiles.
4. If the vendor is not in `conf/oauth_providers.yaml`, the agent should offer to add it.

## Example Usage
`oauth_search(vendor_name="Zoho")`