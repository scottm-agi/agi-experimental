"""Project Configuration Constants.

Constants used by non-gate code: BDD generators, delegation guards,
image generation, and response tool.
"""

# BDD/TDD coverage threshold (used by bdd_generator*.py, bdd_validators.py)
# RCA-470: Raised from 0.8 → 1.0. At 80%, 7 delivery/infrastructure REQs
# (including REQ-SCAFFOLD-001 scaffold cleanup) passed unchecked, causing
# homepage boilerplate to persist into final build. 100% coverage mandatory.
BDD_COVERAGE_THRESHOLD = 1.0

# Max requirements per delegation (used by budget_reserve.py)
MAX_REQUIREMENTS_PER_DELEGATION = 5

# Max rework cycles for delegation guards (used by delegation_guards.py)
MAX_REWORK_CYCLES = 3

# Max image generations per session (used by generate_image.py)
MAX_IMAGE_GENERATIONS_PER_SESSION = 15

# Max cumulative response rejections (used by response.py)
MAX_TOTAL_RESPONSE_REJECTIONS = 6

# Max post-delivery rejections before force-allow (used by response.py)
MAX_POST_DELIVERY_REJECTIONS = 3
