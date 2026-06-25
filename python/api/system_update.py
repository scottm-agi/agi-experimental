from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response
from python.helpers import settings as settings_module
from python.helpers import process, git_helper
import subprocess
import os
import logging

logger = logging.getLogger(__name__)

class SystemUpdate(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        try:
            settings = settings_module.get_settings()
            repo_url = settings.get("update_repo_url")
            
            if not repo_url:
                return Response("Update repository URL not configured.", 400)

            # 1. Update remote URL
            logger.info(f"Setting git remote origin to {repo_url}")
            subprocess.run(["git", "remote", "set-url", "origin", repo_url], check=True)

            # 2. Fetch
            logger.info("Fetching from origin...")
            subprocess.run(["git", "fetch", "origin"], check=True)

            # 3. Pull
            logger.info("Pulling from origin...")
            try:
                git_info = git_helper.get_git_info()
                branch = git_info.get("branch") or "main"
            except Exception:
                branch = "main"
            
            subprocess.run(["git", "pull", "origin", branch], check=True)

            # 5. Restart (reload)
            logger.info("Restarting application...")
            process.reload()

            return {"status": "success", "message": "Update initiated successfully. System is restarting."}
        
        except subprocess.CalledProcessError as e:
            logger.error(f"Git command failed: {e}")
            return Response(f"Update failed: {str(e)}", 500)
        except Exception as e:
            logger.error(f"System update error: {e}")
            return Response(f"System update error: {str(e)}", 500)
