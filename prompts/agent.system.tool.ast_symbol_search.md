Use the `ast_symbol_search` tool to perform high-precision structural search of Python code symbols like classes, functions, and methods.

### Benefits over other tools:
1. **Python Specialization**: Unlike general tools like `ast-grep` which may have parsing issues, this tool uses the standard Python `ast` module and is 100% reliable for `.py` files.
2. **Precision**: It understands code structure and misses matches in comments, docstrings, or string literals.
3. **Context**: It distinguishes between standalone functions and class methods.
4. **Structured Data**: Returns a list of symbols with their types, files, and line numbers.

### Usage:
- `path`: (Required) The directory or file to search.
- `symbol_type`: (Optional) 'class', 'function', 'method', or 'all' (default).
- `pattern`: (Optional) Substring to filter symbol names (case-insensitive).

### Examples:
1. Search for all classes in a directory:
   ```json
   {
       "path": "python/helpers",
       "symbol_type": "class"
   }
   ```
2. Search for a specific method across the project:
   ```json
   {
       "path": ".",
       "symbol_type": "method",
       "pattern": "execute"
   }
   ```
3. Search for all functions in a single file:
   ```json
   {
       "path": "agent.py",
       "symbol_type": "function"
   }
   ```
