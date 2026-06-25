from __future__ import annotations
"""
Dynamic Code Patterns Registry - Configurable search patterns for code analysis.

This module provides:
1. Language-specific search patterns
2. Infrastructure file detection
3. Pattern expansion based on feedback
4. Integration with analysis_feedback for learning
"""

from typing import Dict, List, Set, Optional
import re
import logging

logger = logging.getLogger(__name__)


# Language-specific definition patterns
LANGUAGE_PATTERNS = {
    "python": {
        "definitions": r"(def|class|async def)\s+{symbol}",
        "imports": r"(from|import)\s+.*{symbol}",
        "usage": r"{symbol}\s*[\(\.\[]",
        "extensions": [".py"],
    },
    "javascript": {
        "definitions": r"(function|const|let|var|class)\s+{symbol}",
        "imports": r"(import|require)\s*[\(\{].*{symbol}",
        "usage": r"{symbol}\s*[\(\.\[]",
        "extensions": [".js", ".jsx", ".mjs"],
    },
    "typescript": {
        "definitions": r"(function|const|let|interface|type|class|enum)\s+{symbol}",
        "imports": r"import\s+.*{symbol}",
        "usage": r"{symbol}\s*[\(\.\[]",
        "extensions": [".ts", ".tsx"],
    }
}

# Infrastructure files by domain — M-HC-6: Made framework-agnostic
# Each category includes files for Python, TypeScript/JS, Ruby, Go, and Rust
INFRASTRUCTURE_FILES = {
    "parameters_secrets": {
        "files": [
            # Python
            "parameters.py", "secrets.py", "config_db.py", "settings.py",
            "config.py", "env.py", "constants.py",
            # TypeScript/JS
            "config.ts", "constants.ts", "env.ts", "config.js",
            # Framework-agnostic
            ".env", ".env.local", ".env.production", "config.yaml", "config.toml",
        ],
        "keywords": ["parameter", "secret", "config", "settings", "env", "credential"],
        "description": "Parameter and secrets management"
    },
    "database": {
        "files": [
            # Python
            "models.py", "schema.py", "db.py", "database.py", "orm.py",
            # TypeScript/JS
            "schema.ts", "schema.prisma", "drizzle.config.ts", "db.ts",
            "models.ts", "entities.ts",
            # Framework-agnostic
            "migrations/", "entities/",
        ],
        "keywords": ["database", "sql", "model", "schema", "migration", "table", "prisma", "drizzle"],
        "description": "Database and ORM patterns"
    },
    "api_routing": {
        "files": [
            # Python
            "routes.py", "router.py", "api.py", "endpoints.py", "views.py",
            # TypeScript/JS (Next.js / Express)
            "route.ts", "route.js", "api/", "pages/api/", "app/api/",
            # Framework-agnostic
            "controllers/", "handlers/",
        ],
        "keywords": ["api", "route", "endpoint", "request", "response", "http", "handler"],
        "description": "API routing and handlers"
    },
    "event_system": {
        "files": [
            "event_bus.py", "events.py", "signals.py", "pubsub.py",
            "handlers.py", "listeners.py",
            "events.ts", "event-bus.ts", "hooks.ts",
        ],
        "keywords": ["event", "signal", "emit", "subscribe", "handler", "listener", "hook"],
        "description": "Event/signal handling"
    },
    "scheduling": {
        "files": [
            "scheduler.py", "tasks.py", "jobs.py", "cron.py", "celery.py",
            "cron.ts", "jobs.ts", "worker.ts", "queue.ts",
        ],
        "keywords": ["schedule", "task", "job", "cron", "periodic", "background", "worker", "queue"],
        "description": "Background tasks and scheduling"
    },
    "authentication": {
        "files": [
            "auth.py", "authentication.py", "jwt.py", "oauth.py", "session.py",
            "auth.ts", "middleware.ts", "auth.config.ts",
        ],
        "keywords": ["auth", "login", "token", "jwt", "oauth", "session", "csrf", "middleware"],
        "description": "Authentication and authorization"
    },
    "helpers_utils": {
        "files": [
            "helpers/", "utils/", "common/", "shared/", "lib/",
        ],
        "keywords": ["helper", "util", "common", "shared", "lib"],
        "description": "Helper and utility modules"
    },
    "mcp_tools": {
        "files": [
            "mcp/", "tools/", "agents/", "extensions/", "plugins/",
        ],
        "keywords": ["mcp", "tool", "agent", "extension", "plugin"],
        "description": "MCP servers and tools"
    }
}


class CodePatternRegistry:
    """
    Manages search patterns for code analysis.
    
    Combines:
    - Static language patterns
    - Infrastructure patterns
    - Learned patterns from feedback
    """
    
    def __init__(self):
        self._custom_patterns: Dict[str, List[str]] = {}
        self._learned_files: Set[str] = set()
    
    def get_search_patterns(
        self, 
        issue_text: str, 
        tech_stack: List[str] = None,
        include_learned: bool = True
    ) -> Dict[str, List[str]]:
        """
        Get relevant search patterns based on issue context.
        
        Args:
            issue_text: The issue description/title
            tech_stack: Detected technologies (e.g., ["Python", "Docker"])
            include_learned: Whether to include learned patterns from feedback
            
        Returns:
            Dict with keys: 'patterns', 'files', 'extensions'
        """
        result = {
            "patterns": [],
            "files": [],
            "extensions": []
        }
        
        issue_lower = issue_text.lower()
        
        # 1. Add language-specific patterns
        if tech_stack:
            for tech in tech_stack:
                tech_key = tech.lower()
                if tech_key in LANGUAGE_PATTERNS:
                    lang_data = LANGUAGE_PATTERNS[tech_key]
                    result["patterns"].extend([
                        lang_data["definitions"],
                        lang_data["imports"],
                        lang_data["usage"]
                    ])
                    result["extensions"].extend(lang_data["extensions"])
        
        # 2. Add infrastructure files based on keywords
        for infra_name, infra_data in INFRASTRUCTURE_FILES.items():
            if any(kw in issue_lower for kw in infra_data["keywords"]):
                result["files"].extend(infra_data["files"])
                logger.debug(f"Added {infra_name} patterns due to keyword match")
        
        # 3. Add learned patterns from feedback
        if include_learned:
            try:
                from python.helpers.analysis_feedback import get_feedback_tracker
                tracker = get_feedback_tracker()
                
                # Get issue type from text (simple heuristic)
                issue_type = self._detect_issue_type(issue_text)
                suggestions = tracker.suggest_improvements(issue_type, tech_stack)
                result["files"].extend(suggestions)
            except ImportError:
                logger.debug("Feedback tracker not available")
            except Exception as e:
                logger.debug(f"Could not get learned patterns: {e}")
        
        # 4. Add custom patterns
        for key, patterns in self._custom_patterns.items():
            if key in issue_lower:
                result["patterns"].extend(patterns)
        
        # Deduplicate
        result["patterns"] = list(set(result["patterns"]))
        result["files"] = list(set(result["files"]))
        result["extensions"] = list(set(result["extensions"]))
        
        return result
    
    def _detect_issue_type(self, issue_text: str) -> str:
        """Simple issue type detection from text."""
        text_lower = issue_text.lower()
        if "pfr" in text_lower or "feature request" in text_lower:
            return "PFR"
        elif "bug" in text_lower or "fix" in text_lower or "error" in text_lower:
            return "Bug"
        elif "refactor" in text_lower:
            return "Refactor"
        return "General"
    
    def add_custom_pattern(self, keyword: str, patterns: List[str]):
        """Add custom pattern for a keyword trigger."""
        self._custom_patterns[keyword.lower()] = patterns
    
    def get_infrastructure_files(self, domain: str) -> List[str]:
        """Get files for a specific infrastructure domain."""
        if domain in INFRASTRUCTURE_FILES:
            return INFRASTRUCTURE_FILES[domain]["files"]
        return []
    
    def get_all_domains(self) -> List[str]:
        """List all infrastructure domains."""
        return list(INFRASTRUCTURE_FILES.keys())


# Singleton instance
_registry_instance: Optional[CodePatternRegistry] = None


def get_pattern_registry() -> CodePatternRegistry:
    """Get or create the global pattern registry."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = CodePatternRegistry()
    return _registry_instance


def get_patterns_for_issue(issue_text: str, tech_stack: List[str] = None) -> Dict[str, List[str]]:
    """
    Convenience function to get search patterns for an issue.
    
    Args:
        issue_text: Issue description/title
        tech_stack: Technologies involved
        
    Returns:
        Dict with 'patterns', 'files', 'extensions'
    """
    registry = get_pattern_registry()
    return registry.get_search_patterns(issue_text, tech_stack)
