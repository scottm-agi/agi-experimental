from __future__ import annotations
import os
import asyncio
import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from python.helpers import files
from python.helpers.database_client import DatabaseClient
from python.helpers.models_sql import ContextSQL, AgentSQL, MessageSQL, LogSQL, LogItemSQL, SharedMemorySQL
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

logger = logging.getLogger("agix.persistence_manager")
print("PERSISTENCE_MANAGER_MODULE_LOADED", flush=True)

class StorageScope(Enum):
    GLOBAL = "global"   # ~/.agix
    PROJECT = "project" # .agix/ in project dir
    LOCAL = "local"     # project root (legacy/shared)

class PersistenceManager:
    """
    Manages data persistence across different scopes.
    Ensures that data is stored in appropriate locations based on its scope (JSON or SQL).
    """
    
    _instances: Dict[int, 'PersistenceManager'] = {}
    _global_dir = None
    _project_dir = None
    
    @classmethod
    def get_instance(cls, global_dir: Optional[str] = None, project_dir: Optional[str] = None):
        """Get or create loop-local singleton instance of PersistenceManager."""
        try:
            loop = asyncio.get_running_loop()
            loop_id = id(loop)
        except RuntimeError:
            loop_id = 0
            
        if loop_id not in cls._instances:
            cls._instances[loop_id] = cls(global_dir, project_dir)
        return cls._instances[loop_id]
    
    def __init__(self, global_dir: Optional[str] = None, project_dir: Optional[str] = None):
        # Resolve global directory (~/.agix by default)
        if global_dir:
            self.global_dir = Path(global_dir)
        else:
            self.global_dir = Path.home() / ".agix"
            
        # Resolve project directory (project root by default)
        if project_dir:
            self.project_dir = Path(project_dir)
        else:
            self.project_dir = Path(files.get_base_dir())
            
        # Create directories if they don't exist
        os.makedirs(self.global_dir, exist_ok=True)
        os.makedirs(self.project_dir / ".agix", exist_ok=True)
        
        # Initialize Database Client
        self.db = DatabaseClient.get_instance()
        self._db_initialized = False
        self._db_lock = asyncio.Lock()
        
        logger.info(f"PersistenceManager initialized. Global: {self.global_dir}, Project: {self.project_dir}")

    async def _ensure_db(self):
        """Ensure database schema is initialized with proper synchronization."""
        if not self._db_initialized:
            async with self._db_lock:
                # Double-check inside lock
                if not self._db_initialized:
                    await self.db.init_db()
                    self._db_initialized = True

    @staticmethod
    def _sanitize_for_sql(obj, visited=None, depth=0, max_depth=100):
        """
        Deeply sanitize any object for JSON storage in SQL.
        Handles datetimes, enums, circular refs, and nested structures.
        Includes recursion depth protection.
        """
        from datetime import datetime
        from enum import Enum
        
        if visited is None: visited = set()
        
        # Basic types that are natively JSON serializable
        if obj is None or isinstance(obj, (bool, int, float)):
            return obj
        if isinstance(obj, str):
            # Strip Unicode surrogates (\ud800-\udfff) that crash UTF-8 encoding
            # in SQLite/PostgreSQL. These can appear in MCP tool responses.
            try:
                obj.encode('utf-8')
                return obj  # Fast path: no surrogates, return as-is
            except UnicodeEncodeError:
                # Use 'replace' to substitute surrogates with \ufffd (standard replacement char).
                # This produces valid UTF-8 safe for all downstream consumers (SQL, JSON, APIs).
                # NOTE: Do NOT use 'surrogatepass' here — it produces CESU-8 bytes (\xed\xa0\x80)
                # that are technically encodable but crash aiosqlite/SQLAlchemy on write.
                return obj.encode('utf-8', errors='replace').decode('utf-8')

        # Recursion depth protection
        if depth > max_depth:
            return f"<Max Recursion Depth Exceeded at depth {depth}>"

        obj_id = id(obj)
        if obj_id in visited:
            return f"<Circular Reference {obj_id}>"

        # Handle common non-serializable objects
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        
        # Track complex objects to prevent circularity
        is_complex = isinstance(obj, (dict, list, tuple, set)) or hasattr(obj, "__dict__") or hasattr(obj, "to_dict")
        if is_complex:
            visited.add(obj_id)

        try:
            # Handle lists/tuples/sets
            if isinstance(obj, (list, tuple, set)):
                return [PersistenceManager._sanitize_for_sql(item, visited, depth + 1, max_depth) for item in obj]

            # Handle dictionaries and dict-like objects
            if isinstance(obj, dict) or hasattr(obj, "items"):
                try:
                    # Convert mappingproxy or others to dict
                    it = obj.items() if hasattr(obj, "items") else obj.items()
                    clean = {}
                    for k, v in it:
                        clean[str(k)] = PersistenceManager._sanitize_for_sql(v, visited, depth + 1, max_depth)
                    return clean
                except Exception:
                    pass

            # Handle Pydantic models (v2)
            if hasattr(obj, "model_dump"):
                try:
                    return PersistenceManager._sanitize_for_sql(obj.model_dump(), visited, depth + 1, max_depth)
                except Exception:
                    pass

            # Handle objects with custom serialization or __dict__
            if hasattr(obj, "to_dict"):
                try:
                    return PersistenceManager._sanitize_for_sql(obj.to_dict(), visited, depth + 1, max_depth)
                except Exception:
                    pass
            
            if hasattr(obj, "__dict__"):
                try:
                    return PersistenceManager._sanitize_for_sql(obj.__dict__, visited, depth + 1, max_depth)
                except Exception:
                    pass

            # Fallback to string representation
            return str(obj)
        finally:
            # Remove from visited when returning from recursion to allow same object in different branches
            if is_complex:
                visited.remove(obj_id)

    # ==========================================================================
    # FILE-BASED JSON PERSISTENCE (LEGACY/FALLBACK)
    # ==========================================================================

    def get_path(self, relative_path: str, scope: StorageScope = StorageScope.PROJECT) -> str:
        """
        Resolve a relative path based on the specified scope.
        """
        if scope == StorageScope.GLOBAL:
            return str(self.global_dir / relative_path)
        elif scope == StorageScope.PROJECT:
            return str(self.project_dir / ".agix" / relative_path)
        elif scope == StorageScope.LOCAL:
            return str(self.project_dir / relative_path)
        else:
            raise ValueError(f"Invalid storage scope: {scope}")

    def save_json(self, relative_path: str, data: Any, scope: StorageScope = StorageScope.PROJECT):
        """Save data as JSON to the specified scope (atomic write)."""
        full_path = self.get_path(relative_path, scope)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        files.save_json_atomic(full_path, data)
        logger.debug(f"Saved JSON to {full_path}")

    def load_json(self, relative_path: str, scope: StorageScope = StorageScope.PROJECT) -> Optional[Any]:
        """Load JSON data from the specified scope."""
        full_path = self.get_path(relative_path, scope)
        if not os.path.exists(full_path):
            return None
        with open(full_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    # ==========================================================================
    # SQL-BASED PERSISTENCE (PRIMARY)
    # ==========================================================================

    async def save_context_sql(self, context_data: Dict[str, Any]):
        """
        Save an agent context and its full graph to the SQL database atomically.
        
        Args:
            context_data: Serialized context dictionary
        """
        await self._ensure_db()
        ctx_id = context_data.get("id", "unknown")
        try:
            sanitized_data = await asyncio.to_thread(self._prepare_sanitized_data, context_data)
            
            # UNIFIED ATOMIC TRANSACTION
            # We use a single session and transaction for the entire graph.
            # This ensures that either the entire context saves, or nothing does.
            async with self.db.get_session() as session:
                async with session.begin():
                    # 1. Update or create Context
                    stmt = select(ContextSQL).where(ContextSQL.id == ctx_id)
                    result = await session.execute(stmt)
                    ctx_row = result.scalar_one_or_none()
                    
                    if not ctx_row:
                        ctx_row = ContextSQL(id=ctx_id)
                        session.add(ctx_row)
                    
                    ctx_row.name = context_data.get("name")
                    ctx_row.type = context_data.get("type", "user")
                    ctx_row.parent_id = context_data.get("parent_id")
                    ctx_row.project_name = context_data.get("project_name")
                    
                    if "created_at" in context_data:
                        dt = datetime.fromisoformat(context_data["created_at"])
                        if dt.tzinfo is not None:
                            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                        ctx_row.created_at = dt
                    if "last_message" in context_data:
                        dt = datetime.fromisoformat(context_data["last_message"])
                        if dt.tzinfo is not None:
                            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                        ctx_row.last_message = dt
                    
                    ctx_row.data = sanitized_data["context_data"]
                    ctx_row.output_data = sanitized_data["output_data"]
                    
                    # 2. Update Log Header
                    log_sql_id = None
                    if sanitized_data["log"]:
                        log_sql = sanitized_data["log"]
                        stmt = select(LogSQL).where(LogSQL.context_id == ctx_id)
                        result = await session.execute(stmt)
                        existing_log = result.scalar_one_or_none()
                        
                        if existing_log:
                            existing_log.guid = log_sql.guid
                            existing_log.progress = log_sql.progress
                            existing_log.progress_no = log_sql.progress_no
                            log_sql_id = existing_log.id
                        else:
                            session.add(log_sql)
                            await session.flush() # Get log_sql_id
                            log_sql_id = log_sql.id

                    # 3. Agents & Messages
                    if sanitized_data["agents"]:
                        for ag_sql in sanitized_data["agents"]:
                            ag_sql.context_id = ctx_id
                            
                            # Upsert agent
                            stmt = select(AgentSQL).options(selectinload(AgentSQL.messages)).where(AgentSQL.context_id == ctx_id, AgentSQL.number == ag_sql.number).limit(1)
                            result = await session.execute(stmt)
                            existing_ag = result.scalar_one_or_none()
                            
                            if existing_ag:
                                existing_ag.data = ag_sql.data
                                existing_ag.messages = []
                                for msg in ag_sql.messages:
                                    new_msg = MessageSQL(
                                        agent_id=existing_ag.id,
                                        ai=msg.ai,
                                        content=msg.content,
                                        summary=msg.summary,
                                        tokens=msg.tokens,
                                        created_at=msg.created_at
                                    )
                                    session.add(new_msg)
                                    existing_ag.messages.append(new_msg)
                            else:
                                session.add(ag_sql)

                    # 4. Log Items (Telemetry)
                    if log_sql_id and sanitized_data["log_items"]:
                        # Inline cleanup: Delete old items to maintain pruning logic
                        await session.execute(delete(LogItemSQL).where(LogItemSQL.log_id == log_sql_id))
                        for item_sql in sanitized_data["log_items"]:
                            item_sql.log_id = log_sql_id
                            session.add(item_sql)

            logger.info(f"Context {ctx_id} saved atomically to unified database")

        except Exception as e:
            import sys
            import traceback
            print(f"CRITICAL ERROR in save_context_sql for {ctx_id}: {str(e)}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
            logger.error(f"Failed to save context {ctx_id} to SQL: {str(e)}")
            raise e # Propagation for transaction rollback awareness

    @staticmethod
    def _flatten_history_messages(history: Any) -> List[Dict[str, Any]]:
        """
        Extract a flat list of message dictionaries from a serialized history object.
        Supports History -> Bulk -> Topic -> Message structure.
        """
        if not history:
            return []
            
        if isinstance(history, str):
            try:
                history = json.loads(history)
            except Exception:
                return []  # Corrupted history JSON — return empty
                
        messages = []
        if not isinstance(history, dict):
            return []
            
        # 1. From Bulks
        for bulk in history.get("bulks", []):
            for record in bulk.get("records", []):
                if record.get("_cls") == "Topic":
                    messages.extend(record.get("messages", []))
                elif record.get("_cls") == "Message":
                    messages.append(record)
                    
        # 2. From Topics
        for topic in history.get("topics", []):
            messages.extend(topic.get("messages", []))
            
        # 3. From Current topic
        current = history.get("current")
        if current:
            messages.extend(current.get("messages", []))
            
        return messages

    def _prepare_sanitized_data(self, context_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform heavy sanitization and object preparation in a separate thread.
        """
        ctx_id = context_data["id"]
        
        # Truncation limits to prevent DB bloat/CPU spikes
        MAX_CONTENT_LEN = 51200 # 50 KB for tool outputs
        MAX_HISTORY_LEN = 204800 # 200 KB for agent history
        
        def _truncate_if_needed(val: Any, limit: int = MAX_CONTENT_LEN) -> Any:
            if isinstance(val, str) and len(val) > limit:
                return val[:limit] + f"\n\n[TRUNCATED: Content exceeded {limit/1024:.0f}KB]"
            return val
        
        # Sanitize top-level data
        clean_ctx_data = PersistenceManager._sanitize_for_sql(context_data.get("data", {}))
        clean_output_data = PersistenceManager._sanitize_for_sql(context_data.get("output_data", []))
        
        # Prepare Agents
        agents_sql = []
        for ag_data in context_data.get("agents", []):
            history_blob = ag_data.get("history", "[]")
            # Truncation removed as we are now using PostgreSQL for high-volume state

            clean_ag_data = PersistenceManager._sanitize_for_sql(ag_data.get("data", {}))
            if not isinstance(clean_ag_data, dict):
                clean_ag_data = {"data": clean_ag_data}
            
            # Store history_blob inside the data blob
            # RCA-306d: Sanitize history_blob to strip surrogates that crash UTF-8 encoding.
            # Previously this was inserted raw, bypassing _sanitize_for_sql.
            clean_ag_data["history_blob"] = PersistenceManager._sanitize_for_sql(history_blob)
            
            # Prepare messages (if any)
            messages_sql = []
            
            # Extract messages from history if available (Issue #377 Fix)
            messages_data = ag_data.get("messages", [])
            history_src = ag_data.get("history")
            
            if not messages_data and history_src:
                logger.debug(f"[DEBUG] History Source for Agent {ag_data.get('number')}: type={type(history_src)}, keys={list(history_src.keys()) if isinstance(history_src, dict) else 'N/A'}")
                messages_data = self._flatten_history_messages(history_src)
                logger.debug(f"[DEBUG] Extracted {len(messages_data)} messages from history for agent {ag_data.get('number')}")
            elif messages_data:
                logger.debug(f"[DEBUG] Found {len(messages_data)} direct messages for agent {ag_data.get('number')}")
            else:
                logger.debug(f"[DEBUG] No messages or history found for agent {ag_data.get('number')}. Keys: {list(ag_data.keys())}")

            for msg_data in messages_data:
                # RCA-307b: Sanitize message content to strip surrogates that crash
                # UTF-8 encoding in SQL. Previously only history_blob was sanitized.
                msg_sql = MessageSQL(
                    ai=msg_data.get("ai", False),
                    content=PersistenceManager._sanitize_for_sql(msg_data.get("content")),
                    summary=PersistenceManager._sanitize_for_sql(msg_data.get("summary")),
                    tokens=msg_data.get("tokens", 0)
                )
                if "created_at" in msg_data:
                    try:
                        msg_sql.created_at = datetime.fromisoformat(msg_data["created_at"])
                    except Exception:
                        pass
                messages_sql.append(msg_sql)

            agent_sql = AgentSQL(
                context_id=ctx_id,
                number=ag_data.get("number", 0),
                data=clean_ag_data,
                messages=messages_sql
            )
            agents_sql.append(agent_sql)
            
        # Prepare Logs
        log_sql = None
        log_items_sql = []
        log_data = context_data.get("log")
        if log_data:
            log_sql = LogSQL(
                context_id=ctx_id,
                guid=log_data.get("guid"),
                progress=log_data.get("progress"),
                progress_no=log_data.get("progress_no", 0)
            )
            
            for item_data in log_data.get('logs', []):
                # Apply high-limit truncation for safety (System Hardening)
                raw_type = item_data.get("type", "info")
                clean_type = _truncate_if_needed(raw_type, limit=2048) # Type should still be reasonably sized
                
                raw_heading = item_data.get("heading")
                clean_heading = PersistenceManager._sanitize_for_sql(_truncate_if_needed(raw_heading, limit=MAX_CONTENT_LEN))
                
                raw_content = item_data.get("content")
                clean_content = PersistenceManager._sanitize_for_sql(_truncate_if_needed(raw_content))
                
                raw_kvps = item_data.get("kvps")
                clean_kvps = PersistenceManager._sanitize_for_sql(raw_kvps)
                if isinstance(clean_kvps, dict):
                    for k, v in clean_kvps.items():
                        clean_kvps[k] = _truncate_if_needed(v)

                item_sql = LogItemSQL(
                    no=item_data.get("no", 0),
                    type=clean_type,
                    heading=clean_heading,
                    content=clean_content,
                    kvps=clean_kvps,
                    temp=item_data.get("temp", False)
                )
                log_items_sql.append(item_sql)
                
        return {
            "context_data": clean_ctx_data,
            "output_data": clean_output_data,
            "agents": agents_sql,
            "log": log_sql,
            "log_items": log_items_sql
        }

    async def load_context_sql(self, context_id: str) -> Optional[Dict[str, Any]]:
        """
        Load an agent context from SQL and reconstruct the serialized dictionary.
        """
        await self._ensure_db()
        async with self.db.get_session(target="core") as session:
            stmt = (
                select(ContextSQL)
                .where(ContextSQL.id == context_id)
                .options(
                    selectinload(ContextSQL.logs)
                )
            )
            result = await session.execute(stmt)
            ctx_row = result.scalar_one_or_none()
            
            if not ctx_row:
                return None
            
            data = {
                "id": ctx_row.id,
                "name": ctx_row.name,
                "created_at": ctx_row.created_at.isoformat(),
                "type": ctx_row.type,
                "last_message": ctx_row.last_message.isoformat(),
                "parent_id": ctx_row.parent_id,
                "project_name": ctx_row.project_name,
                "data": ctx_row.data,
                "output_data": ctx_row.output_data,
                "agents": [],
                "log": None
            }
            
            # Fetch Agents and Messages from logs engine (Hybrid)
            async with self.db.get_session(target="logs") as logs_session:
                agents_stmt = select(AgentSQL).where(AgentSQL.context_id == context_id).options(selectinload(AgentSQL.messages))
                agents_result = await logs_session.execute(agents_stmt)
                
                for ag_sql in agents_result.scalars():
                    ag_dict = {
                        "number": ag_sql.number,
                        "data": ag_sql.data,
                        "history": ag_sql.data.get("history_blob", "[]"),
                        "messages": []
                    }
                    for msg_sql in ag_sql.messages:
                        ag_dict["messages"].append({
                            "ai": msg_sql.ai,
                            "content": msg_sql.content,
                            "summary": msg_sql.summary,
                            "tokens": msg_sql.tokens,
                            "created_at": msg_sql.created_at.isoformat()
                        })
                    data["agents"].append(ag_dict)
            
            if ctx_row.logs:
                log_sql = ctx_row.logs[0]
                log_dict = {
                    "guid": log_sql.guid,
                    "progress": log_sql.progress,
                    "progress_no": log_sql.progress_no,
                    "logs": []
                }
                
                # Fetch items from logs engine (with limit to prevent overhead)
                async with self.db.get_session(target="logs") as logs_session:
                    # By default, we only load the last 500 logs for the UI/Display
                    # This prevents the 1.03GB NET I/O seen in heavy parallel tests
                    items_stmt = select(LogItemSQL).where(LogItemSQL.log_id == log_sql.id).order_by(LogItemSQL.no.desc()).limit(500)
                    items_result = await logs_session.execute(items_stmt)
                    
                    log_items = list(items_result.scalars())
                    log_items.reverse() # Back to chronological for UI
                    
                    for item_sql in log_items:
                        log_dict["logs"].append({
                            "no": item_sql.no,
                            "type": item_sql.type,
                            "heading": item_sql.heading,
                            "content": item_sql.content,
                            "kvps": item_sql.kvps,
                            "temp": item_sql.temp
                        })
                data["log"] = log_dict
                
            return data

    async def delete_context_sql(self, context_id: str):
        """Delete an agent context and all its related data (cross-engine).
        
        Uses retry logic with exponential backoff to handle SQLite 'database is locked'
        errors that occur during rapid concurrent delete operations (#589).
        Optimized to use fewer sessions and atomic transactions where possible.
        """
        await self._ensure_db()
        max_retries = 5 # Increased retries
        
        for attempt in range(max_retries):
            try:
                # 1. ATOMIC CORE CLEANUP (includes gathering log IDs)
                # We do this in a single session to reduce file locks
                log_ids = []
                async with self.db.get_session(target="core") as session:
                    async with session.begin():
                        # Gather log IDs first
                        stmt = select(LogSQL.id).where(LogSQL.context_id == context_id)
                        res = await session.execute(stmt)
                        log_ids = [r for r in res.scalars()]
                        
                        # Cleanup Core tables
                        await session.execute(delete(AgentSQL).where(AgentSQL.context_id == context_id))
                        await session.execute(delete(LogSQL).where(LogSQL.context_id == context_id))
                        await session.execute(delete(ContextSQL).where(ContextSQL.id == context_id))

                # 2. CLEANUP LOG ITEMS (Hybrid target)
                if log_ids:
                    async with self.db.get_session(target="logs") as logs_session:
                        async with logs_session.begin():
                            await logs_session.execute(delete(LogItemSQL).where(LogItemSQL.log_id.in_(log_ids)))

                logger.info(f"Context {context_id} fully deleted from database")
                return
            except Exception as e:
                err_str = str(e).lower()
                if attempt < max_retries - 1 and ("locked" in err_str or "busy" in err_str):
                    wait = 0.2 * (2 ** attempt) # Faster initial retries
                    logger.warning(f"Delete context {context_id} hit DB lock, retry {attempt + 1}/{max_retries} in {wait:.1f}s...")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Failed to delete context {context_id} after {attempt + 1} attempts: {e}")
                    raise e

    async def delete_contexts_by_project(self, project_name: str):
        """Delete ALL contexts (and related data) associated with a project.
        
        This is called during project deletion to cascade-clean SQL entries
        for contexts that may have been evicted from memory but still exist
        in the database. Fixes issue #838/#911.
        
        Args:
            project_name: The project name to match against contexts.project_name
        """
        await self._ensure_db()
        try:
            # 1. Find all context IDs for this project
            async with self.db.get_session(target="core") as session:
                stmt = select(ContextSQL.id).where(ContextSQL.project_name == project_name)
                result = await session.execute(stmt)
                context_ids = [r for r in result.scalars()]
            
            if not context_ids:
                logger.debug(f"No SQL contexts found for project '{project_name}'")
                return
            
            logger.info(f"Cascade-deleting {len(context_ids)} SQL contexts for project '{project_name}'")
            
            # 2. Delete each context using existing method (handles all related tables)
            for ctx_id in context_ids:
                try:
                    await self.delete_context_sql(ctx_id)
                except Exception as e:
                    logger.warning(f"Failed to cascade-delete context {ctx_id} for project '{project_name}': {e}")
            
            logger.info(f"Cascade delete complete for project '{project_name}': {len(context_ids)} contexts removed")
        except Exception as e:
            logger.error(f"Failed to cascade-delete contexts for project '{project_name}': {e}")

    async def get_active_project_names(self) -> set:
        """
        Get all distinct project names from the contexts table.
        Used by stale project cleanup to identify which filesystem
        project directories still have associated chat contexts.
        
        Returns:
            Set of project name strings that have at least one context.
        """
        await self._ensure_db()
        from sqlalchemy import distinct
        async with self.db.get_session(target="core") as session:
            stmt = select(distinct(ContextSQL.project_name)).where(
                ContextSQL.project_name.isnot(None),
                ContextSQL.project_name != ""
            )
            result = await session.execute(stmt)
            return {row for row in result.scalars()}

    async def save_shared_memory_sql(self, entry_dict: Dict[str, Any], project_name: Optional[str] = None):
        """Save a shared memory entry to the SQL database."""
        await self._ensure_db()
        async with self.db.get_session() as session:
            async with session.begin():
                mem_id = entry_dict["id"]
                stmt = select(SharedMemorySQL).where(SharedMemorySQL.id == mem_id)
                result = await session.execute(stmt)
                mem_row = result.scalar_one_or_none()
                
                if not mem_row:
                    mem_row = SharedMemorySQL(id=mem_id)
                    session.add(mem_row)
                
                mem_row.agent_id = entry_dict.get("agent_id")
                mem_row.content = entry_dict.get("content")
                mem_row.version = entry_dict.get("version", 1)
                mem_row.meta_data = entry_dict.get("metadata", {})
                mem_row.project_name = project_name or mem_row.meta_data.get("project")
                
                if "created_at" in entry_dict:
                    mem_row.created_at = datetime.fromisoformat(entry_dict["created_at"])
                if "updated_at" in entry_dict:
                    mem_row.updated_at = datetime.fromisoformat(entry_dict["updated_at"])
                    
        logger.info(f"Shared memory {mem_id} saved to SQL database")

    def list_files(self, relative_dir: str, scope: StorageScope = StorageScope.PROJECT) -> List[str]:
        """List files in a directory within the specified scope."""
        full_dir = self.get_path(relative_dir, scope)
        if not os.path.exists(full_dir) or not os.path.isdir(full_dir):
            return []
        return os.listdir(full_dir)

    def delete(self, relative_path: str, scope: StorageScope = StorageScope.PROJECT):
        """Delete a file or directory within the specified scope."""
        full_path = self.get_path(relative_path, scope)
        if not os.path.exists(full_path):
            return
        if os.path.isdir(full_path):
            files.delete_dir(full_path)
        else:
            os.remove(full_path)
        logger.debug(f"Deleted {full_path}")
