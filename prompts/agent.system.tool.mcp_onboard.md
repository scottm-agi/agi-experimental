# Tool: mcp_onboard

## Description
Adds or updates an MCP (Model Context Protocol) server configuration. This allows the system to discover and use new tools from local or remote MCP servers.

## Usage
Use this tool to add a new MCP server. You MUST provide a unique `name` for the server.

- **Local Server (stdio)**:
  `mcp_onboard(name="my-local-server", type="stdio", command="node", args=["path/to/server.js"])`
- **Remote Server (sse)**:
  `mcp_onboard(name="my-remote-server", type="sse", url="https://mcp.example.com/sse")`

## Arguments
- `name` (required): A unique, descriptive name for the MCP server (e.g., "google-chat", "github", "my-tools").
- `type`: "stdio" (for local scripts/packages) or "sse" (for remote web-based servers). Default: "stdio".
- `command`: (Required for stdio) The executable command (e.g., "node", "python", "npx").
- `args`: (Optional for stdio) A list of arguments for the command.
- `env`: (Optional for stdio) Dictionary of environment variables.
- `url`: (Required for sse) The SSE endpoint URL.
- `headers`: (Optional for sse) Dictionary of HTTP headers.
- `disabled`: Set to `True` to add the server without enabling it. Default: `False`.
- `description`: Optional text describing the tools this server provides.

## Guidelines
1. **Name matches context**: Choose a name that reflects the service (e.g., use "google-chat" if adding a chat integration).
2. **Verify Configuration**: Ensure paths for local commands are absolute or correctly reachable.
3. **SSE URLs**: Ensure remote URLs are valid and accessible from this environment.
4. **Persistence**: Onboarded servers are saved to `settings.json` and persist across restarts.
