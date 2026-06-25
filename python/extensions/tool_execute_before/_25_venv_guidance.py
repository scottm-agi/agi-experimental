from __future__ import annotations
"""
Virtual Environment Guidance Extension

This extension intercepts package installation commands and provides
proactive guidance to use the container's pre-configured venv instead
of slow system package managers.

CRITICAL: This implements the Autonomous Retry Protocol by:
1. Intercepting apt-get/pip3 commands BEFORE they execute
2. Providing immediate guidance to use /opt/venv-agix/
3. Preventing slow mirror downloads and permission issues

The container has a pre-configured venv at /opt/venv-agix/ with:
- Python: /opt/venv-agix/bin/python
- Pip: /opt/venv-agix/bin/pip
- Pre-installed: flask, requests, boto3, beautifulsoup4, aiohttp, etc.
"""

import logging
import re
from typing import Any, Optional

from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers.universal_gate_budget import gate_check

logger = logging.getLogger("agix.venv_guidance")

# Patterns that indicate slow/inefficient installation methods
SLOW_INSTALL_PATTERNS = [
    r"apt-get\s+install",
    r"apt\s+install",
    r"sudo\s+apt",
    r"yum\s+install",
    r"dnf\s+install",
    r"apk\s+add",
]

# Patterns that indicate system Python usage (should use venv instead)
SYSTEM_PYTHON_PATTERNS = [
    r"python3\s+-m\s+pip",
    r"pip3\s+install",
    r"sudo\s+pip",
    r"/usr/bin/python",
    r"/usr/bin/pip",
]

# Packages commonly available in the container venv
VENV_PACKAGES = {
    "flask": "Flask web framework",
    "requests": "HTTP library",
    "boto3": "AWS SDK",
    "beautifulsoup4": "HTML parsing",
    "bs4": "BeautifulSoup alias",
    "aiohttp": "Async HTTP",
    "pyyaml": "YAML parsing",
    "jinja2": "Template engine",
    "werkzeug": "WSGI utilities",
    "click": "CLI framework",
    "python-docx": "Word documents",
    "pip": "Package installer",
    "python3-pip": "System pip (use venv instead)",
}

# Container venv configuration
CONTAINER_VENV = {
    "path": "/opt/venv-agix",
    "python": "/opt/venv-agix/bin/python",
    "pip": "/opt/venv-agix/bin/pip",
}


class VenvGuidance(Extension):
    # Context-aware: code agents only, code execution
    PROFILES = {"code"}
    TOOLS = frozenset({"code_execution_tool", "code_execution"})

    """
    Extension that provides proactive guidance for using container venv.
    
    Intercepts slow package manager commands (apt-get, pip3) and provides
    immediate guidance to use the pre-configured venv at /opt/venv-agix/.
    
    This implements the Autonomous Retry Protocol - agents should NEVER
    use slow system package managers when faster alternatives exist.
    """
    
    async def execute(
        self,
        tool_args: dict[str, Any] | None = None,
        tool_name: str = "",
        **kwargs
    ) -> Response | None:
        """
        Check for slow installation commands and provide guidance.
        
        Args:
            tool_args: Arguments passed to the tool
            tool_name: Name of the tool being executed
            **kwargs: Additional arguments
            
        Returns:
            Response with guidance if slow command detected, None otherwise
        """
        # Only intercept code_execution_tool
        if tool_name != "code_execution_tool":
            return None
        
        # Get the command being executed
        if not tool_args:
            return None
        
        code = tool_args.get("code", "") or tool_args.get("runtime", "")
        if not code:
            return None
        
        # Check for slow installation patterns
        is_slow, slow_pattern = self._is_slow_install_command(code)
        if is_slow:
            # Escape hatch — prevent infinite blocking loops
            if gate_check(self.agent.data, "venv_guidance"):
                return None

            package = self._extract_package_name(code)
            guidance = self._build_venv_guidance(code, slow_pattern, package)
            
            logger.warning(f"Intercepted slow install command: {code[:50]}...")
            
            # Store guidance in agent data for tracking
            self.agent.set_data("_venv_guidance_shown", True)
            
            # Return guidance as a Response - this will be shown to the agent
            # but won't block execution (break_loop=False)
            return Response(
                message=guidance,
                break_loop=False,
            )
        
        # Check for system Python usage
        is_system, system_pattern = self._is_system_python_command(code)
        if is_system:
            # Escape hatch — prevent infinite blocking loops
            if gate_check(self.agent.data, "venv_guidance"):
                return None

            package = self._extract_package_name(code)
            guidance = self._build_venv_guidance(code, system_pattern, package)
            
            logger.info(f"Detected system Python usage: {code[:50]}...")
            
            self.agent.set_data("_venv_guidance_shown", True)
            
            return Response(
                message=guidance,
                break_loop=False,
            )
        
        return None
    
    def _extract_package_name(self, command: str) -> Optional[str]:
        """Extract package name from install command."""
        # apt-get install <package>
        apt_match = re.search(r"(?:apt-get|apt)\s+install\s+(?:-y\s+)?(\S+)", command)
        if apt_match:
            return apt_match.group(1)
        
        # pip install <package>
        pip_match = re.search(r"pip\d?\s+install\s+(\S+)", command)
        if pip_match:
            return pip_match.group(1)
        
        return None
    
    def _is_slow_install_command(self, command: str) -> tuple[bool, str]:
        """
        Check if command is a slow installation method.
        
        Returns:
            Tuple of (is_slow, pattern_matched)
        """
        command_lower = command.lower()
        
        for pattern in SLOW_INSTALL_PATTERNS:
            if re.search(pattern, command_lower):
                return True, pattern
        
        return False, ""
    
    def _is_system_python_command(self, command: str) -> tuple[bool, str]:
        """
        Check if command uses system Python instead of venv.
        
        Returns:
            Tuple of (is_system, pattern_matched)
        """
        for pattern in SYSTEM_PYTHON_PATTERNS:
            if re.search(pattern, command):
                return True, pattern
        
        return False, ""
    
    def _build_venv_guidance(
        self,
        command: str,
        pattern: str,
        package: Optional[str]
    ) -> str:
        """Build guidance message for using container venv."""
        
        # Check if package is pre-installed
        package_info = ""
        if package:
            pkg_lower = package.lower().replace("-", "").replace("_", "")
            for venv_pkg, desc in VENV_PACKAGES.items():
                if pkg_lower in venv_pkg.lower().replace("-", "").replace("_", ""):
                    package_info = f"\n\n✅ **Good news!** `{venv_pkg}` ({desc}) is already installed in the container venv!"
                    break
        
        return f"""
🐍 **VENV GUIDANCE**: Use the container's pre-configured Python environment instead!

**Your command:** `{command[:100]}{'...' if len(command) > 100 else ''}`
**Issue:** Using system package manager is slow and may fail.
{package_info}

## ✅ Use Container Venv Instead

The container has a pre-configured Python virtual environment at `/opt/venv-agix/` with common packages already installed.

### Quick Commands:
```bash
# Check if package is already installed
{CONTAINER_VENV['pip']} show {package or '<package>'}

# Install a package (fast, no apt-get needed)
{CONTAINER_VENV['pip']} install {package or '<package>'}

# Run Python script
{CONTAINER_VENV['python']} your_script.py

# Run Flask app (use standardize_demo_port to get available port)
{CONTAINER_VENV['python']} -m flask run --host=0.0.0.0 --port=<PORT>
# ⚠️ Use `standardize_demo_port` tool to get an available port first!
```

### Pre-installed Packages:
- flask, requests, boto3, beautifulsoup4
- aiohttp, pyyaml, jinja2, werkzeug, click
- python-docx, and more...

### Example - Instead of:
```bash
# SLOW - Don't do this:
apt-get update && apt-get install -y python3-pip
pip3 install flask
python3 app.py
```

### Do this:
```bash
# FAST - Use venv:
{CONTAINER_VENV['python']} app.py
# Flask is already installed!
```

---
## 🚨 AUTONOMOUS RETRY PROTOCOL

**DO NOT use apt-get for Python packages.**
**DO NOT ask for human help.**

Use the venv at `/opt/venv-agix/` - it's faster and has common packages pre-installed.

If you need a package that's not installed:
```bash
{CONTAINER_VENV['pip']} install <package>
```

This is much faster than apt-get and won't have mirror/network issues.
"""
