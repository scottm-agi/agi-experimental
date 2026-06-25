"""
Provider detection and credential management for repository automation.

REFACTORED: This module now re-exports from the centralized credentials.py helper.
All credential loading logic lives in python/helpers/credentials.py to maintain DRY.
"""

import logging

# =============================================================================
# RE-EXPORT FROM CENTRALIZED MODULE (DRY Principle)
# =============================================================================

# Import everything from the centralized credentials module
from python.helpers.credentials import (
    # Dataclasses
    GitHubCredentials,
    ForgejoCredentials,
    # Detection functions
    detect_provider,
    detect_provider_from_params,
    # Credential loading functions
    get_github_credentials as load_github_credentials,
    get_forgejo_credentials as load_forgejo_credentials,
    # Constants
    GITHUB_PATTERNS,
    FORGEJO_PATTERNS,
)

logger = logging.getLogger("repository-automation")

# Backward compatibility note:
# If any code imports load_github_credentials(params, context) or 
# load_forgejo_credentials(params, context), it will work seamlessly 
# because credentials.py uses the same signature.

# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    'GitHubCredentials',
    'ForgejoCredentials',
    'detect_provider',
    'detect_provider_from_params',
    'load_github_credentials',
    'load_forgejo_credentials',
    'GITHUB_PATTERNS',
    'FORGEJO_PATTERNS'
]