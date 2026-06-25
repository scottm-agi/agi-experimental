## setup_project
Set up a new project with mise-en-place (everything in its place).

**MANDATORY**: This is the ONLY way to create new projects. Do NOT use code_execution to create project directories.

This tool creates a complete project environment with:
- Project directory at `usr/projects/<name>/` (relative to Alex, NOT at OS root)
- Git repository initialization
- MISE configuration (.mise.toml) for environment management
- Framework-appropriate .gitignore
- README.md with project documentation
- AGIX project metadata (.agix.proj)

**IMPORTANT PATH NOTE**: 
- Projects are created at `usr/projects/<name>/` within Alex
- This is NOT `/projects/` at the OS root
- This is NOT `/tmp/` or any temporary directory
- The tool handles the correct path automatically

### Parameters:
- **name** (required): Project name (folder name, lowercase with hyphens)
- **description** (required): **MANDATORY**. Comprehensive project description.
- **title** (optional): Human-readable project title. Defaults to sanitized name.
- **instructions** (optional): High-level project-specific instructions for the agent.
- **framework** (optional): Framework type - python, nodejs, rust, go, ruby, java, fullstack, generic.
- **color** (optional): UI color associated with the project (e.g., "#4A90E2").
- **memory** (optional): 'own' (isolated project memory) or 'global' (shared). Default: 'own'.
- **file_structure** (optional): Nested settings for how the file structure is presented to agents.
  - **enabled**: bool
  - **max_depth**: int (default 5)
  - **max_lines**: int (default 250)
- **auto_install** (optional): Whether to auto-install MISE tools (default: false)

### When to use:
- When starting a new coding project
- When the user asks to create an app, website, API, or service
- Before writing any code for a new project
- When you detect a new project is needed

### Example:
~~~yaml
setup_project:
  name: my-awesome-app
  title: "My Awesome App"
  description: "A comprehensive web application for real-time task management with collaboration features."
  instructions: "Focus on clean architecture and high performance. Use Tailwind CSS for styling."
  framework: nodejs
  color: "#4A90E2"
  memory: own
  file_structure:
    enabled: true
    max_depth: 7
    max_lines: 300
  auto_install: false
~~~

### Output:
Returns a detailed report of all setup steps completed, including:
- Directory creation status
- Git initialization status
- MISE configuration status
- Files created
- Next steps for the user

### Best Practices:
1. **Always use this tool first** when starting a new project.
2. **Provide a FULL definition**: Fill out as many fields as possible. A complete model leads to better agent behavior.
3. **Description is CRITICAL**: It is used by the supervisor and other agents to understand context.
4. Use descriptive project names (lowercase, hyphens for spaces).
5. After setup, navigate to the project directory before coding.

### MISE Integration:
The tool creates a .mise.toml file that:
- Specifies language/runtime versions (e.g., Python 3.11, Node 20)
- Defines common tasks (install, test, dev, build)
- Sets environment variables
- Ensures consistent development environment

### Project Structure Created:
```
usr/projects/<name>/
├── .mise.toml          # MISE configuration
├── .git/               # Git repository
├── .gitignore          # Framework-appropriate ignores
├── .agix.proj/          # AGIX project metadata
│   ├── project.json
│   ├── instructions/
│   └── knowledge/
└── README.md           # Project documentation
```

### FORBIDDEN - Do NOT Do This:
```python
# WRONG - Never create project directories manually
import os
os.makedirs("/tmp/my-project", exist_ok=True)  # WRONG
os.makedirs("/projects/my-project", exist_ok=True)  # WRONG
```

```bash
# WRONG - Never use mkdir for project directories
mkdir -p /tmp/my-project  # WRONG
mkdir -p /projects/my-project  # WRONG
