from __future__ import annotations
from python.helpers.api import ApiHandler, Input, Request, Output
from python.api.poll import Poll

class ApiPoll(Poll):
    """
    API-key protected version of the Poll endpoint.
    Used by host-based test scripts and external integrations.
    """
    
    @classmethod
    def requires_auth(cls) -> bool:
        return False  # Do not require web session/CSRF

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]

    @classmethod
    def requires_api_key(cls) -> bool:
        return True  # Require X-API-KEY header
