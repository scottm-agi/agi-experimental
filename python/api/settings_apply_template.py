from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response
from python.helpers import settings, feature_flags
from python.helpers.print_style import PrintStyle

class SettingsApplyTemplate(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        if feature_flags.is_production_env():
            return Response("Template application is not available in this environment", 403)
        try:
            template_name = input.get("template_name")
            if not template_name:
                return Response("template_name is required", 400)
            
            bundle = settings.get_preset_template(template_name)
            if not bundle:
                return Response(f"Template '{template_name}' not found", 404)
            
            # Apply the bundle
            settings.apply_settings_bundle(bundle)
            
            # Explicit save verification: re-fetch and check a sentinel value
            verification_bundle = settings.get_settings_bundle()
            v_s = verification_bundle.get("settings", {})
            b_s = bundle.get("settings", {})
            
            # Verify the primary provider was updated to match the template intent
            if v_s.get("chat_model_provider") == b_s.get("chat_model_provider"):
                return {"ok": True, "message": f"Template '{template_name}' applied and verified successfully"}
            else:
                PrintStyle.error(f"Verification failed: Expected {b_s.get('chat_model_provider')}, got {v_s.get('chat_model_provider')}")
                return Response(f"Template '{template_name}' application failed verification", 500)
        except Exception as e:
            PrintStyle.error(f"Error applying template: {e}")
            return Response(f"Template application failed: {str(e)}", 500)

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def requires_api_key(cls) -> bool:
        return True

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]
