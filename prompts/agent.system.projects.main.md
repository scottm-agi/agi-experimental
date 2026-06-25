# Projects
- Users and agents can create and configure projects.
- Projects Have a work folder in `usr/projects/<name>/` and metadata in `usr/projects/<name>/.agix.proj/`.
- When a project is active, agents work within the project folder and MUST follow project instructions.
- **MANDATE**: When generating a new project, you MUST use `setup_project` and fill out the project definition (description, title, instructions) COMPLETELY. A detailed project model is required for high-quality coordination.
- Use `configure_project` to refine or complete a project's model if it is missing details.
- Use `validate_project` to ensure all metadata and required files are present.
- **Memory Bank**: Every project has a `memory-bank/` directory containing Markdown files. You MUST use the `maintain_memory_bank` tool to read and update these files. They are the source of truth for project state.