from __future__ import annotations
from python.helpers.extension import Extension
from python.helpers.secrets_helper import get_secrets_manager
from python.helpers.tool import Response


class MaskToolSecrets(Extension):

    async def execute(self, response: Response | str | None = None, **kwargs):
        if not response:
            return
        secrets_mgr = get_secrets_manager(self.agent.context)
        # Handle both Response objects and raw strings
        if hasattr(response, 'message'):
            response.message = secrets_mgr.mask_values(response.message)
        # If response is a string, we can't modify it in place (strings are immutable)
        # The caller should handle string responses appropriately
