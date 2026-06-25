from __future__ import annotations
import ast
import os
from python.helpers.tool import Tool, Response

class AstSymbolSearch(Tool):
    """
    Tool for high-precision structural search of Python code symbols (classes, functions, methods).
    Uses the built-in 'ast' module for reliable indexing.
    """

    async def execute(self, path: str = ".", symbol_type: str = "all", pattern: str = "", **kwargs) -> Response:
        """
        Execute structural search.
        
        Args:
            path: File or directory path to search (defaults to current directory).
            symbol_type: Type of symbol to find: 'class', 'function', 'method', or 'all'.
            pattern: Substring pattern to filter symbol names.
        """
        # Alias 'symbol' to 'pattern' for better agent compatibility
        if "symbol" in kwargs and not pattern:
            pattern = kwargs["symbol"]
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            return Response(message=f"Error: Path '{path}' not found.", break_loop=False)

        results = []
        
        if os.path.isfile(abs_path):
            if not abs_path.endswith(".py"):
                return Response(message=f"Error: 'ast_symbol_search' only supports Python files. For Javascript or other languages, please use terminal tools like 'grep' or 'find'.", break_loop=False)
            results.extend(self._search_file(abs_path, symbol_type, pattern))
        else:
            for root, dirs, files in os.walk(abs_path):
                # Skip hidden directories and common excludes
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', 'venv', '__pycache__', 'target', '.git']]
                for file in files:
                    if file.endswith(".py"):
                        file_path = os.path.join(root, file)
                        results.extend(self._search_file(file_path, symbol_type, pattern))

        if not results:
            sym_desc = f"matching '{pattern}'" if pattern else "all symbols"
            return Response(message=f"No {symbol_type} symbols {sym_desc} found in '{path}'.", break_loop=False)

        # Sort results by file and line
        results.sort(key=lambda x: (x['file'], x['line']))

        # Format results (limit to top 100)
        output = f"### Structural Search Results in '{path}'\n\n"
        output += f"Found {len(results)} matches. Showing first 100:\n\n"
        
        for res in results[:100]:
            rel_file = os.path.relpath(res['file'], os.getcwd())
            output += f"- **{res['name']}** ({res['type']}) in `{rel_file}` (Line {res['line']})\n"

        if len(results) > 100:
            output += f"\n... and {len(results) - 100} more matches."

        # Explicit summary for UI skeleton view
        summary = f"📊 Key Findings: Found {len(results)} {symbol_type} symbol(s) in '{path}'"
        if pattern:
             summary += f" matching '{pattern}'"

        return Response(message=output, break_loop=False, summary=summary, additional={"results": results})

    def _search_file(self, file_path: str, symbol_type: str, pattern: str) -> list:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                tree = ast.parse(content)
        except Exception as e:
            # Silent fail for unparseable files
            return []

        results = []
        
        class SymbolVisitor(ast.NodeVisitor):
            def __init__(self, file_path, symbol_type, pattern):
                self.file_path = file_path
                self.symbol_type = symbol_type
                self.pattern = pattern.lower()
                self.results = []
                self.current_class = None

            def visit_Module(self, node):
                for item in node.body:
                    self.visit(item)

            def visit_ClassDef(self, node):
                if self.symbol_type in ["class", "all"]:
                    if not self.pattern or self.pattern in node.name.lower():
                        self.results.append({
                            "name": node.name,
                            "type": "class",
                            "file": self.file_path,
                            "line": node.lineno
                        })
                
                old_class = self.current_class
                self.current_class = node.name
                for item in node.body:
                    self.visit(item)
                self.current_class = old_class

            def visit_FunctionDef(self, node):
                node_type = "method" if self.current_class else "function"
                
                if self.symbol_type == "all" or self.symbol_type == node_type:
                    if not self.pattern or self.pattern in node.name.lower():
                        name = f"{self.current_class}.{node.name}" if self.current_class else node.name
                        self.results.append({
                            "name": name,
                            "type": node_type,
                            "file": self.file_path,
                            "line": node.lineno
                        })
                # No need to visit children of functions for this tool
            
            def visit_AsyncFunctionDef(self, node):
                self.visit_FunctionDef(node)

            def generic_visit(self, node):
                # Stop recursion for other nodes to avoid depth issues in large expressions/statements
                pass

        visitor = SymbolVisitor(file_path, symbol_type, pattern)
        visitor.visit(tree)
        return visitor.results
