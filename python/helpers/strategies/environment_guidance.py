"""
Environment Guidance Strategy for Master Agent Supervisor

Extracted from intervention_strategies.py per Issue #778 (line audit).
This module provides the EnvironmentGuidanceStrategy that handles
environment and installation issues in container environments.

CRITICAL: Implements the Autonomous Retry Protocol:
- Never accept errors as final
- Always provide 3-5 alternative approaches
- Include Perplexity MCP research suggestions
- Require 5 attempts before escalating to human
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from python.helpers.loop_prevention import (
    PatternType, InterventionType, InterventionRecord
)
from python.helpers.pattern_detectors import DetectedPattern, AgentState

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Import base class
from python.helpers.intervention_strategies import InterventionStrategy, InterventionPlan


class EnvironmentGuidanceStrategy(InterventionStrategy):
    """
    Strategy for handling environment and installation issues.
    
    Provides guidance when agents use inefficient installation methods
    or don't leverage pre-configured environments.
    
    CRITICAL: This strategy implements the Autonomous Retry Protocol:
    - Never accept errors as final
    - Always provide 3-5 alternative approaches
    - Include Perplexity MCP research suggestions
    - Require 5 attempts before escalating to human
    """
    
    def __init__(self):
        # Container environment knowledge
        self._container_venvs = {
            "/opt/venv-agix": {
                "python": "/opt/venv-agix/bin/python",
                "pip": "/opt/venv-agix/bin/pip",
                "packages": ["flask", "requests", "boto3", "python-docx", "beautifulsoup4", 
                            "aiohttp", "pyyaml", "jinja2", "werkzeug", "click"],
            },
        }
        
        # Alternative approaches for common packages
        self._package_alternatives = {
            "pip": "Already available at /opt/venv-agix/bin/pip",
            "python3-pip": "Use /opt/venv-agix/bin/pip instead - DO NOT use apt-get",
            "flask": "Already installed in /opt/venv-agix - use /opt/venv-agix/bin/python app.py",
            "requests": "Already installed in /opt/venv-agix - use /opt/venv-agix/bin/python",
            "boto3": "Already installed in /opt/venv-agix - use /opt/venv-agix/bin/python",
            "beautifulsoup4": "Already installed in /opt/venv-agix - use /opt/venv-agix/bin/python",
            "aiohttp": "Already installed in /opt/venv-agix - use /opt/venv-agix/bin/python",
        }
        
        # Perplexity MCP research queries for common issues
        self._research_queries = {
            "pip_install_fail": [
                "Python pip install without apt-get in Docker container",
                "Use existing Python venv instead of system pip",
                "Docker container Python package installation best practices",
            ],
            "flask_not_found": [
                "Run Flask app with specific Python interpreter path",
                "Flask ModuleNotFoundError Docker container solution",
                "Python virtual environment Flask import error fix",
            ],
            "network_timeout": [
                "Docker container apt-get slow mirror alternatives",
                "Install Python packages offline in Docker",
                "Pre-installed Python packages Docker container",
            ],
            "permission_denied": [
                "Python pip install permission denied Docker solution",
                "Run Python script without sudo in container",
                "Docker container Python venv permissions",
            ],
        }
    
    @property
    def intervention_type(self) -> InterventionType:
        return InterventionType.PROVIDE_HINT
    
    @property
    def handles_patterns(self) -> List[PatternType]:
        return [PatternType.PROGRESS_STALL]  # ENV patterns use PROGRESS_STALL
    
    async def plan(
        self,
        pattern: DetectedPattern,
        state: AgentState,
        failed_interventions: List[InterventionRecord],
    ) -> Optional[InterventionPlan]:
        # Only handle ENV-* patterns
        pattern_id = pattern.metadata.get("pattern_id", "")
        if not pattern_id.startswith("ENV-"):
            return None
        
        # Check previous attempts
        env_attempts = len([
            i for i in failed_interventions
            if i.intervention_type == InterventionType.PROVIDE_HINT
            and i.metadata.get("pattern_category") == "environment"
        ])
        
        if env_attempts >= 2:
            return None
        
        message = self._build_environment_message(pattern, state)
        
        return self._create_plan(
            state,
            message=message,
            priority=2,  # High priority - environment issues block progress
            metadata={
                "pattern_id": pattern_id,
                "pattern_category": "environment",
                "attempt_number": env_attempts + 1,
            },
        )
    
    def _build_environment_message(
        self,
        pattern: DetectedPattern,
        state: AgentState,
    ) -> str:
        """Build the intervention message for environment issues."""
        pattern_id = pattern.metadata.get("pattern_id", "")
        suggestion = pattern.metadata.get("suggestion", "")
        command = pattern.metadata.get("command", "")
        package = pattern.metadata.get("package", "")
        
        if pattern_id == "ENV-001":
            # Inefficient installation
            alt = self._package_alternatives.get(package, "")
            return f"""🐢 SLOW INSTALLATION DETECTED: You're using a system package manager which is slow.

**Problem:** {pattern.description}
**Command:** `{command[:100]}...`

**Faster Alternative:**
{alt if alt else suggestion}

**Container Environment:**
This container has a pre-configured Python virtual environment at `/opt/venv-agix/` with common packages already installed:
- Python: `/opt/venv-agix/bin/python`
- Pip: `/opt/venv-agix/bin/pip`
- Pre-installed: flask, requests, boto3, beautifulsoup4

**Example:**
```bash
# Instead of: apt-get install python3-pip && pip install flask
# Use:
/opt/venv-agix/bin/python -c "import flask; print('Flask ready!')"
```

Please use the existing venv instead of installing packages via apt-get."""

        elif pattern_id == "ENV-002":
            # Slow network/mirror
            return f"""🌐 SLOW NETWORK DETECTED: Package downloads are stalling.

**Problem:** {pattern.description}

**Alternatives:**
1. Use pre-installed packages from `/opt/venv-agix/`
2. Check if the package is already available: `pip show <package>`
3. If you must install, try: `/opt/venv-agix/bin/pip install <package>`

The container's venv has faster access than system package managers."""

        elif pattern_id == "ENV-003":
            # Redundant installation
            return f"""📦 REDUNDANT INSTALLATION: You're installing packages without checking availability.

**Problem:** {pattern.description}

**Best Practice:**
Before installing, check if the package exists:
```bash
# For Python packages:
/opt/venv-agix/bin/pip show <package>

# For system commands:
which <command>
```

**Pre-installed in /opt/venv-agix:**
- flask, requests, boto3, beautifulsoup4, python-docx

Check first, install only if needed."""

        elif pattern_id == "ENV-004":
            # Venv unawareness
            return f"""🐍 SYSTEM PYTHON DETECTED: You're using system Python instead of the container venv.

**Problem:** {pattern.description}
**Command:** `{command[:100]}...`

**Use the Container Venv Instead:**
```bash
# Instead of: python3 -m pip install ...
# Use:
/opt/venv-agix/bin/pip install ...

# Instead of: python3 script.py
# Use:
/opt/venv-agix/bin/python script.py
```

The venv at `/opt/venv-agix/` has common packages pre-installed and avoids permission issues."""

        else:
            return f"""⚠️ ENVIRONMENT ISSUE: {pattern.description}

{suggestion}

**Container Environment:**
- Python venv: `/opt/venv-agix/`
- Python: `/opt/venv-agix/bin/python`
- Pip: `/opt/venv-agix/bin/pip`

{self._get_autonomous_retry_footer(pattern_id)}"""
    
    def _get_autonomous_retry_footer(self, pattern_id: str) -> str:
        """
        Generate the Autonomous Retry Protocol footer with Perplexity MCP research suggestions.
        
        This footer enforces the rule that agents should NEVER give up or ask for human help.
        Instead, they must try at least 5 alternative approaches before escalating.
        """
        # Get relevant research queries for this pattern
        research_category = self._get_research_category(pattern_id)
        queries = self._research_queries.get(research_category, [])
        
        research_section = ""
        if queries:
            research_section = f"""
**🔍 Research Alternatives with Perplexity MCP:**
If the above solutions don't work, use Perplexity MCP to research:
"""
            for i, query in enumerate(queries, 1):
                research_section += f"   {i}. \"{query}\"\n"
        
        return f"""
---
## 🚨 AUTONOMOUS RETRY PROTOCOL (MANDATORY)

**DO NOT ASK FOR HUMAN HELP. DO NOT GIVE UP.**

You MUST try at least 5 alternative approaches before escalating:

**Alternative Approaches to Try:**
1. **Use container venv**: `/opt/venv-agix/bin/python` and `/opt/venv-agix/bin/pip`
2. **Check if package exists**: `/opt/venv-agix/bin/pip show <package>`
3. **Install to venv**: `/opt/venv-agix/bin/pip install <package>`
4. **Use Python's built-in modules** when possible (no install needed)
5. **Research alternatives** using Perplexity MCP search tool
{research_section}
**Explicit Commands to Try:**
```bash
# Check what's already installed
/opt/venv-agix/bin/pip list | grep -i <package>

# Install if needed (fast, no apt-get)
/opt/venv-agix/bin/pip install <package>

# Run your script with the venv Python
/opt/venv-agix/bin/python your_script.py
```

**CRITICAL**: Keep trying until you succeed. Research solutions. Adapt your approach.
Human intervention is NOT an option until you've exhausted all alternatives."""
    
    def _get_research_category(self, pattern_id: str) -> str:
        """Map pattern ID to research category."""
        mapping = {
            "ENV-001": "pip_install_fail",
            "ENV-002": "network_timeout",
            "ENV-003": "pip_install_fail",
            "ENV-004": "pip_install_fail",
        }
        return mapping.get(pattern_id, "pip_install_fail")
