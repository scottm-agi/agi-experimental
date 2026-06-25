---
name: safe-deploy
description: >
  Enforces safe git and deployment practices for agents. Mandates CWD
  verification before any git command, staging-first deployment, and
  branch protection rules to prevent accidental pushes to production
  or the host repository.
---

# Safe Deploy Skill

## Purpose
Prevent agents from accidentally committing to the host repository root,
force-pushing to protected branches, or deploying directly to production
without staging verification.

## Rules (MANDATORY)

### 1. CWD Verification Before Any Git Command
Before executing **any** `git` command (`commit`, `push`, `add`, `checkout`, `branch`),
the agent MUST verify its current working directory:

```bash
# ALWAYS run this FIRST
pwd
```

**Verify the output**:
- ✅ Must be inside `usr/projects/<project-name>/` (or `/agix/usr/projects/...` in Docker)
- ❌ NEVER run git commands from `/agix/` (the framework root)
- ❌ NEVER run git commands from `/` or `/home/` or any non-project directory

If the working directory is wrong, `cd` to the correct project directory first.

### 2. Branch Protection
- **NEVER** `git push --force` to `main` or `master` branches
- **NEVER** `git checkout main` in the host repository
- **ALWAYS** work on feature branches: `git checkout -b feature/<name>`
- **ALWAYS** verify branch before pushing: `git branch --show-current`

### 3. Staging-First Deployment
For any deployment-related task:
1. Deploy to **staging** environment first
2. Verify the staging deployment works (health check, smoke test)
3. Only then deploy to **production** (if explicitly requested by user)

**NEVER** deploy directly to production without staging verification.

### 4. Commit Hygiene
- Write descriptive commit messages (not "fix" or "update")
- Review `git diff --staged` before committing
- Never commit `.env` files, `node_modules/`, or build artifacts

### 5. Push Safety Checklist
Before `git push`, verify ALL of these:
1. ✅ `pwd` shows you're in `usr/projects/<project>/`
2. ✅ `git branch --show-current` shows a feature branch (not main)
3. ✅ `git status` shows no unintended files staged
4. ✅ `git diff --staged` shows only expected changes
5. ✅ No secrets in staged files (use `secret_scanner.py` or review manually)

## Error Recovery
If you accidentally committed to the wrong branch:
```bash
# Undo the last commit (keep changes)
git reset --soft HEAD~1
# Create correct branch
git checkout -b feature/<correct-name>
# Re-commit
git add . && git commit -m "descriptive message"
```

If you accidentally pushed secrets:
1. **IMMEDIATELY** rotate the exposed credential
2. Remove the secret from code → use environment variables
3. Force-push to overwrite the commit (only on your feature branch)
4. Notify the team about the exposure

### 6. AGIX Container Git Deployment (CRITICAL)

> **IMPORTANT**: This section prevents the 17-GitGuard-block failure pattern observed in ITR-45.

Inside the AGIX container, **GitGuard blocks any `git` command that would traverse up to the host repository**. This means you CANNOT just `git init` in a random directory and push — GitGuard will block it. You CANNOT `cd /tmp && git add .` — GitGuard blocks this.

#### Preferred Method: Use the `git_publish` Tool

The `git_publish` tool handles all git deployment safely:
```
Use tool: git_publish
Arguments: {
    "repo_url": "https://github.com/<owner>/<repo>.git",
    "branch": "main",
    "commit_message": "Deploy <project-name>",
    "github_token": "<token from environment>"
}
```

This tool:
1. Clones the target repo into `<project>/tmp/push_staging/` (creates `.git` isolation)
2. Rsyncs project files (excluding node_modules, .next, .git, etc.)
3. Commits and pushes from the staging dir (GitGuard allows this because it has its own `.git`)
4. Cleans up the staging dir

#### Manual Fallback Procedure (only if git_publish is unavailable)

If you must use raw git commands:
```bash
# Step 1: ALWAYS clone first — this creates a .git directory
cd <project>/tmp/
git clone https://github.com/<owner>/<repo>.git push_staging

# Step 2: Copy files into the clone
rsync -av --exclude=node_modules --exclude=.next --exclude=.git \
    --exclude=tmp/ --exclude=__pycache__ \
    <project>/src/ push_staging/

# Step 3: Commit and push from the clone
cd push_staging
git add -A
git commit -m "Deploy from AGIX"
git push origin main
```

#### FORBIDDEN Patterns (GitGuard WILL block these)
- ❌ `mkdir /tmp/staging && cd /tmp/staging && git init && git add .` — No `.git` from clone
- ❌ `cd <project> && git add . && git push` — Traverses to host repo
- ❌ `git -C /tmp/newdir remote add origin <url>` — No `.git` directory exists
- ❌ Repeating any of the above after a GitGuard block — same mistake, same result

#### If GitGuard Blocks You
1. **STOP** — do not create another directory and repeat
2. **Read the error message** — it contains the fix instruction
3. Use the `git_publish` tool — it's designed specifically for this

