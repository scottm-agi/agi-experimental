from __future__ import annotations
from python.helpers import dotenv_manager as dotenv
dotenv.save_dotenv_value("ANONYMIZED_TELEMETRY", "false")
import browser_use
import browser_use.utils
