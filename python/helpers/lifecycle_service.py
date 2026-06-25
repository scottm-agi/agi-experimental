"""
Centralized lifecycle management for AGIX entities.

All CRUD operations for chats, projects, and tasks route through these
shared primitives to ensure consistent cleanup across memory, disk, and SQL.

Usage:
    from python.helpers.lifecycle_service import LifecycleService

    await LifecycleService.delete_chat(context_id)
    await LifecycleService.delete_project(project_name)
"""
from __future__ import annotations

import logging
import os
import shutil

from python.helpers import files

logger = logging.getLogger("agix.lifecycle_service")

CHATS_FOLDER = "tmp/chats"


class LifecycleService:
    """Shared CRUD primitives for entity lifecycle management."""

    # ================================================================
    # STARTUP GC — clean up orphan artifacts
    # ================================================================

    @staticmethod
    def gc_empty_chats(chats_dir: str | None = None) -> list[str]:
        """Remove chat directories that have a 0-byte chat.json file.
        
        These orphans are created by crashed/aborted chat sessions and cause
        startup warnings in persist_chat.py.
        
        Returns list of removed directory names.
        """
        chats_dir = chats_dir or files.get_abs_path(CHATS_FOLDER)
        removed: list[str] = []
        
        if not os.path.isdir(chats_dir):
            return removed
        
        for entry in os.listdir(chats_dir):
            chat_path = os.path.join(chats_dir, entry)
            if not os.path.isdir(chat_path):
                continue
            
            json_path = os.path.join(chat_path, "chat.json")
            if os.path.exists(json_path) and os.path.getsize(json_path) == 0:
                try:
                    if files.delete_dir(chat_path):
                        removed.append(entry)
                        logger.info(f"[gc_empty_chats] Removed orphan: {entry}")
                    else:
                        logger.warning(f"[gc_empty_chats] Failed to remove {entry} after retries")
                except Exception as e:
                    logger.warning(f"[gc_empty_chats] Failed to remove {entry}: {e}")
        
        if removed:
            logger.info(f"[gc_empty_chats] Cleaned up {len(removed)} orphan chat dirs")
        
        return removed

    # ================================================================
    # PRIMITIVE OPERATIONS — used by all composite operations below
    # ================================================================

    @staticmethod
    async def delete_memory(context_id: str) -> bool:
        """Remove context from in-memory registry + add to REMOVED_CONTEXTS.
        
        Returns True if the context was found and removed, False if not found.
        """
        from python.agent import AgentContext
        try:
            context = AgentContext.use(context_id)
            if context:
                context.reset()
            AgentContext.remove(context_id)
            logger.info(f"[delete_memory] Context {context_id} removed from memory")
            return True
        except Exception as e:
            logger.warning(f"[delete_memory] Context {context_id}: {e}")
            return False

    @staticmethod
    def delete_disk(context_id: str) -> bool:
        """Remove context folder from tmp/chats/.
        
        Returns True if the folder was found and deleted, False if not found.
        """
        from python.helpers import persist_chat
        try:
            persist_chat.remove_chat(context_id)
            folder = files.get_abs_path(CHATS_FOLDER, context_id)
            gone = not os.path.exists(folder)
            if gone:
                logger.info(f"[delete_disk] Context folder {context_id} deleted")
            else:
                logger.warning(f"[delete_disk] Context folder {context_id} still exists after deletion!")
            return gone
        except Exception as e:
            logger.warning(f"[delete_disk] Context {context_id}: {e}")
            return False

    @staticmethod
    async def delete_sql(context_id: str) -> bool:
        """Remove context + all related rows from SQL database.
        
        Returns True if deletion succeeded, False on error.
        """
        try:
            from python.helpers.persistence_manager import PersistenceManager
            pm = PersistenceManager.get_instance()
            await pm.delete_context_sql(context_id)
            logger.info(f"[delete_sql] Context {context_id} deleted from SQL")
            return True
        except Exception as e:
            logger.warning(f"[delete_sql] Context {context_id} (may already be deleted): {e}")
            return False

    @staticmethod
    async def delete_tasks_for_context(context_id: str) -> int:
        """Remove all scheduled tasks associated with a context.
        
        Returns the number of tasks removed.
        """
        try:
            from python.helpers.task_scheduler import TaskScheduler
            scheduler = TaskScheduler.get()
            await scheduler.reload()
            tasks = scheduler.get_tasks_by_context_id(context_id)
            count = len(tasks)
            for task in tasks:
                await scheduler.remove_task_by_uuid(task.uuid)
            if count:
                logger.info(f"[delete_tasks] Removed {count} tasks for context {context_id}")
            return count
        except Exception as e:
            logger.warning(f"[delete_tasks] Context {context_id}: {e}")
            return 0

    @staticmethod
    def delete_project_dir(project_name: str) -> bool:
        """Remove project directory and associated git worktrees from work_dir/projects/.
        
        When deleting a base clone (repo-* dir), this also finds and removes all
        git worktree directories (build-* dirs) that were created from it. This ensures
        no orphaned worktree directories remain on the filesystem after project deletion.
        """
        from python.helpers.projects import PROJECTS_PARENT_DIR
        abs_path = files.get_abs_path(PROJECTS_PARENT_DIR, project_name)
        try:
            # If this is a base clone with a .git directory, clean up its worktrees first
            git_dir = os.path.join(abs_path, ".git")
            if os.path.isdir(git_dir):
                LifecycleService._cleanup_worktrees(abs_path, project_name)

            deleted = files.delete_dir(abs_path)
            gone = not os.path.exists(abs_path)
            if gone:
                logger.info(f"[delete_project_dir] Project dir '{project_name}' deleted")
            else:
                logger.warning(f"[delete_project_dir] Project dir '{project_name}' still exists!")
            return gone
        except Exception as e:
            logger.warning(f"[delete_project_dir] Project '{project_name}': {e}")
            return False

    @staticmethod
    def _cleanup_worktrees(base_path: str, project_name: str):
        """Remove all git worktrees associated with a base clone.
        
        Scans the projects directory for build-* directories whose .git file
        points back to this base clone, then removes them.
        """
        from python.helpers.projects import PROJECTS_PARENT_DIR
        projects_dir = files.get_abs_path(PROJECTS_PARENT_DIR)
        worktrees_removed = 0
        try:
            for entry in os.listdir(projects_dir):
                if not entry.startswith("build-"):
                    continue
                worktree_path = os.path.join(projects_dir, entry)
                git_file = os.path.join(worktree_path, ".git")
                # Git worktrees have a .git FILE (not directory) pointing to the base clone
                if os.path.isfile(git_file):
                    try:
                        with open(git_file, "r") as f:
                            content = f.read().strip()
                        # Content is like: "gitdir: /path/to/repo-owner-name/.git/worktrees/build-56-xxx"
                        # Match on project_name since paths may differ between host and container
                        if project_name in content:
                            import shutil
                            shutil.rmtree(worktree_path, ignore_errors=True)
                            worktrees_removed += 1
                            logger.info(f"[_cleanup_worktrees] Removed worktree '{entry}' (linked to '{project_name}')")
                    except Exception as e:
                        logger.warning(f"[_cleanup_worktrees] Error checking '{entry}': {e}")
            if worktrees_removed:
                logger.info(f"[_cleanup_worktrees] Removed {worktrees_removed} worktrees for '{project_name}'")
        except Exception as e:
            logger.warning(f"[_cleanup_worktrees] Error scanning for worktrees: {e}")

    @staticmethod
    def delete_project_scope(project_name: str) -> bool:
        """Clean up scoped secrets and parameters in DB for a project."""
        try:
            from python.helpers import config_db
            config_db.delete_scope(project_name)
            logger.info(f"[delete_project_scope] Scope '{project_name}' cleaned")
            return True
        except Exception as e:
            logger.warning(f"[delete_project_scope] Project '{project_name}': {e}")
            return False

    @staticmethod
    async def delete_sql_by_project(project_name: str) -> int:
        """Delete ALL SQL context entries associated with a project.
        
        Returns the number of contexts deleted.
        """
        try:
            from python.helpers.persistence_manager import PersistenceManager
            pm = PersistenceManager.get_instance()
            await pm.delete_contexts_by_project(project_name)
            logger.info(f"[delete_sql_by_project] Cascade-deleted contexts for project '{project_name}'")
            return 1  # Success
        except Exception as e:
            logger.warning(f"[delete_sql_by_project] Project '{project_name}': {e}")
            return 0

    # ================================================================
    # COMPOSITE OPERATIONS — high-level entity deletion
    # ================================================================

    @staticmethod
    async def delete_chat(context_id: str) -> dict:
        """Full chat deletion: memory + disk + SQL + tasks.
        
        This is the SINGLE authoritative method for deleting a chat context.
        All callers (API handlers, project cascade, etc.) MUST use this.
        
        Returns dict with status of each operation.
        """
        logger.info(f"[delete_chat] Starting full deletion of context {context_id}")

        memory_ok = await LifecycleService.delete_memory(context_id)
        disk_ok = LifecycleService.delete_disk(context_id)
        sql_ok = await LifecycleService.delete_sql(context_id)
        tasks_removed = await LifecycleService.delete_tasks_for_context(context_id)

        result = {
            "context_id": context_id,
            "memory": memory_ok,
            "disk": disk_ok,
            "sql": sql_ok,
            "tasks_removed": tasks_removed,
        }
        logger.info(f"[delete_chat] Result: {result}")
        return result

    @staticmethod
    async def delete_project(project_name: str) -> dict:
        """Full project deletion: cascade delete all associated chats + project dir + scope.
        
        This is the SINGLE authoritative method for deleting a project.
        
        Steps:
        1. Find ALL in-memory chats referencing this project and delete_chat each
        2. Cascade-delete ALL SQL context entries for this project (catches evicted ones)
        3. Delete the project filesystem directory
        4. Clean up scoped secrets/parameters
        
        Returns dict with deletion results.
        """
        from python.agent import AgentContext
        from python.helpers.projects import CONTEXT_DATA_KEY_PROJECT

        logger.info(f"[delete_project] Starting full deletion of project '{project_name}'")

        # 1. Find ALL in-memory contexts for this project and fully delete them
        #    This handles memory + disk + SQL for each context.
        chats_deleted = 0
        try:
            context_ids_to_delete = []
            for ctx in AgentContext.all():
                ctx_project = ctx.get_data(CONTEXT_DATA_KEY_PROJECT)
                if ctx_project == project_name:
                    context_ids_to_delete.append(ctx.id)
            
            for cid in context_ids_to_delete:
                await LifecycleService.delete_chat(cid)
                chats_deleted += 1
            logger.info(f"[delete_project] Deleted {chats_deleted} in-memory chats for '{project_name}'")
        except Exception as e:
            logger.warning(f"[delete_project] Chat cascade for '{project_name}': {e}")

        # 1b. Issue #1070: Clean up _context_metadata entries for evicted contexts.
        # _context_metadata may still contain entries for contexts that were LRU-evicted
        # from _contexts but still belong to the deleted project. Without this cleanup,
        # these ghost entries keep appearing in sidebar/history after project deletion.
        metadata_cleaned = 0
        try:
            meta_ids_to_remove = [
                ctx_id for ctx_id, meta in AgentContext._context_metadata.items()
                if meta.get("project_name") == project_name
            ]
            for ctx_id in meta_ids_to_remove:
                AgentContext._context_metadata.pop(ctx_id, None)
                metadata_cleaned += 1
            if metadata_cleaned:
                AgentContext._increment_version()
                logger.info(f"[delete_project] Cleaned {metadata_cleaned} metadata entries for '{project_name}'")
        except Exception as e:
            logger.warning(f"[delete_project] Metadata cleanup for '{project_name}': {e}")

        # 2. Cascade-delete all REMAINING SQL contexts for this project
        #    (catches evicted contexts not in memory)
        sql_ok = await LifecycleService.delete_sql_by_project(project_name)

        # 3. Delete project directory
        dir_ok = LifecycleService.delete_project_dir(project_name)

        # 4. Clean up scoped secrets/parameters
        scope_ok = LifecycleService.delete_project_scope(project_name)

        # 5. RCA-261: Clean up service registry entries for this project
        #    Prevents orphaned stopped-service entries from accumulating in
        #    managed_services.json after project deletion.
        services_cleaned = 0
        try:
            from python.helpers.projects import PROJECTS_PARENT_DIR
            project_dir = files.get_abs_path(PROJECTS_PARENT_DIR, project_name)
            from python.tools.services_mgt import cleanup_services_for_project
            services_cleaned = cleanup_services_for_project(project_dir)
            if services_cleaned:
                logger.info(f"[delete_project] Cleaned {services_cleaned} service entries for '{project_name}'")
        except Exception as e:
            logger.warning(f"[delete_project] Service cleanup for '{project_name}': {e}")

        result = {
            "project_name": project_name,
            "chats_deleted": chats_deleted,
            "metadata_cleaned": metadata_cleaned,
            "sql_cascade": sql_ok,
            "directory": dir_ok,
            "scope": scope_ok,
            "services_cleaned": services_cleaned,
        }
        logger.info(f"[delete_project] Result: {result}")
        return result

