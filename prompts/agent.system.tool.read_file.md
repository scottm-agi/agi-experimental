## Tool: read_file

Read one or more file contents in a single call. Use for inspecting existing code, configs, data files.

### Usage

**Single file:**
~~~json
{
  "path": "src/app/page.tsx"
}
~~~

**Multiple files at once (preferred over cat):**
~~~json
{
  "files": [
    {"path": "src/lib/prisma.ts"},
    {"path": "src/types/index.ts"},
    {"path": "prisma/schema.prisma"}
  ]
}
~~~

**Specific line ranges:**
~~~json
{
  "files": [
    {"path": "src/app/page.tsx", "line_ranges": [[1, 50], [100, 150]]}
  ]
}
~~~

### Parameters
- `path` — single file path (shorthand when reading one file)
- `files` — array of `{path, line_ranges}` objects for batch reads
- `max_lines` — max lines per file before truncation (default: 2000)

### Best practices
- **Use `files` array to read multiple files in one call** — do NOT use `cat` via code_execution_tool
- Check memories/solutions first before reading many files
- Read relevant files to understand context before modifying
- Use line ranges for large files to avoid truncation
