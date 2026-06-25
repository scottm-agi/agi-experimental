## list_projects
List all existing projects in the workspace. **Use this BEFORE setup_project** to avoid creating duplicates.

This tool returns all projects with their names, titles, descriptions, git remotes, and timestamps.

### Parameters:
- **search** (optional): Filter projects by name, title, or description substring match.

### When to use:
- **ALWAYS before creating a new project** — check if one already exists
- When the user references a project by name/topic and you need to find it
- When you need to discover what projects exist in the workspace
- When switching between projects

### Example:
~~~yaml
list_projects:
  search: my-project
~~~

### Output:
Returns a formatted list of all matching projects with:
- Project name (folder name)
- Title and description
- Git remote URL (if configured)
- Creation and last-modified dates

### MANDATORY WORKFLOW:
1. **Call `list_projects`** to see existing projects
2. **If a matching project exists** → activate it, don't create a new one
3. **If no match** → use `setup_project` to create a new project
