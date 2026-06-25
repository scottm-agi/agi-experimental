from __future__ import annotations
import logging
import os
from python.helpers.api import ApiHandler, Input, Output, Request, Response
from python.helpers.persistence_manager import PersistenceManager
from python.helpers import projects, files

logger = logging.getLogger("agix.api.stale_project_cleanup")


class StaleProjectCleanup(ApiHandler):
    """
    API endpoint to identify and optionally clean up stale project directories.
    
    A stale project is a directory on the filesystem that has no associated
    chat contexts in the database. This happens when a chat is deleted but
    the project directory is not cleaned up.

    Query modes:
    - dry_run=true (default): List stale projects without deleting
    - dry_run=false: Actually delete stale project directories
    """

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    async def process(self, input: Input, request: Request) -> Output:
        dry_run = input.get("dry_run", True)
        # Support string "false" from query params
        if isinstance(dry_run, str):
            dry_run = dry_run.lower() != "false"

        projects_dir = projects.get_projects_parent_folder()
        
        if not os.path.isdir(projects_dir):
            return {
                "success": True,
                "message": "Projects directory does not exist.",
                "stale_projects": [],
                "active_projects": [],
            }

        # 1. Get all project directories from the filesystem
        fs_project_names = set()
        for name in os.listdir(projects_dir):
            abs_path = os.path.join(projects_dir, name)
            if os.path.isdir(abs_path):
                fs_project_names.add(name)

        if not fs_project_names:
            return {
                "success": True,
                "message": "No project directories found on filesystem.",
                "stale_projects": [],
                "active_projects": [],
            }

        # 2. Get all distinct project_names from the database
        db_project_names = set()
        try:
            pm = PersistenceManager.get_instance()
            db_project_names = await pm.get_active_project_names()
        except Exception as e:
            logger.error(f"Failed to query database for active projects: {e}")
            return {
                "success": False,
                "message": f"Database query failed: {e}",
                "stale_projects": [],
                "active_projects": [],
            }

        # 3. Identify stale projects (on filesystem but not in DB)
        stale_names = fs_project_names - db_project_names
        active_names = fs_project_names & db_project_names

        # Build result details
        stale_details = []
        for name in sorted(stale_names):
            abs_path = os.path.join(projects_dir, name)
            size_bytes = _get_dir_size(abs_path)
            stale_details.append({
                "name": name,
                "path": abs_path,
                "size_bytes": size_bytes,
                "size_human": _human_size(size_bytes),
            })

        deleted = []
        errors_list = []

        # 4. Optionally delete stale projects
        if not dry_run:
            for item in stale_details:
                try:
                    await projects.delete_project(item["name"])
                    deleted.append(item["name"])
                    logger.info(f"Deleted stale project: {item['name']} ({item['size_human']})")
                except Exception as e:
                    err_msg = f"Failed to delete {item['name']}: {e}"
                    errors_list.append(err_msg)
                    logger.error(err_msg)

        return {
            "success": True,
            "dry_run": dry_run,
            "message": f"Found {len(stale_details)} stale projects out of {len(fs_project_names)} total.",
            "stale_projects": stale_details,
            "active_projects": sorted(active_names),
            "deleted": deleted if not dry_run else [],
            "errors": errors_list,
            "total_stale_size": _human_size(sum(d["size_bytes"] for d in stale_details)),
        }


def _get_dir_size(path: str) -> int:
    """Get total size of a directory in bytes."""
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _human_size(size_bytes: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"
