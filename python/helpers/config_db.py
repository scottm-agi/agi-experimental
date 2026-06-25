from __future__ import annotations
"""
SQLite-based configuration database for secrets and parameters.

Provides thread-safe, atomic operations for storing:
- Secrets (scoped by global or project name)
- Parameters (scoped by global or project name)

Uses WAL mode for better concurrency support.
"""

import sqlite3
import threading
import os
import json
import time
import random
from typing import Dict, Any, Optional, List, Tuple
from contextlib import contextmanager
from functools import lru_cache
from python.helpers import files
from python.helpers.print_style import PrintStyle

# Default database file path
DEFAULT_DB_PATH = "data/config.db"

# Thread-local storage for connections
_local = threading.local()
_init_lock = threading.Lock()
_initialized = False


@lru_cache()
def get_db_path() -> str:
    """Get the absolute path to the config database."""
    env_path = os.environ.get("AGENT_ZERO_CONFIG_DB")
    if env_path:
        return os.path.abspath(env_path)
    return files.get_abs_path(DEFAULT_DB_PATH)


def get_connection() -> sqlite3.Connection:
    """Get a thread-local database connection with retries."""
    if not hasattr(_local, 'connection') or _local.connection is None:
        db_path = get_db_path()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        # Exponential backoff for connection
        max_retries = 5
        for attempt in range(max_retries):
            try:
                conn = sqlite3.connect(db_path, timeout=60, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                
                # Optimizations for virtualized environments (Docker/Mac)
                # NOTE: WAL mode is often problematic on shared volumes (disk i/o error)
                # Switching to DELETE mode for maximum compatibility
                conn.execute("PRAGMA journal_mode=DELETE")
                conn.execute("PRAGMA busy_timeout=120000")  # Increase to 120s
                conn.execute("PRAGMA synchronous=FULL")     # Safer for shared mounts
                conn.execute("PRAGMA cache_size=-2000")    # 2MB cache
                conn.execute("PRAGMA mmap_size=0")         # Disable mmap to avoid I/O errors
                
                # Check if it actually works
                conn.execute("SELECT 1").fetchone()
                
                _local.connection = conn
                _ensure_schema(conn)
                break
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if ("database is locked" in msg or "disk i/o error" in msg) and attempt < max_retries - 1:
                    wait = min((attempt + 1) * 0.2 + random.uniform(0, 0.1), 1.0)  # Fast backoff
                    time.sleep(wait)
                    continue
                raise
            except Exception:
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                    continue
                raise
    
    return _local.connection


def _ensure_schema(conn: sqlite3.Connection):
    """Ensure database schema exists without triggering recursive retries."""
    global _initialized
    
    with _init_lock:
        if _initialized:
            return
        
        # We use a simple executescript here as we are inside get_connection()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS secrets (
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (scope, key)
            );
            
            CREATE TABLE IF NOT EXISTS parameters (
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (scope, key)
            );
            
            CREATE INDEX IF NOT EXISTS idx_secrets_scope ON secrets(scope);
            CREATE INDEX IF NOT EXISTS idx_parameters_scope ON parameters(scope);
        """)
        conn.commit()
        _initialized = True


def execute_with_retry(conn: sqlite3.Connection, sql: str, params: Tuple = (), max_retries: int = 5, _is_retry: bool = False) -> sqlite3.Cursor:
    """Execute a SQL statement with retries. Prevents infinite recursion."""
    current_conn = conn
    for attempt in range(max_retries):
        try:
            return current_conn.execute(sql, params)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if ("database is locked" in msg or "disk i/o error" in msg) and attempt < max_retries - 1:
                PrintStyle.debug(f"[config_db] {msg} during execute. Attempt {attempt + 1}/{max_retries}. Retrying...")
                
                # If disk I/O error, try to reset the connection
                if "disk i/o error" in msg and not _is_retry:
                    try:
                        current_conn.close()
                    except Exception:
                        pass
                    _local.connection = None
                    # Use a new connection for the next attempt, but mark as retry to avoid recursion
                    try:
                        current_conn = get_connection()
                    except Exception as conn_err:
                        PrintStyle.debug(f"[config_db] Failed to reconnect during retry: {conn_err}")
                
                wait = min((attempt + 1) * 0.2 + random.uniform(0, 0.1), 1.0)  # Fast backoff: max 1s
                time.sleep(wait)
                continue
            
            # Log final failure
            PrintStyle(font_color="red").print(f"[config_db] FATAL: {e} after {max_retries} attempts. SQL: {sql}")
            raise


@contextmanager
def transaction():
    """Context manager for atomic transactions. Supports non-recursive nesting."""
    conn = get_connection()
    # Check if we are already in a transaction
    in_transaction = conn.in_transaction
    
    try:
        if not in_transaction:
            # wait for busy timeout if needed
            execute_with_retry(conn, "BEGIN IMMEDIATE")
        yield conn
        if not in_transaction:
            conn.commit()
    except Exception:
        if not in_transaction:
            conn.rollback()
        raise


# ============== SECRETS API ==============

def get_secrets(scope: str = "global") -> Dict[str, str]:
    """Load all secrets for a scope."""
    conn = get_connection()
    cursor = execute_with_retry(
        conn,
        "SELECT key, value FROM secrets WHERE scope = ?",
        (scope,)
    )
    return {row['key']: row['value'] for row in cursor.fetchall()}


def get_secret(key: str, scope: str = "global", default: Optional[str] = None) -> Optional[str]:
    """Get a single secret by key."""
    conn = get_connection()
    cursor = execute_with_retry(
        conn,
        "SELECT value FROM secrets WHERE scope = ? AND key = ?",
        (scope, key)
    )
    row = cursor.fetchone()
    return row['value'] if row else default


def set_secret(key: str, value: str, scope: str = "global"):
    """Set a single secret."""
    PrintStyle.debug(f"[config_db] set_secret: key={key}, scope={scope} (len={len(value)})")
    with transaction() as conn:
        execute_with_retry(conn, """
            INSERT INTO secrets (scope, key, value, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(scope, key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
        """, (scope, key, value))


def set_secrets(secrets: Dict[str, str], scope: str = "global", replace: bool = False):
    """Set multiple secrets atomically.
    
    If replace=True, removes existing secrets not in the new dict.
    If replace=False, merges with existing secrets.
    """
    PrintStyle.debug(f"[config_db] set_secrets: scope={scope}, count={len(secrets)}, replace={replace}")
    with transaction() as conn:
        if replace:
            PrintStyle.debug(f"[config_db] DELETING ALL secrets for scope={scope} due to replace=True")
            execute_with_retry(conn, "DELETE FROM secrets WHERE scope = ?", (scope,))
        
        for key, value in secrets.items():
            execute_with_retry(conn, """
                INSERT INTO secrets (scope, key, value, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(scope, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
            """, (scope, key, value))


def delete_secret(key: str, scope: str = "global"):
    """Delete a single secret."""
    PrintStyle.debug(f"[config_db] delete_secret: key={key}, scope={scope}")
    with transaction() as conn:
        execute_with_retry(conn, "DELETE FROM secrets WHERE scope = ? AND key = ?", (scope, key))


def get_secrets_as_env(scope: str = "global") -> str:
    """Get secrets formatted as .env content for UI display."""
    secrets = get_secrets(scope)
    lines = []
    for key, value in secrets.items():
        # Escape quotes in value
        escaped = value.replace('"', '\\"')
        lines.append(f'{key}="{escaped}"')
    return "\n".join(lines)


def set_secrets_from_env(content: str, scope: str = "global", merge: bool = True):
    """Parse .env format and save secrets.
    
    If merge=True, merges with existing (preserving secrets not in content).
    If merge=False, replaces all secrets with content.
    """
    parsed = parse_env_content(content)
    
    if merge:
        # Merge: update only keys present in content
        for key, value in parsed.items():
            set_secret(key, value, scope)
    else:
        set_secrets(parsed, scope, replace=True)


def parse_env_content(content: str) -> Dict[str, str]:
    """Parse .env format string into dict, handling trailing comments."""
    result = {}
    for line in content.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        
        idx = line.find('=')
        if idx == -1:
            continue
        
        key = line[:idx].strip()
        value = line[idx + 1:].strip()
        
        # Remove trailing inline comment (simple logic)
        # Scan for # but respect quotes
        in_single = False
        in_double = False
        esc = False
        comment_idx = None
        for i, ch in enumerate(value):
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == "'" and not in_double:
                in_single = not in_single
                continue
            if ch == '"' and not in_single:
                in_double = not in_double
                continue
            if ch == "#" and not in_single and not in_double:
                comment_idx = i
                break
        
        if comment_idx is not None:
            value = value[:comment_idx].strip()

        # Remove quotes
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        
        if key:
            result[key.upper()] = value
    
    return result


# ============== PARAMETERS API ==============

def get_parameters(scope: str = "global") -> Dict[str, Any]:
    """Load all parameters for a scope."""
    conn = get_connection()
    cursor = execute_with_retry(
        conn,
        "SELECT key, value FROM parameters WHERE scope = ?",
        (scope,)
    )
    result = {}
    for row in cursor.fetchall():
        try:
            result[row['key']] = json.loads(row['value'])
        except json.JSONDecodeError:
            result[row['key']] = row['value']
    return result


def get_parameter(key: str, scope: str = "global", default: Any = None) -> Any:
    """Get a single parameter by key. Re-evaluates JSON values."""
    conn = get_connection()
    cursor = execute_with_retry(
        conn,
        "SELECT value FROM parameters WHERE scope = ? AND key = ?",
        (scope, key)
    )
    row = cursor.fetchone()
    if row:
        val = row['value']
        # Double-parsing check: parameters are stored as JSON strings.
        # If it was a string, it's stored as "value" (with quotes).
        # loads() will return the raw string.
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return default


def set_parameter(key: str, value: Any, scope: str = "global"):
    """Set a single parameter."""
    PrintStyle.debug(f"[config_db] set_parameter: key={key}, scope={scope}")
    json_value = json.dumps(value) if not isinstance(value, str) else json.dumps(value)
    with transaction() as conn:
        execute_with_retry(conn, """
            INSERT INTO parameters (scope, key, value, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(scope, key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
        """, (scope, key, json_value))


def set_parameters(params: Dict[str, Any], scope: str = "global", replace: bool = False):
    """Set multiple parameters atomically.
    
    If replace=True, removes existing parameters not in the new dict.
    If replace=False, merges with existing parameters.
    """
    PrintStyle.debug(f"[config_db] set_parameters: scope={scope}, count={len(params)}, replace={replace}")
    with transaction() as conn:
        if replace:
            PrintStyle.debug(f"[config_db] DELETING ALL parameters for scope={scope} due to replace=True")
            execute_with_retry(conn, "DELETE FROM parameters WHERE scope = ?", (scope,))
        
        for key, value in params.items():
            json_value = json.dumps(value)
            execute_with_retry(conn, """
                INSERT INTO parameters (scope, key, value, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(scope, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
            """, (scope, key, json_value))


def delete_parameter(key: str, scope: str = "global"):
    """Delete a single parameter."""
    PrintStyle.debug(f"[config_db] delete_parameter: key={key}, scope={scope}")
    with transaction() as conn:
        execute_with_retry(conn, "DELETE FROM parameters WHERE scope = ? AND key = ?", (scope, key))


def delete_scope(scope: str):
    """Delete all secrets and parameters for a specific scope.
    
    Used during project or chat cleanup.
    """
    if not scope or scope == "global":
        return

    PrintStyle.debug(f"[config_db] delete_scope: scope={scope}")
    with transaction() as conn:
        execute_with_retry(conn, "DELETE FROM secrets WHERE scope = ?", (scope,))
        execute_with_retry(conn, "DELETE FROM parameters WHERE scope = ?", (scope,))


def get_parameters_as_json(scope: str = "global") -> str:
    """Get parameters formatted as JSON string for UI display."""
    params = get_parameters(scope)
    return json.dumps(params, indent=4)


def set_parameters_from_json(content: str, scope: str = "global", merge: bool = True):
    """Parse JSON format and save parameters.
    
    If merge=True, merges with existing (preserving params not in content).
    If merge=False, replaces all parameters with content.
    """
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return
    
    if not isinstance(parsed, dict):
        return
    
    if merge:
        for key, value in parsed.items():
            set_parameter(key, value, scope)
    else:
        set_parameters(parsed, scope, replace=True)


# ============== MIGRATION ==============

def migrate_from_files(
    secrets_file: str = "tmp/secrets.env",
    params_file: str = "tmp/parameters.json",
    scope: str = "global"
):
    """Migrate existing file-based secrets and parameters to database."""
    # Migrate secrets
    secrets_path = files.get_abs_path(secrets_file)
    if os.path.exists(secrets_path):
        try:
            content = files.read_file(secrets_path)
            if content.strip():
                set_secrets_from_env(content, scope, merge=True)
                print(f"Migrated secrets from {secrets_file}")
        except Exception as e:
            print(f"Warning: Could not migrate secrets from {secrets_file}: {e}")
    
    # Migrate parameters
    params_path = files.get_abs_path(params_file)
    if os.path.exists(params_path):
        try:
            content = files.read_file(params_path)
            if content.strip() and content.strip() != "{}":
                set_parameters_from_json(content, scope, merge=True)
                print(f"Migrated parameters from {params_file}")
        except Exception as e:
            print(f"Warning: Could not migrate parameters from {params_file}: {e}")


def check_integrity() -> bool:
    """Run a PRAGMA integrity_check on the database."""
    conn = get_connection()
    try:
        cursor = execute_with_retry(conn, "PRAGMA integrity_check")
        row = cursor.fetchone()
        if row and row[0] == "ok":
            return True
        return False
    except Exception:
        return False


def close_connection():
    """Close the thread-local connection."""
    if hasattr(_local, 'connection') and _local.connection:
        _local.connection.close()
        _local.connection = None
