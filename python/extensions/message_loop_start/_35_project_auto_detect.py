from __future__ import annotations
"""
Project Auto-Detection Extension

Detects when the agent is working on a new coding project and triggers
mise-en-place setup automatically. This ensures all projects follow
the standard structure with MISE, git, etc.
"""

import logging
import re
from typing import Optional, Tuple

from python.helpers import projects
from python.helpers.project_layout_detector import FRAMEWORK_NAMES
from python.helpers.project_setup import mise_en_place, validate_project_ready

logger = logging.getLogger("agix.project_auto_detect")


# Keywords that suggest a new project is being created
NEW_PROJECT_KEYWORDS = [
    # Direct project creation
    r"\b(create|build|make|start|initialize|init|setup|set up)\b.*\b(project|app|application|website|api|service|tool|cli|library|package)\b",
    r"\b(new|fresh|blank|empty)\b.*\b(project|app|application|website|api|service)\b",
    
    # Framework-specific
    r"\b(create|build|make)\b.*\b(react|vue|angular|next|nuxt|svelte|express|fastapi|flask|django|rails|spring)\b",
    r"\b(python|node|nodejs|rust|go|ruby|java)\b.*\b(project|app|application)\b",
    
    # Development tasks that imply new project
    r"\b(implement|develop|code)\b.*\b(from scratch|new|complete)\b",
    r"\b(full[- ]?stack|frontend|backend)\b.*\b(application|app|project)\b",
]

# Keywords that suggest working on existing project (should NOT trigger)
EXISTING_PROJECT_KEYWORDS = [
    r"\b(fix|debug|update|modify|change|refactor|improve|optimize)\b",
    r"\b(existing|current|this)\b.*\b(project|code|app)\b",
    r"\b(add|remove|delete)\b.*\b(feature|function|component|file)\b",
]

# Minimum confidence threshold for auto-detection
MIN_CONFIDENCE = 0.6


def _detect_new_project_intent(message: str) -> Tuple[bool, float, Optional[str]]:
    """
    Detect if the message indicates intent to create a new project.
    
    Args:
        message: User message to analyze
        
    Returns:
        Tuple of (is_new_project, confidence, suggested_name)
    """
    message_lower = message.lower()
    
    # Check for existing project keywords (negative signal)
    for pattern in EXISTING_PROJECT_KEYWORDS:
        if re.search(pattern, message_lower):
            return False, 0.0, None
    
    # Check for new project keywords
    matches = 0
    for pattern in NEW_PROJECT_KEYWORDS:
        if re.search(pattern, message_lower):
            matches += 1
    
    if matches == 0:
        return False, 0.0, None
    
    # Calculate confidence based on matches
    confidence = min(0.3 + (matches * 0.2), 1.0)
    
    # Try to extract project name
    suggested_name = _extract_project_name(message)
    
    return True, confidence, suggested_name


def _extract_project_name(message: str) -> Optional[str]:
    """
    Try to extract a project name from the message.
    
    Args:
        message: User message
        
    Returns:
        Extracted project name or None
    """
    # Patterns to extract project names (order matters - more specific first)
    patterns = [
        # Quoted names with "called" or "named" - require quotes
        r'(?:called|named)\s+["\']([a-zA-Z][a-zA-Z0-9_-]+)["\']',
        # Quoted names anywhere followed by project/app
        r'["\']([a-zA-Z][a-zA-Z0-9_-]+)["\'].*(?:project|app|application)',
        # "project name:" followed by name
        r'project\s+name[:\s]+["\']?([a-zA-Z][a-zA-Z0-9_-]+)["\']?',
        # "create/build/make X application/project" pattern
        r'(?:create|build|make)\s+(?:a\s+)?([a-zA-Z][a-zA-Z0-9_-]+)\s+(?:project|app|application)',
        # Unquoted names with "called" or "named" (fallback)
        r'(?:called|named)\s+([a-zA-Z][a-zA-Z0-9_-]+)(?:\s|$)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            name = match.group(1)
            # Validate name length and exclude common words
            excluded_words = {'a', 'an', 'the', 'my', 'new', 'this', 'that', 'called', 'named'}
            if 2 <= len(name) <= 50 and name.lower() not in excluded_words:
                return name
    
    return None


def _detect_framework_from_message(message: str) -> Optional[str]:
    """
    Detect framework/language from the message.
    
    Args:
        message: User message
        
    Returns:
        Framework name or None
    """
    message_lower = message.lower()
    
    # Framework detection patterns
    framework_patterns = {
        "python": [r"\bpython\b", r"\bflask\b", r"\bdjango\b", r"\bfastapi\b", r"\bpytest\b"],
        "nodejs": [r"\bnode\.?js\b", r"\bexpress\b", r"\bnpm\b", r"\breact\b", r"\bvue\b", r"\bangular\b", r"\bnext\.?js\b", r"\btypescript\b"],
        "rust": [r"\brust\b", r"\bcargo\b"],
        "go": [r"\bgo\b", r"\bgolang\b"],
        "ruby": [r"\bruby\b", r"\brails\b"],
        "java": [r"\bjava\b", r"\bspring\b", r"\bmaven\b", r"\bgradle\b"],
    }
    
    for framework, patterns in framework_patterns.items():
        for pattern in patterns:
            if re.search(pattern, message_lower):
                return framework
    
    return None


async def execute(agent, loop_data: dict = {}, **kwargs):
    """
    Extension hook executed at the start of each message loop.
    
    Detects new project intent and triggers mise-en-place if needed.
    
    Args:
        agent: The agent instance
        loop_data: Data passed through the loop
    """
    # Only run for multiagentdev profile or when in code mode
    agent_name = getattr(agent, 'agent_name', '')
    current_mode = getattr(agent, 'current_mode', None)
    
    # Skip if not in a development context
    if agent_name not in ['multiagentdev', 'code', 'architect'] and current_mode not in ['code', 'architect']:
        return
    
    # Get the current message
    history = getattr(agent, 'history', [])
    if not history:
        return
    
    # Get the last user message
    last_message = None
    for msg in reversed(history):
        if msg.get('role') == 'user':
            last_message = msg.get('content', '')
            break
    
    if not last_message:
        return
    
    # Check if already in a project context
    context = getattr(agent, 'context', None)
    if context:
        current_project = projects.get_context_project_name(context)
        if current_project:
            # Already in a project, skip auto-detection
            logger.debug(f"Already in project context: {current_project}")
            return
    
    # Detect new project intent
    is_new_project, confidence, suggested_name = _detect_new_project_intent(last_message)
    
    if not is_new_project or confidence < MIN_CONFIDENCE:
        return
    
    logger.info(f"Detected new project intent (confidence: {confidence:.2f})")
    
    # Detect framework
    framework = _detect_framework_from_message(last_message)
    
    # Generate project name if not extracted
    if not suggested_name:
        # Use a generic name based on framework
        if framework:
            suggested_name = f"{framework}-project"
        else:
            suggested_name = "new-project"
    
    # Store detection results in loop_data for the agent to use
    loop_data['project_auto_detect'] = {
        'detected': True,
        'confidence': confidence,
        'suggested_name': suggested_name,
        'framework': framework,
        'message': f"""
## New Project Detected

I detected that you want to create a new project. I recommend setting up the project environment first.

**Suggested Name:** {suggested_name}
**Detected Framework:** {framework or 'auto-detect'}
**Confidence:** {confidence:.0%}

To set up the project with mise-en-place (git, MISE, proper structure), use:

```
setup_project:
  name: {suggested_name}
  framework: {framework or 'auto'}
```

This will:
1. Create project directory at `/projects/{suggested_name}`
2. Initialize git repository
3. Set up MISE environment (.mise.toml)
4. Create .gitignore
5. Create README.md
6. Initialize AGIX project metadata

Would you like me to proceed with project setup?
"""
    }
    
    logger.info(f"Project auto-detect: name={suggested_name}, framework={framework}, confidence={confidence}")
