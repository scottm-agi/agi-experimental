---
name: secret-scanner
description: >
  Pre-commit secret scanning patterns and procedures. Agents must scan
  for hardcoded API keys, tokens, passwords, and connection strings
  before adding files to git. Uses the secret_scanner.py helper module
  for automated detection.
---

# Secret Scanner Skill

## Purpose
Prevent hardcoded secrets (API keys, tokens, passwords, database connection
strings) from being committed to version control. All secrets must be stored
in environment variables and `.env` files (which are `.gitignore`d).

## When to Scan
Agents MUST scan for secrets **before** running `git add` or `git commit`:
1. After writing or modifying any source file
2. Before any deployment
3. When reviewing code from external sources

## Secret Patterns to Detect

| Pattern | Example | Regex Hint |
|---------|---------|------------|
| OpenAI API Key | `sk-proj-abc123...` | `sk-(?:proj-)?[A-Za-z0-9_-]{20,}` |
| GitHub PAT | `ghp_xxxx...` | `gh[phosr]_[A-Za-z0-9_]{20,}` |
| GitLab PAT | `glpat-xxxx...` | `glpat-[A-Za-z0-9_-]{20,}` |
| AWS Secret Key | `wJalrXUtn...` | Long alphanumeric after `aws_secret` |
| Stripe Key | `sk_live_xxxx...` | `(?:sk\|pk)_(?:live\|test)_...` |
| Database URL | `postgresql://user:pass@...` | Connection string with credentials |
| Bearer Token | `Bearer eyJhbG...` | JWT in Authorization header |
| Password Assignment | `password = "value"` | Quoted string after `password =` |
| Private Key | `-----BEGIN PRIVATE KEY-----` | PEM key header |

## Using the Scanner

### Automated (Recommended)
Use the `secret_scanner.py` helper module:

```python
from python.helpers.secret_scanner import scan_file, scan_directory

# Scan a single file
matches = scan_file("src/config.ts")
if matches:
    for m in matches:
        print(f"⚠️  {m}")

# Scan entire project
matches = scan_directory("usr/projects/my-app/")
if matches:
    print(f"🚨 Found {len(matches)} potential secrets!")
    for m in matches:
        print(f"  {m.file_path}:{m.line_number} [{m.pattern_name}]")
```

### Manual (Shell)
```bash
# Quick grep for common patterns
grep -rn "sk-proj-\|ghp_\|sk_live_\|sk_test_" src/ --include="*.ts" --include="*.js" --include="*.py"
```

## Safe Files (Excluded from Scanning)
The following files are **excluded** because they are the correct
location for secrets (and should be in `.gitignore`):
- `.env`
- `.env.local`
- `.env.development`
- `.env.production`
- `.env.staging`
- `.env.test`
- `.env.example` / `.env.template` / `.env.sample`

## How to Fix Detected Secrets

### Step 1: Move to Environment Variable
```python
# ❌ BAD — hardcoded secret
api_key = "sk-proj-abc123def456..."

# ✅ GOOD — environment variable
import os
api_key = os.environ.get("OPENAI_API_KEY")
```

```typescript
// ❌ BAD
const apiKey = "sk-proj-abc123def456...";

// ✅ GOOD
const apiKey = process.env.OPENAI_API_KEY;
```

### Step 2: Add to `.env` File
```bash
# .env (this file is .gitignored)
OPENAI_API_KEY=sk-proj-abc123def456...
```

### Step 3: Ensure `.gitignore` Covers It
```gitignore
# Secrets
.env
.env.local
.env.*.local
```

### Step 4: Verify and Commit
```bash
# Re-scan to confirm no secrets remain
python -c "from python.helpers.secret_scanner import scan_directory; print(scan_directory('src/'))"

# If clean, proceed with git add
git add src/
git commit -m "feat: add configuration (secrets in env vars)"
```

## Integration with GitGuard
This skill works alongside the `safe-deploy` skill:
1. `secret-scanner` catches hardcoded secrets before commit
2. `safe-deploy` ensures correct CWD and branch before push
3. Together they form the **GitGuard** security layer
