from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response
from python.helpers import settings, feature_flags
from python.helpers.print_style import PrintStyle

class SettingsImport(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        if feature_flags.is_production_env():
            return Response("Settings import is not available in this environment", 403)
        try:
            if not input:
                return Response("Empty settings bundle", 400)
            
            settings.apply_settings_bundle(input)
            return {"ok": True, "message": "Settings imported successfully"}
        except Exception as e:
            PrintStyle.error(f"Error importing settings: {e}")
            return Response(f"Import failed: {str(e)}", 500)

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def requires_api_key(cls) -> bool:
        return True

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]
