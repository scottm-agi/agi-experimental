from __future__ import annotations
import logging
import sys
import os

# Default Playwright path for persistent storage on Railway
if os.getenv("RAILWAY_ENVIRONMENT") and not os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/agix/data/playwright" if os.path.exists("/agix/data") else "/agix/data/playwright"

# Store the original print function at the module level
_original_print = print

def mcp_print(*args, **kwargs):
    """
    Redirects print calls to stderr if AGIX_MCP_SERVER is true.
    Used for monkeypatching builtins.print.
    """
    if os.environ.get("AGIX_MCP_SERVER") == "true":
        # If no file is specified or it's stdout, redirect to stderr
        if 'file' not in kwargs or kwargs['file'] is None or kwargs['file'] == sys.stdout:
            kwargs['file'] = sys.stderr
    _original_print(*args, **kwargs)

def init_mcp_logging():
    """
    Configure logging for MCP servers to ensure all output goes to stderr.
    This prevents Pydantic validation errors in the MCP client caused by
    non-JSON output on stdout.
    """
    # Force AGIX_MCP_SERVER env var if not set (we are in an MCP server)
    os.environ["AGIX_MCP_SERVER"] = "true"
    
    # Configure root logger to use stderr
    # We clear existing handlers to ensure we are the primary configuration
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        
    # Create stderr handler
    stderr_handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter('%(levelname)-8s [%(name)s] %(message)s')
    stderr_handler.setFormatter(formatter)
    
    root.addHandler(stderr_handler)
    root.setLevel(logging.INFO)
    
    # Silence FastMCP startup logs
    try:
        from fastmcp.utilities.logging import configure_logging
        configure_logging(level="CRITICAL")
        logging.info("FastMCP logging silenced (level=CRITICAL)")
    except ImportError:
        pass
    
    logging.info("MCP Logging initialized: All logs redirected to stderr.")
    
    # Silence tqdm progress bars globally (used by sentence-transformers, etc.)
    os.environ["TQDM_DISABLE"] = "true"
    
    # Nuclear Option: Monkeypatch builtins.print to catch all raw print() calls
    import builtins
    if builtins.print != mcp_print:
        builtins.print = mcp_print
        logging.info("Global print monkeypatch applied.")

if __name__ == "__main__":
    init_mcp_logging()
