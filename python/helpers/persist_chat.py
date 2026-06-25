from __future__ import annotations
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any
import logging
import os
import shutil
import uuid
from python.agent import Agent, AgentConfig, AgentContext, AgentContextType
import python.history as history
from python.helpers import files
import json
from python.initialize import initialize_agent

from python.helpers.log import Log, LogItem
from python.helpers.secret_redactor import redact_known_secrets

logger = logging.getLogger("agix.persist_chat")

# Set of context IDs that have been explicitly removed to prevent re-saving by race conditions
REMOVED_CONTEXTS: set[str] = set()

CHATS_FOLDER = "tmp/chats"
LOG_SIZE = 5000
CHAT_FILE_NAME = "chat.json"
WAL_EXTENSION = ".wal"
BACKUP_EXTENSION = ".bak"


def get_chat_folder_path(ctxid: str):
    """
    Get the folder path for any context (chat or task).

    Args:
        ctxid: The context ID

    Returns:
        The absolute path to the context folder
    """
    return files.get_abs_path(CHATS_FOLDER, ctxid)

def get_chat_msg_files_folder(ctxid: str):
    return files.get_abs_path(get_chat_folder_path(ctxid), "messages")

def save_tmp_chat(context: AgentContext):
    """Save context to the chats folder"""
    # Skip saving if context has been explicitly removed
    if context.id in REMOVED_CONTEXTS:
        logger.debug(f"Skipping save for removed context {context.id}")
        return

    # Skip saving BACKGROUND contexts as they should be ephemeral
    if context.type == AgentContextType.BACKGROUND:
        return

    path = _get_chat_file_path(context.id)
    files.make_dirs(path)
    data = _serialize_context(context)
    js = _safe_json_serialize(data, ensure_ascii=False)
    if not js or js.strip() in ["", "{}", "[]"]:
        logger.warning(f"Skipping save for context {context.id}: Serialization returned empty/minimal data.")
        return
    # F-21 (RCA-358): Redact any leaked secrets from the serialized JSON before writing to disk
    project_name = context.get_data("project") or ""
    js = redact_known_secrets(js, project_name=project_name)
    files.write_file(path, js)


def save_tmp_chats():
    """Save all contexts to the chats folder"""
    # Use local import to avoid circular dependency issues during shutdown
    from python.helpers.persist_chat import save_tmp_chat
    for _, context in AgentContext._contexts.items():
        # Skip BACKGROUND contexts as they should be ephemeral
        if context.type == AgentContextType.BACKGROUND:
            continue
        save_tmp_chat(context)


def _gc_query_known_ids_postgres(database_url: str) -> set:
    """Query PostgreSQL for known context IDs using psycopg2 (sync).
    
    Returns set of context IDs, or None if connection fails (caller should
    skip GC rather than assume everything is an orphan).
    """
    try:
        import psycopg2
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()
        cur.execute("SELECT id FROM contexts")
        known_ids = {row[0] for row in cur.fetchall()}
        cur.close()
        conn.close()
        logger.debug(f"GC: PostgreSQL query returned {len(known_ids)} known context IDs")
        return known_ids
    except ImportError:
        logger.warning("GC: psycopg2 not installed — cannot query PostgreSQL for orphan check")
        return None
    except Exception as e:
        logger.warning(f"GC: Could not query PostgreSQL for orphan check: {e}")
        return None


def _gc_query_known_ids_sqlite() -> set:
    """Query local SQLite for known context IDs.
    
    Returns set of context IDs, or None if query fails.
    """
    import sqlite3
    db_path = files.get_abs_path("tmp/agix.db")
    if not os.path.exists(db_path):
        return set()
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT id FROM contexts").fetchall()
        known_ids = {row[0] for row in rows}
        conn.close()
        return known_ids
    except Exception as e:
        logger.warning(f"GC: Could not query SQLite database: {e}")
        return None


def gc_orphan_folders():
    """Remove chat folders from disk that have no matching entry in the SQL database.
    
    This prevents startup from loading hundreds of stale contexts that were
    deleted via the UI but whose disk folders were never cleaned up.
    Fixes Forgejo #911.
    
    IMPORTANT: When DATABASE_URL points to PostgreSQL, we query PostgreSQL
    (not local SQLite) for known context IDs. Without this, container restarts
    wipe all webhook chat folders because the local SQLite is always empty
    when PostgreSQL is the primary persistence layer. (Forgejo #1143)
    """
    chats_path = files.get_abs_path(CHATS_FOLDER)
    if not os.path.exists(chats_path):
        return 0
    
    # Exclude known non-directory entries that live alongside chat folders
    GC_EXCLUDE = {"chats.db"}
    all_entries = files.list_files(CHATS_FOLDER, "*")
    disk_folders = set(entry for entry in all_entries if entry not in GC_EXCLUDE)
    if not disk_folders:
        return 0
    
    # Query the CORRECT database for known context IDs based on DATABASE_URL
    database_url = os.environ.get("DATABASE_URL", "")
    is_postgres = database_url.startswith("postgresql")
    
    if is_postgres:
        known_ids = _gc_query_known_ids_postgres(database_url)
        if known_ids is None:
            # Cannot verify — do NOT delete anything to avoid data loss
            logger.warning(
                f"GC: PostgreSQL query failed — skipping orphan GC to prevent data loss "
                f"({len(disk_folders)} folders on disk, cannot verify against DB)"
            )
            return 0
    else:
        known_ids = _gc_query_known_ids_sqlite()
        if known_ids is None:
            return 0  # Don't delete anything if we can't verify
    
    # Find orphans: on disk but not in SQL
    orphans = disk_folders - known_ids
    if not orphans:
        db_label = "PostgreSQL" if is_postgres else "SQLite"
        logger.debug(f"GC: No orphan folders found ({len(disk_folders)} folders, {len(known_ids)} in {db_label})")
    else:
        removed = 0
        for folder_name in orphans:
            folder_path = files.get_abs_path(CHATS_FOLDER, folder_name)
            try:
                files.delete_dir(folder_path)
                removed += 1
            except Exception as e:
                logger.warning(f"GC: Failed to remove orphan folder {folder_name}: {e}")
        
        db_label = "PostgreSQL" if is_postgres else "SQLite"
        logger.info(f"GC: Removed {removed}/{len(orphans)} orphan chat folders ({len(disk_folders) - removed} remaining, {len(known_ids)} in {db_label})")
    
    # Reverse check: SQL entries with no disk folder = orphan SQL rows
    # Only applicable for SQLite (PostgreSQL cleanup is handled by DB-level cascade)
    if not is_postgres:
        import sqlite3
        db_path = files.get_abs_path("tmp/agix.db")
        sql_orphans = known_ids - disk_folders
        if sql_orphans:
            sql_removed = 0
            try:
                for orphan_id in sql_orphans:
                    try:
                        conn2 = sqlite3.connect(db_path)
                        # Delete related data first (foreign key order)
                        log_ids = [r[0] for r in conn2.execute("SELECT id FROM logs WHERE context_id=?", (orphan_id,)).fetchall()]
                        for lid in log_ids:
                            conn2.execute("DELETE FROM log_items WHERE log_id=?", (lid,))
                        agent_ids = [r[0] for r in conn2.execute("SELECT id FROM agents WHERE context_id=?", (orphan_id,)).fetchall()]
                        for aid in agent_ids:
                            conn2.execute("DELETE FROM messages WHERE agent_id=?", (aid,))
                        conn2.execute("DELETE FROM agents WHERE context_id=?", (orphan_id,))
                        conn2.execute("DELETE FROM logs WHERE context_id=?", (orphan_id,))
                        conn2.execute("DELETE FROM contexts WHERE id=?", (orphan_id,))
                        conn2.commit()
                        conn2.close()
                        sql_removed += 1
                    except Exception as e:
                        logger.warning(f"GC: Failed to remove SQL orphan {orphan_id}: {e}")
                logger.info(f"GC: Removed {sql_removed}/{len(sql_orphans)} orphan SQL entries (no disk folder)")
            except Exception as e:
                logger.warning(f"GC: SQL orphan cleanup failed: {e}")
    
    return len(orphans) if orphans else 0


def load_tmp_chats(config: AgentConfig = None):
    """Load all contexts from the chats folder (with parallel file I/O)"""
    gc_orphan_folders()
    recover_wal_files()
    _convert_v080_chats()
    folders = files.list_files(CHATS_FOLDER, "*")
    
    if not config:
        config = initialize_agent()

    ctxids = []
    
    # Optimization: Parallel file reading (I/O bound), serial deserialization (thread-safety)
    # This avoids race conditions in PyTorch model initialization while still speeding up disk I/O
    max_workers = min(8, len(folders)) if folders else 1
    
    def read_chat_file(folder_name):
        """Read chat file content - safe to parallelize"""
        file_path = _get_chat_file_path(folder_name)
        if not os.path.exists(file_path):
            return (folder_name, None)
        try:
            js = files.read_file(file_path)
            if not js or not js.strip():
                logger.warning(f"Chat file at {file_path} is empty. Skipping.")
                return (folder_name, None)
            return (folder_name, js)
        except Exception as e:
            logger.warning(f"[PERSIST CHAT] Error reading chat file {folder_name}: {e}")
            return (folder_name, None)
    
    # Phase 1: Parallel file reading
    file_contents = []
    if len(folders) <= 3:
        # For small counts, serial is fine
        for folder in folders:
            file_contents.append(read_chat_file(folder))
    else:
        # Parallel file reads
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            file_contents = list(executor.map(read_chat_file, folders))
    
    # Phase 2: Serial deserialization (avoids PyTorch race conditions)
    for folder_name, js in file_contents:
        if js is None:
            continue
        try:
            # Check if already in memory
            existing = AgentContext.get(folder_name)
            if existing:
                ctxids.append(existing.id)
                continue
                
            data = json.loads(js)
            
            # Pre-register lightweight metadata BEFORE full deserialization.
            # This ensures the chat appears in sidebar even if full deserialization
            # fails or the context is LRU-evicted from memory. (Forgejo #1019 fix)
            from datetime import datetime as _dt
            _ca = data.get("created_at", _dt.fromtimestamp(0).isoformat())
            _lm = data.get("last_message", _dt.fromtimestamp(0).isoformat())
            try:
                _ca_dt = _dt.fromisoformat(_ca)
            except (ValueError, TypeError):
                _ca_dt = _dt.fromtimestamp(0)
            try:
                _lm_dt = _dt.fromisoformat(_lm)
            except (ValueError, TypeError):
                _lm_dt = _dt.fromtimestamp(0)
            
            AgentContext.register_metadata({
                "id": data.get("id", folder_name),
                "name": data.get("name"),
                "created_at": _ca_dt,
                "type": data.get("type", "user"),
                "last_message": _lm_dt,
                "project_name": data.get("project_name") or (data.get("data", {}) or {}).get("project", "default"),
                "parent_id": data.get("parent_id"),
            })
            
            ctx = _deserialize_context(data, config=config)
            logger.debug(f"Chat {folder_name} loaded successfully from disk.")
            ctxids.append(ctx.id)
        except json.JSONDecodeError as je:
            logger.error(f"Failed to decode JSON for {folder_name}: {je}")
        except Exception as e:
            logger.warning(f"Error deserializing chat {folder_name}: {e} (metadata still registered for sidebar)")
    
    logger.info(f"Chat loading complete: {len(ctxids)}/{len(folders)} contexts loaded from disk (folders: {len(folders)}, skipped/errors: {len(folders) - len(ctxids)})")
    return ctxids


async def load_chat(ctxid: str, config: AgentConfig = None) -> AgentContext | None:
    """
    Load a single context by its ID.
    If already in memory, returns the existing instance.
    Supports hybrid persistence (SQLite/JSON + PostgreSQL).
    """
    # 1. First check if it's already in memory
    existing = AgentContext.get(ctxid)
    if existing:
        return existing

    # 2. Try to load from SQL (Hybrid/PostgreSQL)
    from python.helpers.persistence_manager import PersistenceManager
    pm = PersistenceManager.get_instance()
    try:
        sql_data = await pm.load_context_sql(ctxid)
        if sql_data:
            if not config:
                config = initialize_agent()
            ctx = _deserialize_context(sql_data, config=config)
            logger.debug(f"Chat {ctxid} loaded successfully from SQL.")
            return ctx
    except Exception as e:
        logger.warning(f"Failed to load chat {ctxid} from SQL: {e}")

    # 3. Fallback to Disk (JSON)
    file_path = _get_chat_file_path(ctxid)
    if not os.path.exists(file_path):
        logger.debug(f"Chat file not found at {file_path}")
        return None

    try:
        if not config:
            config = initialize_agent()
            
        logger.debug(f"Loading chat {ctxid} from disk fallback...")
        js = files.read_file(file_path)
        if not js or not js.strip():
            logger.warning(f"Chat file at {file_path} is empty. Skipping.")
            return None
            
        try:
            data = json.loads(js)
        except json.JSONDecodeError as je:
            logger.error(f"Failed to decode JSON from {file_path}: {je}")
            return None
            
        ctx = _deserialize_context(data, config=config)
        logger.debug(f"Chat {ctxid} loaded successfully from disk fallback.")
        return ctx
    except Exception as e:
        logger.warning(f"Failed to load chat {ctxid} from disk: {e}", exc_info=True)
        return None


def load_tmp_chat(ctxid: str, config: AgentConfig = None) -> AgentContext | None:
    """
    Synchronous wrapper for load_chat.
    WARNING: Prefer await load_chat() in async contexts.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in event loop, this is tricky. 
            # If we are in Antigravity/Agentic mode, we should have nest_asyncio.
            return loop.run_until_complete(load_chat(ctxid, config))
        else:
            return asyncio.run(load_chat(ctxid, config))
    except Exception:
        # Fallback to direct memory/disk if loop fails
        existing = AgentContext.get(ctxid)
        if existing: return existing
        
        file_path = _get_chat_file_path(ctxid)
        if os.path.exists(file_path):
            try:
                if not config: config = initialize_agent()
                data = json.loads(files.read_file(file_path))
                return _deserialize_context(data, config=config)
            except Exception: pass
        return None


def _get_chat_file_path(ctxid: str):
    return files.get_abs_path(CHATS_FOLDER, ctxid, CHAT_FILE_NAME)


def _convert_v080_chats():
    json_files = files.list_files(CHATS_FOLDER, "*.json")
    for file in json_files:
        path = files.get_abs_path(CHATS_FOLDER, file)
        name = file.rstrip(".json")
        new = _get_chat_file_path(name)
        files.move_file(path, new)


def load_json_chats(jsons: list[str]):
    """Load contexts from JSON strings"""
    ctxids = []
    for js in jsons:
        data = json.loads(js)
        if "id" in data:
            del data["id"]  # remove id to get new
        ctx = _deserialize_context(data)
        ctxids.append(ctx.id)
    return ctxids


def export_json_chat(context: AgentContext):
    """Export context as JSON string with secret redaction."""
    data = _serialize_context(context)
    # M-1 Fix: Use centralized serialize_and_redact to prevent secret leaks
    project_name = context.get_data("project") or ""
    return serialize_and_redact(data, project_name=project_name)


def export_markdown_chat(context: AgentContext):
    """Export context as Markdown string"""
    lines = []
    
    # Header
    lines.append(f"# {context.name or 'Chat History'}")
    lines.append(f"**Context ID:** `{context.id}`")
    if context.created_at:
        lines.append(f"**Created:** {context.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Logs
    for item in context.log.logs:
        # Skip internal/temp items if they don't have content
        if not item.content and not item.heading:
            continue
            
        # Determine prefix based on type
        prefix = ""
        if item.type == "user":
            prefix = "### 👤 User"
        elif item.type in ["response", "agent"]:
            prefix = "### 🤖 Agent"
        elif item.type == "tool":
            prefix = "#### 🛠️ Tool: " + (item.heading or "Execution")
        elif item.type == "thought":
            prefix = "#### 🧠 Thought"
        elif item.type == "info":
            prefix = "#### ℹ️ Info"
        elif item.type == "error":
            prefix = "#### ❌ Error"
        elif item.type == "warning":
            prefix = "#### ⚠️ Warning"
        else:
            prefix = f"#### {item.type.capitalize()}"

        if prefix:
            lines.append(prefix)
        
        # Content formatting
        content = item.content or ""
        
        # Special handling for different types
        if item.type in ["tool", "thought", "code"]:
             # Use blockquotes or code blocks
             if "\n" in content:
                 # Try to detect if it's JSON or other code
                 lang = ""
                 if content.strip().startswith("{") or content.strip().startswith("["):
                     lang = "json"
                 lines.append(f"```{lang}")
                 lines.append(content)
                 lines.append("```")
             else:
                 lines.append(f"> {content}")
        else:
            # Regular message
            lines.append(content)
            
        # KVPs (Key-Value Pairs) if any important ones exist
        if item.kvps:
            important_kvps = {k: v for k, v in item.kvps.items() if k not in ["sequence_id", "timestamp"]}
            if important_kvps:
                lines.append("")
                lines.append("**Metadata:**")
                for k, v in important_kvps.items():
                    lines.append(f"- **{k}:** {v}")
            
        lines.append("")

    # M-1 Fix: Redact secrets from the final markdown output
    result = "\n".join(lines)
    return redact_known_secrets(result)


def export_structured_json(context: AgentContext):
    """Export context as structured JSON with medium detail (#816).
    
    Includes: user prompts, agent responses (complete), tools called (name + summary),
    agent thoughts, and key metadata.
    Excludes: TRACE messages, internal temp data, raw tool payloads, full history dumps.
    """
    turns = []
    
    for item in context.log.logs:
        # Skip empty items
        if not item.content and not item.heading:
            continue
        
        # Skip TRACE messages and performance metrics
        content = item.content or ""
        if "[TRACE]" in content or content.startswith("⏱"):
            continue
        
        # Skip temp/internal items
        if item.temp:
            continue
        
        turn = {
            "type": item.type,
            "timestamp": item.timestamp if item.timestamp else None,
        }
        
        if item.type == "user":
            turn["content"] = content
        elif item.type in ["response", "agent"]:
            turn["content"] = content
        elif item.type == "tool":
            turn["tool_name"] = item.heading or "unknown"
            # Truncate tool output to medium detail (first 500 chars)
            turn["result_summary"] = content[:500] + ("..." if len(content) > 500 else "")
        elif item.type == "thought":
            turn["content"] = content[:1000] + ("..." if len(content) > 1000 else "")
        elif item.type in ["error", "warning"]:
            turn["content"] = content
        else:
            # Include other types but mark them
            turn["content"] = content[:500] + ("..." if len(content) > 500 else "")
        
        # Include important KVPs (skip internal ones)
        if item.kvps:
            important = {k: v for k, v in item.kvps.items() 
                        if k not in ["sequence_id", "timestamp", "hash"]}
            if important:
                turn["metadata"] = important
        
        turns.append(turn)
    
    result = {
        "id": context.id,
        "name": context.name,
        "created_at": context.created_at.isoformat() if context.created_at else None,
        "turn_count": len(turns),
        "turns": turns,
    }
    
    # M-1 Fix: Redact secrets from the final structured JSON output
    raw = json.dumps(result, ensure_ascii=False, indent=2)
    return redact_known_secrets(raw)


def remove_chat(ctxid):
    """Remove a chat or task context"""
    path = get_chat_folder_path(ctxid)
    files.delete_dir(path)


def remove_msg_files(ctxid):
    """Remove all message files for a chat or task context"""
    path = get_chat_msg_files_folder(ctxid)
    files.delete_dir(path)


def _serialize_context(context: AgentContext):
    # serialize agents
    agents = []
    agent = context.agent0
    while agent:
        agents.append(_serialize_agent(agent))
        agent = agent.data.get(Agent.DATA_NAME_SUBORDINATE, None)


    data = {k: v for k, v in context.data.items() if not k.startswith("_")}
    output_data = {k: v for k, v in context.output_data.items() if not k.startswith("_")}

    return {
        "id": context.id,
        "name": context.name,
        "created_at": (
            context.created_at.isoformat()
            if context.created_at
            else datetime.fromtimestamp(0).isoformat()
        ),
        "type": context.type.value,
        "last_message": (
            context.last_message.isoformat()
            if context.last_message
            else datetime.fromtimestamp(0).isoformat()
        ),
        "agents": agents,
        "streaming_agent": (
            context.streaming_agent.number if context.streaming_agent else 0
        ),
        "log": _serialize_log(context.log) if context.log else {"guid": "", "logs": [], "progress": "", "progress_no": 0},
        "parent_id": context.parent_id,
        "project_name": context.get_data("project"),
        "data": data,
        "output_data": output_data,
        "execution_state": getattr(context, 'execution_state', 'idle'),  # Issue #1095: Persist for restart re-nudge
    }


def _serialize_agent(agent: Agent):
    # RCA-261: Whitelist specific underscore-prefixed keys that are required
    # for correct profile restoration after deserialization.
    # RCA-354 I-3: Added delegation tracking keys. Without these, crash/restart
    # wipes _subordinate_call_count, causing get_total_delegation_count() to
    # return 0. The essential gate then blocks the orchestrator's final response
    # with "Zero delegations" even though 24+ delegations actually happened.
    # RCA-451: REMOVED _delegation_task_ledger from whitelist. It was a computed
    # view that created a feedback loop with migrate_legacy_ledger(), causing
    # exponential inflation (1M+ entries, 403MB JSON, OOM crash).
    # R1 Fix: Derive whitelist from canonical registry instead of hardcoding.
    # See python/helpers/agent_data_keys.py for the full registry with metadata.
    # RCA-451: _delegation_task_ledger was REMOVED — use _requirements_ledger.
    from python.helpers.agent_data_keys import PERSIST_WHITELIST
    _WHITELIST_UNDERSCORE_KEYS = PERSIST_WHITELIST
    data = {
        k: v for k, v in agent.data.items()
        if not k.startswith("_") or k in _WHITELIST_UNDERSCORE_KEYS
    }

    history = agent.history.to_dict()

    return {
        "number": agent.number,
        "profile": getattr(agent.config, "profile", ""),  # RCA-261: persist per-agent profile
        "data": data,
        "history": history,
    }


def _serialize_log(log: Log):
    if log is None:
        return {"guid": "", "logs": [], "progress": "", "progress_no": 0}

    # F-21 (RCA-358): Redact secrets from each LogItem before serialization
    serialized_logs = []
    for item in log.logs[-LOG_SIZE:]:
        item_data = item.output()
        # Redact content and heading fields where secrets might appear
        if isinstance(item_data.get("content"), str):
            item_data["content"] = redact_known_secrets(item_data["content"])
        if isinstance(item_data.get("heading"), str):
            item_data["heading"] = redact_known_secrets(item_data["heading"])
        serialized_logs.append(item_data)

    data = {
        "guid": log.guid,
        "logs": serialized_logs,
        "progress": log.progress,
        "progress_no": log.progress_no,
    }

    # Lineage Inclusion (#274): Include archived logs if they exist
    if log.context and log.context.id:
        try:
            lineage_path = files.get_abs_path("tmp", "chats", log.context.id, "log_lineage.jsonl")
            if os.path.exists(lineage_path):
                with open(lineage_path, 'r', encoding='utf-8') as f:
                    # Read as JSONL (one JSON object per line)
                    lineage = []
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                lineage.append(json.loads(line))
                            except Exception:
                                pass
                    data["lineage"] = lineage
                    logger.debug(f"Included {len(lineage)} lineage entries in export")
        except Exception as e:
            logger.warning(f"Failed to include lineage in export: {e}")

    return data


def _deserialize_context(data, config: AgentConfig = None):
    if not config:
        config = initialize_agent()
    log = _deserialize_log(data.get("log", None))

    context = AgentContext(
        config=config,
        id=data.get("id", None),  # get new id
        name=data.get("name", None),
        created_at=(
            datetime.fromisoformat(
                # older chats may not have created_at - backcompat
                data.get("created_at", datetime.fromtimestamp(0).isoformat())
            )
        ),
        type=AgentContextType(data.get("type", AgentContextType.USER.value)),
        last_message=(
            datetime.fromisoformat(
                data.get("last_message", datetime.fromtimestamp(0).isoformat())
            )
        ),
        log=log,
        paused=False,
        data=data.get("data", {}),
        output_data=data.get("output_data", {}),
        parent_id=data.get("parent_id", None),
        # agent0=agent0,
        # streaming_agent=straming_agent,
        skip_agent_init=True, # Optimization: Skip default agent creation during deserialization
        skip_version_increment=True, # Optimization: Skip version increment for bulk loading
    )

    agents = data.get("agents", [])
    agent0 = _deserialize_agents(agents, config, context)
    streaming_agent = agent0
    while streaming_agent and streaming_agent.number != data.get("streaming_agent", 0):
        streaming_agent = streaming_agent.data.get(Agent.DATA_NAME_SUBORDINATE, None)

    context.agent0 = agent0
    context.streaming_agent = streaming_agent

    # Restore project name to data if it was saved at top level
    if "project_name" in data and data["project_name"]:
        context.set_data("project", data["project_name"])

    # Issue #1095: Restore execution_state for post-restart re-nudge
    # Without this, execution_state always defaults to "idle" from __init__,
    # causing _post_restart_nudge() to skip mid-execution chats after OOM/crash.
    saved_state = data.get("execution_state", "idle")
    if saved_state in ("executing", "idle"):
        context.execution_state = saved_state

    return context


def _deserialize_agents(
    agents: list[dict[str, Any]], config: AgentConfig, context: AgentContext
) -> Agent:
    import copy

    prev: Agent | None = None
    zero: Agent | None = None

    for ag in agents:
        # RCA-261: Clone config per-agent so each agent has an isolated
        # config object. Without this, all agents share the same config
        # and subordinates lose their profile (e.g., 'code' reverts to
        # 'multiagentdev' after restart).
        agent_config = copy.copy(config)
        saved_profile = ag.get("profile", "")
        if saved_profile:
            agent_config.profile = saved_profile

        current = Agent(
            number=ag["number"],
            config=agent_config,
            context=context,
            skip_init_extensions=True,
            skip_model_loading=True,
        )
        # R1 Fix: Wrap deserialized data in ValidatedAgentData so validation
        # survives persistence roundtrips (load from disk/SQL).
        from python.helpers.agent_data_keys import ValidatedAgentData
        raw_data = ag.get("data", {})
        current.data = ValidatedAgentData(raw_data)
        current.history = history.deserialize_history(
            ag.get("history", ""), agent=current
        )
        if not zero:
            zero = current

        if prev:
            prev.set_data(Agent.DATA_NAME_SUBORDINATE, current)
            current.set_data(Agent.DATA_NAME_SUPERIOR, prev)
        prev = current

    return zero or Agent(0, config, context)


# def _deserialize_history(history: list[dict[str, Any]]):
#     result = []
#     for hist in history:
#         content = hist.get("content", "")
#         msg = (
#             HumanMessage(content=content)
#             if hist.get("type") == "human"
#             else AIMessage(content=content)
#         )
#         result.append(msg)
#     return result


def _deserialize_log(data: dict[str, Any]) -> "Log":
    log = Log()
    log.guid = data.get("guid", str(uuid.uuid4()))
    log.set_initial_progress()

    # Deserialize the list of LogItem objects
    i = 0
    for item_data in data.get("logs", []):
        item = LogItem(
            log=log,  # restore the log reference
            no=i,  # item_data["no"],
            type=item_data["type"],
            heading=item_data.get("heading", ""),
            content=item_data.get("content", ""),
            kvps=OrderedDict(item_data.get("kvps")) if item_data.get("kvps") else None,
            temp=item_data.get("temp", False),
            protected=item_data.get("protected", False),
            id=item_data.get("id", None),
            timestamp=item_data.get("timestamp", 0.0),
            # --- Fields previously missing (Forgejo #1019) ---
            completion=item_data.get("completion", False),
            icon=item_data.get("icon", ""),
            sender_type=item_data.get("sender_type", ""),
            sender_id=item_data.get("sender_id", ""),
            verbose=item_data.get("verbose", False),
        )
        # Restore seq_id from serialized data if present (overrides auto-generated)
        if "seq_id" in item_data and item_data["seq_id"]:
            item.seq_id = item_data["seq_id"]
        log.logs.append(item)
        log.updates.append(i)
        i += 1

    return log


def _sanitize_surrogates(text):
    """Replace unpaired UTF-16 surrogates with the Unicode replacement character.

    LLM output and tool responses can contain unpaired surrogates (U+D800–U+DFFF)
    which are invalid in UTF-8. This causes 'utf-8 codec can't encode surrogates'
    errors in json.dumps and SQLite persistence, permanently losing chat history.

    5-Why RCA (F-3, MSR_Smoke_1777847233): 69 occurrences of this error in a
    single smoke test run. Root cause: no sanitization before JSON serialization.
    """
    if isinstance(text, str):
        return text.encode('utf-8', errors='surrogatepass').decode('utf-8', errors='replace')
    return text


def _sanitize_deep(obj):
    """Recursively sanitize all strings in a nested dict/list structure.

    Walks dicts, lists, and tuples, applying _sanitize_surrogates to every
    string value. Non-string leaves pass through unchanged.
    """
    if isinstance(obj, str):
        return _sanitize_surrogates(obj)
    elif isinstance(obj, dict):
        return {k: _sanitize_deep(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        sanitized = [_sanitize_deep(item) for item in obj]
        return type(obj)(sanitized) if isinstance(obj, tuple) else sanitized
    return obj


def _safe_json_serialize(obj, **kwargs):
    # F-3: Sanitize surrogates before serialization to prevent UnicodeEncodeError
    obj = _sanitize_deep(obj)

    def serializer(o):
        if isinstance(o, dict):
            return {k: v for k, v in o.items() if is_json_serializable(v)}
        elif isinstance(o, (list, tuple)):
            return [item for item in o if is_json_serializable(item)]
        elif is_json_serializable(o):
            return o
        else:
            return None  # Skip this property

    def is_json_serializable(item):
        try:
            json.dumps(item)
            return True
        except (TypeError, OverflowError):
            return False

    return json.dumps(obj, default=serializer, **kwargs)


def serialize_and_redact(data: dict, project_name: str = "") -> str:
    """Centralized serialize-and-redact boundary.

    RCA-M1: Every byte that leaves memory for disk or network MUST pass
    through this function. Combines _safe_json_serialize + redact_known_secrets
    into a single atomic operation so no serialization path can forget redaction.

    Args:
        data: The dict to serialize (from _serialize_context or similar).
        project_name: Optional project name for project-scoped redaction.

    Returns:
        JSON string with all known secrets redacted.
    """
    js = _safe_json_serialize(data, ensure_ascii=False)
    return redact_known_secrets(js, project_name=project_name)


# =============================================================================
# Write-Ahead Logging (WAL) for Crash-Safe Persistence
# =============================================================================

def save_tmp_chat_with_wal(context: AgentContext) -> bool:
    """
    Save context with Write-Ahead Logging for crash safety.
    
    Process:
    1. Write to WAL file first (ensures data is on disk)
    2. Create backup of existing main file (if exists)
    3. Atomically move WAL to main file
    4. Remove backup on success
    
    Returns:
        True if save succeeded, False otherwise
    """
    # Skip saving if context has been explicitly removed
    if context.id in REMOVED_CONTEXTS:
        logger.debug(f"Skipping WAL save for removed context {context.id}")
        return True

    # Skip saving BACKGROUND contexts as they should be ephemeral
    if context.type == AgentContextType.BACKGROUND:
        return True

    try:
        chat_dir = get_chat_folder_path(context.id)
        main_path = _get_chat_file_path(context.id)
        wal_path = main_path + WAL_EXTENSION
        backup_path = main_path + BACKUP_EXTENSION

        # Ensure directory exists
        files.make_dirs(main_path)

        # Step 1: Serialize and write to WAL
        data = _serialize_context(context)
        # M-1 Fix: Use centralized serialize_and_redact to prevent secret leaks
        project_name = context.get_data("project") or ""
        js = serialize_and_redact(data, project_name=project_name)
        if not js or js.strip() in ["", "{}", "[]"]:
            logger.warning(f"Skipping WAL save for context {context.id}: Serialization returned empty/minimal data.")
            return True
        files.write_file(wal_path, js)
        logger.debug(f"WAL written for context {context.id}")

        # Step 2: Backup existing file (if exists)
        if os.path.exists(main_path):
            try:
                shutil.copy2(main_path, backup_path)
            except Exception as e:
                logger.warning(f"Failed to create backup: {e}")

        # Step 3: Atomic move WAL to main
        shutil.move(wal_path, main_path)
        logger.debug(f"WAL committed for context {context.id}")

        # Step 4: Remove backup on success
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except Exception:
                pass  # Non-critical

        return True

    except Exception as e:
        logger.error(f"Failed to save context {context.id} with WAL: {e}")
        
        # Attempt rollback
        try:
            if os.path.exists(backup_path) and not os.path.exists(main_path):
                shutil.move(backup_path, main_path)
                logger.info(f"Rolled back to backup for context {context.id}")
        except Exception as re:
            logger.error(f"Rollback failed: {re}")
            
        return False


def recover_wal_files() -> int:
    """
    Recover any orphaned WAL files on startup.
    
    This should be called during application startup to recover
    any contexts that crashed during save.
    
    Returns:
        Number of WAL files recovered
    """
    recovered = 0
    
    try:
        chats_path = files.get_abs_path(CHATS_FOLDER)
        if not os.path.exists(chats_path):
            return 0
            
        # Find all WAL files
        for root, dirs, filenames in os.walk(chats_path):
            for filename in filenames:
                if filename.endswith(WAL_EXTENSION):
                    wal_path = os.path.join(root, filename)
                    main_path = wal_path[:-len(WAL_EXTENSION)]
                    
                    try:
                        if _recover_single_wal(wal_path, main_path):
                            recovered += 1
                    except Exception as e:
                        logger.error(f"Failed to recover WAL {wal_path}: {e}")
                        
        if recovered > 0:
            logger.info(f"Recovered {recovered} WAL files on startup")
            
    except Exception as e:
        logger.error(f"WAL recovery scan failed: {e}")
        
    return recovered


def _recover_single_wal(wal_path: str, main_path: str) -> bool:
    """
    Recover a single WAL file.
    
    Logic:
    - If main doesn't exist: commit WAL
    - If both exist: prefer newer file
    - Clean up WAL after recovery
    """
    if not os.path.exists(wal_path):
        return False
        
    if not os.path.exists(main_path):
        # WAL exists but main doesn't - commit WAL
        shutil.move(wal_path, main_path)
        logger.info(f"Recovered WAL to {main_path}")
        return True
        
    # Both exist - compare timestamps
    wal_time = os.path.getmtime(wal_path)
    main_time = os.path.getmtime(main_path)
    
    if wal_time > main_time:
        # WAL is newer - use it
        backup_path = main_path + BACKUP_EXTENSION
        shutil.copy2(main_path, backup_path)
        shutil.move(wal_path, main_path)
        logger.info(f"Recovered newer WAL to {main_path}")
        return True
    else:
        # Main is newer - discard WAL
        os.remove(wal_path)
        logger.debug(f"Discarded stale WAL: {wal_path}")
        return True


def cleanup_old_backups(max_age_hours: int = 24) -> int:
    """
    Clean up old backup files.
    
    Args:
        max_age_hours: Maximum age of backup files to keep
        
    Returns:
        Number of backup files removed
    """
    import time
    
    cleaned = 0
    max_age_seconds = max_age_hours * 3600
    current_time = time.time()
    
    try:
        chats_path = files.get_abs_path(CHATS_FOLDER)
        if not os.path.exists(chats_path):
            return 0
            
        for root, dirs, filenames in os.walk(chats_path):
            for filename in filenames:
                if filename.endswith(BACKUP_EXTENSION):
                    backup_path = os.path.join(root, filename)
                    file_age = current_time - os.path.getmtime(backup_path)
                    
                    if file_age > max_age_seconds:
                        try:
                            os.remove(backup_path)
                            cleaned += 1
                        except Exception:
                            pass
                            
        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} old backup files")
            
    except Exception as e:
        logger.error(f"Backup cleanup failed: {e}")
        
    return cleaned
