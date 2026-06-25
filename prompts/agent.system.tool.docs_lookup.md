### tool name: docs_lookup

Look up framework and library documentation. Use this BEFORE configuring any framework, ORM, bundler, or build tool to avoid version-specific errors.

**When to use**: Whenever you need to check how a library/framework is configured, what API it exposes, or what version-specific syntax to use. ALWAYS use this before writing configuration files (next.config.mjs, prisma/schema.prisma, tailwind.config.js, etc.).

**Arguments**:
- `library`: The name of the library or framework (e.g., "next.js", "prisma", "tailwindcss", "react")
- `query`: What you need to know (e.g., "app router configuration", "prisma schema syntax", "v4 migration guide")
- `version`: (optional) Specific version to look up (e.g., "16", "7.7", "4.0")

**How it works**: This tool transparently tries multiple documentation sources in order:
1. Context7 documentation database (fastest, most accurate)
2. Web search scoped to official docs sites
3. AI research as final fallback

You will always get a result — no need to handle "tool not found" errors.

**Example**:
```json
{
    "tool_name": "docs_lookup",
    "tool_args": {
        "library": "next.js",
        "query": "app router next.config.mjs options",
        "version": "16"
    }
}
```

**Rules**:
- ALWAYS use this before writing framework configuration files
- Use specific queries — "prisma schema relations syntax" is better than "prisma docs"
- Include the version when you know it (check package.json first)
- Do NOT use `context7` directly — use this tool instead (it handles Context7 internally)
