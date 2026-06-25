from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response
from python.helpers import settings, runtime

class GetSettings(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        set = settings.convert_out(settings.get_settings())
        return {
            "success": True, 
            "settings": set,
            "is_development": runtime.is_development()
        }

    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_api_key(cls) -> bool:
        return True

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]
