from __future__ import annotations
import logging
from typing import Any, Dict
from python.helpers.tool import Tool, Response
from python.helpers.settings import set_settings_delta
from python.helpers.print_style import PrintStyle

logger = logging.getLogger("settings-set")

# ── Hard-coded validation rules ──────────────────────────────────────────────
# These prevent agents from hallucinating invalid values for critical settings.
# The tool is deterministic — if a value doesn't pass validation, it's rejected.

_VALID_IMAGE_GEN_PROVIDERS = {"openrouter", "gemini", "openai", "dall-e", "dalle"}

# All valid image generation models contain one of these keywords in their ID.
# This catches agent hallucinations like "google/gemini-pro-1.5" (a chat model).
_IMAGE_MODEL_KEYWORDS = ("image", "dall-e", "dalle")

# Settings keys that have validation rules
_VALIDATED_SETTINGS: dict[str, str] = {
    "image_gen_provider": "provider",
    "image_gen_model": "model",
}


class SettingsSet(Tool):
    """
    Updates global configuration settings in settings.json.
    Use this to change model configurations, UI behavior, or system defaults.
    
    IMPORTANT: Secrets (API keys, passwords) should NOT be stored here.
    Use secret_set tool for sensitive credentials.
    
    IMPORTANT: Do NOT change image_gen_model or image_gen_provider unless
    explicitly asked by the user. These are pre-configured in Settings UI.
    """

    def _validate_settings(self, settings: Dict[str, Any]) -> list[str]:
        """Validate settings values against hard rules. Returns list of errors."""
        errors: list[str] = []

        # ── Validate image_gen_provider ──
        provider = settings.get("image_gen_provider")
        if provider is not None:
            if not isinstance(provider, str) or provider.lower() not in _VALID_IMAGE_GEN_PROVIDERS:
                errors.append(
                    f"Invalid image_gen_provider '{provider}'. "
                    f"Must be one of: {sorted(_VALID_IMAGE_GEN_PROVIDERS)}. "
                    f"Do NOT change this setting unless the user explicitly asked."
                )

        # ── Validate image_gen_model ──
        model = settings.get("image_gen_model")
        if model is not None:
            if not isinstance(model, str):
                errors.append(f"image_gen_model must be a string, got {type(model).__name__}.")
            else:
                model_lower = model.lower()
                if not any(kw in model_lower for kw in _IMAGE_MODEL_KEYWORDS):
                    errors.append(
                        f"Invalid image_gen_model '{model}'. "
                        f"Image generation models must contain one of {_IMAGE_MODEL_KEYWORDS} in their name. "
                        f"'{model}' looks like a chat/text model, not an image model. "
                        f"Valid examples: 'google/gemini-3.1-flash-image-preview', 'dall-e-3'. "
                        f"Do NOT change this setting unless the user explicitly asked."
                    )

        return errors

    async def execute(self, settings: Dict[str, Any], **kwargs) -> Response:
        """
        Updates one or more settings using a delta (partial update).
        
        Args:
            settings (dict): A dictionary of settings to update (e.g., {"chat_model_name": "gpt-4o"}).
        
        Returns:
            Confirmation of updated settings.
        """
        try:
            if not settings or not isinstance(settings, dict):
                return Response(message="Error: settings must be a non-empty dictionary.", break_loop=False)

            # ── HARD VALIDATION: Reject hallucinated values before they corrupt settings ──
            validation_errors = self._validate_settings(settings)
            if validation_errors:
                error_msg = "Settings validation FAILED (values rejected):\n" + "\n".join(f"  • {e}" for e in validation_errors)
                logger.warning(f"Agent attempted to set invalid settings: {validation_errors}")
                return Response(message=error_msg, break_loop=False)

            # Note: We rely on python.helpers.settings.set_settings_delta() which further 
            # uses normalize_settings() and _write_sensitive_settings().
            # This ensures that sensitive fields (like auth_password) are securely moved to 
            # the .env file and only placeholders are stored in the plain-text settings.json.
            
            set_settings_delta(settings)
            
            # POST-SET VERIFICATION: Read back to confirm
            from python.helpers.settings import get_settings
            current = get_settings()
            verified = []
            failed = []
            for k, v in settings.items():
                actual = current.get(k)
                if actual == v:
                    verified.append(k)
                else:
                    failed.append(f"{k} (expected={v}, got={actual})")
            
            keys_updated = ", ".join(settings.keys())
            if failed:
                error_msg = f"⚠️ Partial verification failure: {'; '.join(failed)}"
                PrintStyle(font_color="orange", bold=True).print(error_msg)
                message = f"Updated settings: {keys_updated}. {error_msg}"
            else:
                message = f"✅ Successfully updated settings: {keys_updated} [Verified]"
            PrintStyle.hint(message)
            return Response(message=message, break_loop=False)
            
        except Exception as e:
            return Response(message=f"Error updating settings: {e}", break_loop=True)

if __name__ == "__main__":
    pass
