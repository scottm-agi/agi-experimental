"""
Environment & Installation Detectors (ENV-001 to ENV-010)

Detects issues with environment and installation:
- InefficientInstallationDetector: Slow/inefficient installation methods
- RedundantDependencyInstallDetector: Installing already-available dependencies
- VenvUnawarenessDetector: Not using available virtual environments
"""

from typing import Optional

from python.helpers.loop_prevention import PatternType
from .base import PatternDetector, AgentState, DetectedPattern


class InefficientInstallationDetector(PatternDetector):
    """
    ENV-001: Detects when agent uses slow/inefficient installation methods.
    
    Detects patterns like:
    - Using apt-get when pip/venv already has the package
    - Installing packages that are already available
    - Using slow mirrors when faster alternatives exist
    - Not using pre-configured virtual environments
    
    This detector monitors execute_command tool calls for package manager
    operations and checks if faster alternatives exist.
    """
    
    # Slow package manager patterns
    SLOW_INSTALL_PATTERNS = [
        "apt-get install",
        "apt install",
        "yum install",
        "dnf install",
        "apk add",
        "pacman -S",
    ]
    
    # Patterns indicating slow mirrors or network issues (in stdout/stderr)
    SLOW_NETWORK_PATTERNS = [
        "waiting for headers",
        "connecting to",
        "0 b/s",
        "stalled",
        "retrying",
        "mirror",
        "kali.download",  # Known slow mirror
        "http.kali.org",  # Kali mirror
        "ign:",  # apt ignoring packages (slow/unavailable)
        "err:",  # apt errors
    ]
    
    # Pre-configured environments that should be used instead
    KNOWN_VENVS = [
        "/opt/venv-agix",
        "/opt/venv",
        ".venv",
        "venv",
    ]
    
    # Packages commonly available in pre-configured venvs
    COMMON_VENV_PACKAGES = [
        "flask", "django", "fastapi", "requests", "numpy", "pandas",
        "pip", "python3-pip", "python-pip",
    ]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PROGRESS_STALL
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        # Check recent tool calls for slow installation patterns
        for tc in state.recent_tool_calls[-5:]:
            if tc.get("tool_name") != "execute_command":
                continue
            
            cmd = str(tc.get("arguments", {}).get("command", "")).lower()
            
            # Check for slow package manager usage
            for pattern in self.SLOW_INSTALL_PATTERNS:
                if pattern in cmd:
                    # Check if this is installing something available in venv
                    for pkg in self.COMMON_VENV_PACKAGES:
                        if pkg in cmd:
                            return self._create_pattern(
                                state,
                                confidence=0.90,
                                severity="high",
                                description=f"Agent using slow package manager ({pattern}) for '{pkg}' - faster alternatives exist",
                                metadata={
                                    "pattern_id": "ENV-001",
                                    "command": cmd[:200],
                                    "package": pkg,
                                    "suggestion": f"Use existing venv: /opt/venv-agix/bin/pip install {pkg} or check if already installed",
                                    "known_venvs": self.KNOWN_VENVS,
                                },
                            )
                    
                    # General slow package manager detection
                    return self._create_pattern(
                        state,
                        confidence=0.75,
                        severity="medium",
                        description=f"Agent using system package manager ({pattern}) - consider using pip/venv instead",
                        metadata={
                            "pattern_id": "ENV-001",
                            "command": cmd[:200],
                            "suggestion": "Check if package is available via pip in existing venv",
                        },
                    )
        
        # Check recent errors for slow network patterns
        for error in state.recent_errors[-5:]:
            error_lower = error.lower()
            for pattern in self.SLOW_NETWORK_PATTERNS:
                if pattern in error_lower:
                    return self._create_pattern(
                        state,
                        confidence=0.85,
                        severity="high",
                        description=f"Slow network/mirror detected: '{pattern}' - consider alternative approach",
                        metadata={
                            "pattern_id": "ENV-002",
                            "error": error[:200],
                            "suggestion": "Use pre-installed packages or faster mirrors",
                        },
                    )
        
        # Check recent tool RESULTS (stdout) for slow network patterns
        # This catches cases where apt-get is "working" but slowly
        for result in state.recent_tool_results[-5:]:
            result_str = str(result).lower()
            for pattern in self.SLOW_NETWORK_PATTERNS:
                if pattern in result_str:
                    return self._create_pattern(
                        state,
                        confidence=0.90,
                        severity="critical",
                        description=f"Slow mirror/network detected in command output: '{pattern}'",
                        metadata={
                            "pattern_id": "ENV-002",
                            "output_excerpt": result_str[:300],
                            "suggestion": "STOP: Use pre-installed packages from /opt/venv-agix or pip instead of apt-get. The mirror is slow and will waste time.",
                        },
                    )
        
        return None


class RedundantDependencyInstallDetector(PatternDetector):
    """
    ENV-003: Detects when agent tries to install already-available dependencies.
    
    Monitors for patterns where agent installs packages that are already
    present in the environment, wasting time and resources.
    """
    
    # Commands that check for existing packages
    CHECK_COMMANDS = [
        "which", "whereis", "pip show", "pip list", "dpkg -l",
        "rpm -q", "python -c \"import",
    ]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PROGRESS_STALL
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        # Look for install commands without prior check
        install_without_check = False
        has_check = False
        
        for tc in state.recent_tool_calls[-10:]:
            if tc.get("tool_name") != "execute_command":
                continue
            
            cmd = str(tc.get("arguments", {}).get("command", "")).lower()
            
            # Check if this is a check command
            if any(check in cmd for check in self.CHECK_COMMANDS):
                has_check = True
            
            # Check if this is an install command
            if "install" in cmd and ("pip" in cmd or "apt" in cmd or "npm" in cmd):
                if not has_check:
                    install_without_check = True
        
        if install_without_check:
            return self._create_pattern(
                state,
                confidence=0.70,
                severity="medium",
                description="Agent installing packages without checking if already available",
                metadata={
                    "pattern_id": "ENV-003",
                    "suggestion": "Check for existing packages before installing: pip show <pkg>, which <cmd>, etc.",
                },
            )
        
        return None


class VenvUnawarenessDetector(PatternDetector):
    """
    ENV-004: Detects when agent doesn't use available virtual environments.
    
    The container has pre-configured venvs that should be used instead of
    system Python or installing new packages globally.
    """
    
    # Patterns indicating system Python usage when venv should be used
    SYSTEM_PYTHON_PATTERNS = [
        "python3 -m pip",
        "pip3 install",
        "sudo pip",
        "/usr/bin/python",
        "/usr/bin/pip",
    ]
    
    # Known venv paths in the container
    CONTAINER_VENVS = {
        "/opt/venv-agix": ["flask", "requests", "boto3", "python-docx"],
    }
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PROGRESS_STALL
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        for tc in state.recent_tool_calls[-5:]:
            if tc.get("tool_name") != "execute_command":
                continue
            
            cmd = str(tc.get("arguments", {}).get("command", ""))
            cmd_lower = cmd.lower()
            
            # Check for system Python usage
            for pattern in self.SYSTEM_PYTHON_PATTERNS:
                if pattern in cmd_lower:
                    return self._create_pattern(
                        state,
                        confidence=0.85,
                        severity="high",
                        description=f"Agent using system Python ({pattern}) instead of container venv",
                        metadata={
                            "pattern_id": "ENV-004",
                            "command": cmd[:200],
                            "suggestion": "Use /opt/venv-agix/bin/python and /opt/venv-agix/bin/pip instead",
                            "available_venvs": list(self.CONTAINER_VENVS.keys()),
                        },
                    )
        
        return None


__all__ = [
    "InefficientInstallationDetector",
    "RedundantDependencyInstallDetector",
    "VenvUnawarenessDetector",
]