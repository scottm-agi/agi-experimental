## configure_webhook
Configure webhook settings for GitHub, Forgejo, or Gitea integrations.

This tool allows you to:
- Set webhook secrets for signature verification
- Enable or disable webhook processing
- Manage allowed repositories
- Generate new random webhook secrets
- Check current configuration status

### Actions:
- **set_secret**: Store a webhook secret for a provider
- **get_status**: Get current webhook configuration and status
- **enable**: Enable webhook processing
- **disable**: Disable webhook processing (rejects all events)
- **add_repo**: Add repositories to the allowed list
- **remove_repo**: Remove repositories from the allowed list
- **generate_secret**: Generate a new random secret and store it

### Parameters:
- **action** (required): The action to perform
- **provider**: "github" (default) or "forgejo" 
- **secret**: The secret value (for set_secret)
- **allowed_repos**: List of repos like ["owner/repo", "org/*"] (for add_repo/remove_repo)

### Examples:
~~~json
// Get current status
{"action": "get_status"}

// Set GitHub webhook secret
{"action": "set_secret", "provider": "github", "secret": "my-secret-value"}

// Set Forgejo webhook secret
{"action": "set_secret", "provider": "forgejo", "secret": "my-secret-value"}

// Generate a new random secret
{"action": "generate_secret", "provider": "github"}

// Enable webhooks
{"action": "enable"}

// Disable webhooks  
{"action": "disable"}

// Add repos to allowed list
{"action": "add_repo", "allowed_repos": ["your-org/agi-experimental-test", "your-org/*"]}
~~~

### Notes:
- Secrets are stored in the global secrets manager
- The webhook endpoint is `/webhook/github` (handles all providers)
- Empty `allowed_repos` means all repositories are allowed
