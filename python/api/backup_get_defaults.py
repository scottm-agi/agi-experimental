from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response
from python.helpers.backup import BackupService
from python.helpers import feature_flags


class BackupGetDefaults(ApiHandler):
    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def requires_loopback(cls) -> bool:
        return False

    async def process(self, input: dict, request: Request) -> dict | Response:
        if feature_flags.is_production_env():
            return Response("Backup is not available in this environment", 403)
        try:
            backup_service = BackupService()
            default_metadata = backup_service.get_default_backup_metadata()

            return {
                "success": True,
                "default_patterns": {
                    "include_patterns": default_metadata["include_patterns"],
                    "exclude_patterns": default_metadata["exclude_patterns"]
                },
                "metadata": default_metadata
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
