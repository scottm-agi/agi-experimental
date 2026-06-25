from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response
from python.helpers import settings, feature_flags
from typing import Any

class SetSettingsDelta(ApiHandler):
    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_api_key(cls) -> bool:
        return True

    async def process(self, input: dict[Any, Any], request: Request) -> dict[Any, Any] | Response:
        # In production, only allow changes to user-facing settings
        if feature_flags.is_production_env():
            input = settings.filter_production_writable_delta(input)
            if not input:
                return Response("No writable settings found in request", 403)
        settings.set_settings_delta(input)
        return {
            "ok": True,
            "settings": settings.convert_out(settings.get_settings())
        }
