from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response
from python.helpers import settings, feature_flags

class SettingsExport(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        if feature_flags.is_production_env():
            return Response("Settings export is not available in this environment", 403)
        bundle = settings.get_settings_bundle()
        return bundle

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def requires_api_key(cls) -> bool:
        return True

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]
