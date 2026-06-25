## configure_project
Update project metadata and configuration.

Use this tool to refine the definition of an existing project. A complete project model is essential for supervisor coordination and agent context.

### Parameters:
- **name** (required): Project folder name.
- **title** (optional): Human-readable project title.
- **description** (optional): Comprehensive project description.
- **instructions** (optional): High-level project-specific instructions.
- **color** (optional): UI color for the project.
- **memory** (optional): 'own' or 'global'.
- **file_structure** (optional): Nested settings (enabled, max_depth, max_lines, etc.).

### When to use:
- To update a project's description or instructions.
- To change how the file structure is presented to you (adjusting max_depth or max_lines).
- When you realize a project definition is incomplete (use `validate_project` to check).

### Example:
~~~yaml
configure_project:
  name: my-project
  description: "Updated description to include recent architectural changes and new core features."
  file_structure:
    max_depth: 8
    max_lines: 500
~~~
