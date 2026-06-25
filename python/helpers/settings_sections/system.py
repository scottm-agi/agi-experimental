"""
System settings section builders.

Phase 5 of settings.py modularization.
Extracts development and speech/TTS sections.
"""

from typing import Any

from python.helpers import runtime, dotenv_manager as dotenv

from .base import (
    SettingsField,
    SettingsSection,
    SectionBuilderContext,
    PASSWORD_PLACEHOLDER,
)


def build_dev_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Development settings section."""
    settings = ctx.settings
    
    dev_fields: list[SettingsField] = []

    dev_fields.append(
        {
            "id": "shell_interface",
            "title": "Shell Interface",
            "description": "Terminal interface used for Code Execution Tool. Local Python TTY works locally in both dockerized and development environments. SSH always connects to dockerized environment (automatically at localhost or RFC host address).",
            "type": "select",
            "value": settings["shell_interface"],
            "options": [{"value": "local", "label": "Local Python TTY"}, {"value": "ssh", "label": "SSH"}],
        }
    )

    if runtime.is_development():
        dev_fields.append(
            {
                "id": "rfc_url",
                "title": "RFC Destination URL",
                "description": "URL of dockerized Alex instance for remote function calls. Do not specify port here.",
                "type": "text",
                "value": settings["rfc_url"],
            }
        )

    dev_fields.append(
        {
            "id": "rfc_password",
            "title": "RFC Password",
            "description": "Password for remote function calls. Passwords must match on both instances. RFCs can not be used with empty password.",
            "type": "password",
            "value": (
                PASSWORD_PLACEHOLDER
                if dotenv.get_dotenv_value(dotenv.KEY_RFC_PASSWORD)
                else ""
            ),
        }
    )

    if runtime.is_development():
        dev_fields.append(
            {
                "id": "rfc_port_http",
                "title": "RFC HTTP port",
                "description": "HTTP port for dockerized instance of Alex.",
                "type": "text",
                "value": settings["rfc_port_http"],
            }
        )

        dev_fields.append(
            {
                "id": "rfc_port_ssh",
                "title": "RFC SSH port",
                "description": "SSH port for dockerized instance of Alex.",
                "type": "text",
                "value": settings["rfc_port_ssh"],
            }
        )

    return {
        "id": "dev",
        "title": "Development",
        "description": "Parameters for AGIX framework development. RFCs (remote function calls) are used to call functions on another AGIX instance. You can develop and debug Alex natively on your local system while redirecting some functions to Alex instance in docker. This is crucial for development as Alex needs to run in standardized environment to support all features.",
        "fields": dev_fields,
        "tab": "developer",
    }


def build_speech_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Speech (STT + TTS) settings section."""
    settings = ctx.settings
    
    stt_fields: list[SettingsField] = []

    stt_fields.append(
        {
            "id": "stt_microphone_section",
            "title": "Microphone device",
            "description": "Select the microphone device to use for speech-to-text.",
            "value": "<x-component path='/settings/speech/microphone.html' />",
            "type": "html",
        }
    )

    stt_fields.append(
        {
            "id": "stt_model_size",
            "title": "Speech-to-text model size",
            "description": "Select the speech-to-text model size",
            "type": "select",
            "value": settings["stt_model_size"],
            "options": [
                {"value": "tiny", "label": "Tiny (39M, English)"},
                {"value": "base", "label": "Base (74M, English)"},
                {"value": "small", "label": "Small (244M, English)"},
                {"value": "medium", "label": "Medium (769M, English)"},
                {"value": "large", "label": "Large (1.5B, Multilingual)"},
                {"value": "turbo", "label": "Turbo (Multilingual)"},
            ],
        }
    )

    stt_fields.append(
        {
            "id": "stt_language",
            "title": "Speech-to-text language code",
            "description": "Language code (e.g. en, fr, it)",
            "type": "text",
            "value": settings["stt_language"],
        }
    )

    stt_fields.append(
        {
            "id": "stt_silence_threshold",
            "title": "Microphone silence threshold",
            "description": "Silence detection threshold. Lower values are more sensitive to noise.",
            "type": "range",
            "min": 0,
            "max": 1,
            "step": 0.01,
            "value": settings["stt_silence_threshold"],
        }
    )

    stt_fields.append(
        {
            "id": "stt_silence_duration",
            "title": "Microphone silence duration (ms)",
            "description": "Duration of silence before the system considers speaking to have ended.",
            "type": "text",
            "value": settings["stt_silence_duration"],
        }
    )

    stt_fields.append(
        {
            "id": "stt_waiting_timeout",
            "title": "Microphone waiting timeout (ms)",
            "description": "Duration of silence before the system closes the microphone.",
            "type": "text",
            "value": settings["stt_waiting_timeout"],
        }
    )

    # TTS fields
    tts_fields: list[SettingsField] = []

    tts_fields.append(
        {
            "id": "tts_kokoro",
            "title": "Enable Kokoro TTS",
            "description": "Enable higher quality server-side AI (Kokoro) instead of browser-based text-to-speech.",
            "type": "switch",
            "value": settings["tts_kokoro"],
        }
    )

    return {
        "id": "speech",
        "title": "Speech",
        "description": "Voice transcription and speech synthesis settings.",
        "fields": stt_fields + tts_fields,
        "tab": "agent",
    }