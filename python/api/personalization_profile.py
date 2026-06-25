from __future__ import annotations
import os
import logging
from python.helpers.api import ApiHandler
from flask import Request, Response

logger = logging.getLogger(__name__)

PERSONALIZATION_DATA_DIR = os.environ.get(
    "PERSONALIZATION_DATA_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "personalization"),
)


class PersonalizationProfileAPI(ApiHandler):
    """
    REST endpoint for reading/writing personalization profile.
    
    Actions:
      - get: Returns current profile + signal count
      - reset: Deletes profile and signals
      - signals: Returns raw signal history
    """

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    async def process(self, input: dict, request: Request) -> dict | Response:
        action = input.get("action", "get")
        user_id = input.get("user_id", "default")
        data_dir = os.path.abspath(PERSONALIZATION_DATA_DIR)
        
        print(f"[PERSONALIZATION_API] Action: {action}, User: {user_id}, Data Dir: {data_dir}", flush=True)

        try:
            from python.helpers.personalization import PersonalizationProfile
            from python.helpers.personalization_signals import SignalCollector

            if action == "get":
                profile = PersonalizationProfile.load(user_id, data_dir)
                collector = SignalCollector(user_id=user_id, data_dir=data_dir)
                
                # Use collector to get signal count directly
                signal_count = len(collector.get_signal_history())

                profile_data = profile.to_dict() if profile else None

                return {
                    "success": True,
                    "profile": profile_data,
                    "signal_count": signal_count,
                    "has_profile": profile is not None,
                }

            elif action == "signals":
                collector = SignalCollector(user_id=user_id, data_dir=data_dir)
                signals = collector.get_signal_history()
                return {
                    "success": True,
                    "signals": signals[-50:],  # Last 50 to avoid huge responses
                    "total_count": len(signals),
                }

            elif action == "reset":
                PersonalizationProfile.delete(user_id, data_dir)
                # Also clear signals
                signals_path = os.path.join(data_dir, user_id, "signals.jsonl")
                if os.path.exists(signals_path):
                    os.remove(signals_path)
                return {"success": True, "message": "Profile and signals cleared"}

            elif action == "evolution":
                profile = PersonalizationProfile.load(user_id, data_dir)
                if profile:
                    history = profile.get_evolution_history(data_dir)
                    return {"success": True, "evolution": history}
                return {"success": True, "evolution": []}

            else:
                return {"success": False, "error": f"Unknown action: {action}"}

        except Exception as e:
            logger.error("Personalization API error: %s", e)
            return {"success": False, "error": str(e)}
