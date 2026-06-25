import os
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SUPERVISOR_LOG_PATH = "logs/supervisor.log"

def log_intervention(data: dict):
    """
    Appends a JSON line to the supervisor log.
    
    Args:
        data: Dictionary containing intervention details.
    """
    try:
        # Ensure directory exists
        log_dir = os.path.dirname(SUPERVISOR_LOG_PATH)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
            
        # Add common metadata
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data
        }
        
        # Append as JSONL
        with open(SUPERVISOR_LOG_PATH, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
            
    except Exception as e:
        logger.error(f"Failed to write to supervisor log: {e}")
