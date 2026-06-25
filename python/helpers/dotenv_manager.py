from __future__ import annotations
import os
import re
from typing import Any

from python.helpers.files import get_abs_path
from dotenv import load_dotenv as _load_dotenv

KEY_AUTH_LOGIN = "AUTH_LOGIN"
KEY_AUTH_PASSWORD = "AUTH_PASSWORD"
KEY_RFC_PASSWORD = "RFC_PASSWORD"
KEY_ROOT_PASSWORD = "ROOT_PASSWORD"
KEY_AWS_PROFILE = "AWS_PROFILE"

def load_dotenv():
    dotenv_path = get_dotenv_file_path()
    if os.path.isfile(dotenv_path):
        # CRITICAL: override=False — Railway-injected env vars (AUTH_LOGIN, AUTH_PASSWORD, etc.)
        # are set in os.environ BEFORE this process starts. We must NOT overwrite them with
        # potentially stale .env file content written at provisioning time.
        #
        # Priority model:
        #   1. Railway env vars (os.environ at process start)  ← WINS for auth credentials
        #   2. .env file values                                ← FALLBACK for unset keys only
        #
        # This ensures: if a user rotates their password in Railway's env panel and redeploys,
        # the new credentials are picked up immediately without .env shadowing them.
        # User-configured model API keys written via the Settings UI to .env still apply
        # because Railway does not set those keys — they genuinely fall through to .env.
        _load_dotenv(dotenv_path, override=False)
    
    # CRITICAL: After load_dotenv(override=False), .env API key values are
    # IGNORED if Railway already set them (stale). EnvIntegrity.startup_sync()
    # reads .env directly and forces API keys into os.environ, ensuring the
    # user's last saved intent always wins — WITHOUT needing a container rebuild.
    try:
        from python.helpers.env_integrity import EnvIntegrity
        EnvIntegrity.startup_sync()
    except Exception:
        pass  # Don't fail startup if integrity module has issues
    
    # Ensure AWS_PROFILE is set for boto3/Bedrock if configured
    # This allows using named AWS CLI profiles (e.g., brie-account)
    aws_profile = os.getenv("AWS_PROFILE")
    if aws_profile:
        # Re-export to ensure boto3 picks it up
        os.environ["AWS_PROFILE"] = aws_profile


def get_dotenv_file_path():
    return get_abs_path(".env")

def get_dotenv_value(key: str, default: Any = None):
    # load_dotenv()       
    return os.getenv(key, default)

def save_dotenv_value(key: str, value: str):
    save_dotenv_values({key: value})


def save_dotenv_values(updates: dict[str, str]):
    dotenv_path = get_dotenv_file_path()
    if not os.path.isfile(dotenv_path):
        with open(dotenv_path, "w") as f:
            f.write("")
            
    try:
        with open(dotenv_path, "r+") as f:
            lines = f.readlines()
            
            for key, value in updates.items():
                if value is None:
                    value = ""
                
                found = False
                for i, line in enumerate(lines):
                    if re.match(rf"^\s*{key}\s*=", line):
                        lines[i] = f"{key}={value}\n"
                        found = True
                        break
                if not found:
                    lines.append(f"{key}={value}\n")
            
            f.seek(0)
            f.writelines(lines)
            f.truncate()
    except (PermissionError, IOError) as e:
        if os.environ.get("AGIX_ENV") == "production":
            print(f"Warning: Could not save .env changes in production (read-only filesystem): {e}")
        else:
            raise e
    load_dotenv()
