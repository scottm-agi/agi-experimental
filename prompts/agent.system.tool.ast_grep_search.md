Use the `ast_grep_search` tool to perform **structural search of TypeScript, JavaScript, JSX, and TSX** code using [ast-grep](https://ast-grep.github.io/) patterns.

### When to use this tool:
- Searching for **structural code patterns** in TS/JS/JSX/TSX files (exports, imports, component definitions, hooks usage, etc.)
- Finding all occurrences of a code pattern across a project
- **For Python**, use `ast_symbol_search` instead — it uses the stdlib `ast` module and is more reliable for `.py` files.

### Benefits over grep:
1. **Structure-aware**: Matches code structure, not just text. `export function $NAME` matches any exported function regardless of naming.
2. **Metavariables**: Use `$NAME` for single nodes, `$$$ARGS` for multiple nodes — captures vary naturally.
3. **Language-specific**: Parses actual AST, so it won't match patterns inside strings or comments.

### Usage:
- `pattern`: (Required) ast-grep pattern with metavariables.
- `path`: (Optional) Directory or file to search. Defaults to `.`.
- `language`: (Optional) `typescript`, `javascript`, `tsx`, or `jsx`. Defaults to `typescript`.

### Example Patterns:

1. Find all exported functions:
   ```json
   {
       "pattern": "export function $NAME($$$PARAMS) { $$$BODY }",
       "path": "src/",
       "language": "typescript"
   }
   ```

2. Find all named exports:
   ```json
   {
       "pattern": "export const $NAME = $EXPR",
       "path": "src/",
       "language": "typescript"
   }
   ```

3. Find imports from a specific module:
   ```json
   {
       "pattern": "import { $$$NAMES } from 'react'",
       "path": "src/",
       "language": "tsx"
   }
   ```

4. Find React component definitions:
   ```json
   {
       "pattern": "export default function $NAME($$$PROPS) { $$$BODY }",
       "path": "src/components/",
       "language": "tsx"
   }
   ```

5. Find useState hooks:
   ```json
   {
       "pattern": "const [$STATE, $SETTER] = useState($$$INIT)",
       "path": "src/",
       "language": "tsx"
   }
   ```

### Notes:
- Requires the `sg` CLI (ast-grep) to be installed. Install via `npm install -g @ast-grep/cli` or `cargo install ast-grep`.
- For Python structural search, use `ast_symbol_search` instead.
