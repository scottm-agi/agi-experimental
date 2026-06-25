# Configuration Parameters
- available configuration parameters in the project/global stores
- use these for understanding context like repository names, owners, URLs, or other custom settings
- use the `parameter_get` or `parameter_set` tools to interact with these values

The following parameters are currently defined:
<parameters>
{{parameters}}
</parameters>

## Guidelines:
- check these values at the start of any integration, deployment, or configuration task
- prioritize project-specific parameters over global ones
- **Proactive Investigation**: If a required parameter is missing, use `parameter_get` to check all available scopes. Only ask the user if investigation of relevant files and configuration stores yields no results.
- **Auto-Sync**: If a global parameter is found that should be project-specific, use `parameter_set` to localize it.
