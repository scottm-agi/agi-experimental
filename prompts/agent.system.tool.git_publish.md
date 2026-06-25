### git_publish:
Safely publish project code to a remote Git repository using the **staging directory pattern**.
This tool handles the entire clone → sync → commit → push workflow atomically, bypassing GitGuard
restrictions by operating inside an isolated `tmp/push_staging/` directory with its own `.git`.

**CRITICAL**: NEVER run raw `git init`, `git remote add`, `git commit`, or `git push` commands
directly inside the project directory. GitGuard WILL block them and you will waste 15+ minutes
in a failure loop. Use this tool instead — it handles everything safely.

**How it works:**
1. Creates `<project_dir>/tmp/push_staging/` directory
2. Clones the target repository into staging (creates `.git` isolation)
3. Syncs project files via rsync (excluding node_modules, .git, .next, tmp/, etc.)
4. Stages all changes, commits with your message, and pushes to the target branch
5. Cleans up the staging directory automatically

**Arguments:**
- `repo_url` (required): Full Git URL of the target repository (e.g., `https://github.com/user/repo.git`)
- `project_dir` (required): Absolute path to the project directory to publish
- `commit_message` (optional): Commit message. Default: `"Deploy project"`
- `branch` (optional): Target branch name. Default: `"main"`

**Example — Push to GitHub:**
```json
{
    "tool_name": "git_publish",
    "tool_args": {
        "repo_url": "https://github.com/your-bot-username/my-app.git",
        "project_dir": "/agix/usr/projects/my_app_12345",
        "commit_message": "Deploy: My App Fullstack",
        "branch": "main"
    }
}
```

**When to use:**
- After completing a project build, to push the code to a client's GitHub repository
- For any deployment that requires pushing code to a Git remote
- Instead of writing custom git push scripts or running raw git commands

**What it excludes from sync:**
- `node_modules/` — package dependencies (rebuild from package.json)
- `.git/` — prevents corrupting the staging repo's git state
- `.next/` — Next.js build artifacts
- `.agix.proj/` — AGIX internal metadata
- `tmp/` — temporary files including the staging dir itself
- `__pycache__/` — Python bytecode
- `.env`, `.env.local` — environment secrets (NEVER push these)
- `dist/`, `build/` — build output artifacts

**Authentication**: The tool uses whatever git credentials are configured in the environment
(SSH keys, credential helpers, `gh` CLI auth). Ensure credentials are set up before calling.
