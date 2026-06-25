from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response
import os
from python.helpers import errors, git_helper

class HealthCheck(ApiHandler):

    @classmethod
    def requires_loopback(cls) -> bool:
        return True

    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    async def process(self, input: dict, request: Request) -> dict | Response:
        from python.helpers import status, settings
        gitinfo = None
        error = None
        try:
            gitinfo = git_helper.get_git_info()
        except Exception as e:
            error = errors.error_text(e)

        if os.environ.get("DEBUG_HEALTH"):
            print(f"[HEALTH] Check accessed from {request.remote_addr}", flush=True)
        init_status, init_errors = status.get_status()
        
        current_settings = settings.get_settings()
        # Basic check for missing crucial config
        config_status = {
            "chat_model": current_settings.get("chat_model_provider") not in [None, "none", ""],
            "util_model": current_settings.get("util_model_provider") not in [None, "none", ""],
            "embed_model": current_settings.get("embed_model_provider") not in [None, "none", ""],
            "dotenv_exists": os.path.isfile(os.path.join(os.getcwd(), ".env"))
        }

        return {
            "version": "security-hardening-v1",
            "gitinfo": gitinfo, 
            "error": error, 
            "init_status": init_status, 
            "init_errors": init_errors,
            "config_status": config_status
        }
