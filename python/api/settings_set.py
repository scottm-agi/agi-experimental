from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response
from python.helpers import settings, feature_flags
from typing import Any


class SetSettings(ApiHandler):
    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_api_key(cls) -> bool:
        return True

    async def process(self, input: dict[Any, Any], request: Request) -> dict[Any, Any] | Response:
        # In production, only allow changes to user-facing sections
        if feature_flags.is_production_env():
            input = settings.filter_production_writable(input)
        set = settings.convert_in(input)
        settings.set_settings(set)
        return {"settings": settings.convert_out(settings.get_settings())}
