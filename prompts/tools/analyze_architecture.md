# Architecture Analysis Tool
Provides a high-level architectural overview of the repository.

## Purpose
Use this tool to understand the overall structure of a repository, identify entry points, and see how major components interact. It automatically generates a Mermaid diagram representing the system flow.

## Arguments
- `action`: (optional) The type of analysis to perform. Default is "full_analysis".

## Usage Examples
```json
{
  "tool": "analyze_architecture",
  "args": {}
}
```

## Response
The tool returns a Markdown report with:
1. **System Diagram**: A Mermaid diagram showing entry points and module dependencies.
2. **Entry Points**: A list of identified main execution scripts.
3. **Core Components**: A summary of top-level directories and their roles.
