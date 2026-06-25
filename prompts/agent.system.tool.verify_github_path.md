### verify_github_path:
Verify and sanitize GitHub file paths before using them in issue bodies, comments, or markdown references.
This tool prevents **path hallucinations** — a common failure where agents construct incorrect file paths
(e.g., doubled segments like `docs/main/docs/mockups/file.png` instead of `docs/mockups/file.png`).

**MANDATORY USAGE**: You MUST call this tool before embedding ANY GitHub file path or raw URL in:
- Issue bodies (especially during decomposition)
- Issue comments
- Markdown image references (`![img](url)`)

The tool will:
1. **Sanitize** the path by fixing known hallucination patterns (doubled segments, wrong prefixes)
2. **Verify** the file exists in the repository via the GitHub Contents API
3. **Return** the corrected path and verified raw URL

**Arguments:**
- `path`: The proposed file path to verify (e.g., `docs/mockups/dashboard-abc123.png`). Mutually exclusive with `url`.
- `url`: A full GitHub raw URL to verify (e.g., `https://github.com/owner/repo/raw/main/docs/mockups/file.png`). Mutually exclusive with `path`.
- `owner`: Repository owner (optional if credentials are configured)
- `repo`: Repository name (optional if credentials are configured)
- `branch`: Branch name (optional, defaults to `main`)

**Canonical Paths:**
- Mockup images are ALWAYS at `docs/mockups/{filename}` — never `docs/main/docs/mockups/`, `docs/docs/mockups/`, or `main/docs/mockups/`.

**Example Usage (verify a path):**
```json
{
    "tool_name": "verify_github_path",
    "tool_args": {
        "path": "docs/mockups/dashboard-abc123.png",
        "owner": "your-bot-username",
        "repo": "my-project"
    }
}
```

**Example Usage (verify and fix a URL):**
```json
{
    "tool_name": "verify_github_path",
    "tool_args": {
        "url": "https://github.com/your-bot-username/my-project/raw/main/docs/main/docs/mockups/dashboard-abc123.png"
    }
}
```

**Common Hallucination Patterns Fixed:**
| Hallucinated Path | Corrected Path |
|---|---|
| `docs/main/docs/mockups/file.png` | `docs/mockups/file.png` |
| `docs/docs/mockups/file.png` | `docs/mockups/file.png` |
| `main/docs/mockups/file.png` | `docs/mockups/file.png` |
| `docs/main/mockups/file.png` | `docs/mockups/file.png` |
| `mockups/file.png` | `docs/mockups/file.png` |
