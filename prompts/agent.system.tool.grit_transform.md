# grit_transform — AST-Aware Code Transformation Tool

> **⚠️ MANDATORY**: This is the **ONLY** tool you should use for structural code transformations.
> **NEVER** run `grit apply` or `grit check` directly via `code_execution_tool`.
> Always use `grit_transform` — it wraps the CLI with proper error handling, output parsing, and logging.

Perform AST-aware code transformations using GritQL patterns. This tool provides structural code editing powered by Tree-sitter, supporting Python, JavaScript, TypeScript, Go, Rust, and more.

Unlike text-based find-and-replace (sed, awk, grep), GritQL understands code structure — it matches on **AST nodes**, not string patterns. This means it won't accidentally match inside strings, comments, or unrelated code.

## When to Use This Tool

Use `grit_transform` whenever you need to:
- **Rename** functions, variables, methods, or parameters across multiple files
- **Refactor** API calls (e.g., migrating from one library to another)
- **Enforce coding patterns** (e.g., replacing `print()` with `logger.info()`, `console.log` with structured logging)
- **Remove deprecated code** patterns across a codebase
- **Migrate imports** (e.g., `from old_module import X` → `from new_module import X`)
- **Transform** any repetitive code pattern structurally

### Keyword Triggers
If your task involves any of these words, you should use `grit_transform`:
`transform`, `refactor`, `rename`, `migrate`, `replace pattern`, `code pattern`, `AST`, `structural edit`, `bulk rename`, `codemod`, `find and replace function`, `update calls`, `modernize`, `standardize`

## Recommended Workflow

1. **Discover** — Use `rg` (via `code_execution_tool`) to find candidate files
2. **Understand** — Use `read_file` or `ast_symbol_search` to understand code structure
3. **Preview** — Use `grit_transform` with `dry_run: true` to see what will change
4. **Transform** — Use `grit_transform` with `dry_run: false` to apply changes
5. **Verify** — Use `code_execution_tool` to run tests or diff the changes

## Parameters

- **pattern** (required): GritQL pattern for matching and transforming code. Uses backtick syntax for code patterns.
- **target** (optional): File path or list of file paths to transform. Default: `"."` (current directory)
- **dry_run** (optional): If `true`, preview changes without applying. Default: `false`
- **language** (optional): Language hint (e.g., `"python"`, `"javascript"`, `"typescript"`)

## GritQL Pattern Syntax

Patterns use backticks to match code structure:

```gritql
# Simple rename
`console.log($msg)` => `logger.info($msg)`

# Function parameter rename
`def $func($old)` => `def $func($new_name)`

# Remove deprecated calls
`deprecatedFunc($args)` => .

# Import migration
`from old_module import $sym` => `from new_module import $sym`

# Method rename
`$obj.oldMethod($args)` => `$obj.newMethod($args)`
```

Variables start with `$` and match any AST node of appropriate type.

## Example Usage

### Rename a function across files
```json
{
    "tool_name": "grit_transform",
    "tool_args": {
        "pattern": "`old_function($args)` => `new_function($args)`",
        "target": ["src/app.py", "src/utils.py"],
        "dry_run": true
    }
}
```

### Replace print with logging
```json
{
    "tool_name": "grit_transform",
    "tool_args": {
        "pattern": "`print($msg)` => `logger.info($msg)`",
        "target": ".",
        "language": "python"
    }
}
```

### Preview before applying
Always use `dry_run: true` first to verify the pattern matches what you expect, then run again without dry_run to apply.

## Limitations

- Complex multi-statement transformations may need multiple patterns
- Some edge cases with macro-heavy or heavily templated code
- The `grit` CLI is pre-installed; if missing, the tool will report the error
