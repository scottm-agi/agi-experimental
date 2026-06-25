"""
Sweep coordination module for repository automation.
Contains functions for sweep status management and cursor tracking.
"""
from __future__ import annotations
import re
import json
from typing import Dict, Any, List, TYPE_CHECKING
from datetime import datetime, timezone

from .base import logger

if TYPE_CHECKING:
    from python.helpers.task_state import TaskStateManager


class SweepCoordinator:
    """
    Coordinates sweep operations across GitHub/Forgejo issues.
    Manages GH_LAST_ID cursor for descending sweep cycles.
    """
    
    def __init__(self, tsm: "TaskStateManager" = None, context=None):
        """
        Initialize sweep coordinator.
        
        Args:
            tsm: TaskStateManager instance for state tracking
            context: Agent context for parameters access
        """
        self.tsm = tsm
        self.context = context
    
    def get_current_cursor(self) -> int:
        """Get current GH_LAST_ID cursor value."""
        try:
            from python.helpers.parameters import get_parameters_manager
            pm = get_parameters_manager(self.context)
            parameters = pm.load_parameters()
            
            cursor = parameters.get("GH_LAST_ID", 999999)
            if isinstance(cursor, str):
                try:
                    return int(cursor)
                except ValueError:
                    return 999999
            return cursor
        except Exception as e:
            logger.debug(f"Failed to get cursor: {e}")
            return 999999
    
    def set_cursor(self, value: int) -> bool:
        """
        Set GH_LAST_ID cursor to a new value.
        
        Args:
            value: New cursor value
        
        Returns:
            True if successful
        """
        try:
            from python.helpers.parameters import get_parameters_manager
            pm = get_parameters_manager(self.context)
            pm.set_parameter("GH_LAST_ID", str(value))
            return True
        except Exception as e:
            logger.error(f"Failed to set cursor: {e}")
            return False
    
    def reset_cursor(self, highest_open_id: int) -> Dict[str, Any]:
        """
        Reset cursor to highest open issue ID for a new sweep cycle.
        
        Args:
            highest_open_id: Highest open issue number to start from
        
        Returns:
            Dict with old_cursor, new_cursor, timestamp
        """
        try:
            from python.helpers.parameters import get_parameters_manager
            
            pm = get_parameters_manager(self.context)
            old_cursor = self.get_current_cursor()
            
            # Update cursor
            pm.set_parameter("GH_LAST_ID", str(highest_open_id))
            
            # Record reset timestamp
            reset_timestamp = datetime.now(timezone.utc).isoformat()
            pm.set_parameter("GH_LAST_ID_RESET_AT", reset_timestamp)
            pm.set_parameter("GH_LAST_ID_RESET_FROM", str(old_cursor))
            
            logger.info(f"[reset_cursor] Reset GH_LAST_ID from {old_cursor} to {highest_open_id}")
            
            return {
                "old_cursor": old_cursor,
                "new_cursor": highest_open_id,
                "reset_timestamp": reset_timestamp
            }
        except Exception as e:
            logger.error(f"[reset_cursor] Error: {e}")
            raise
    
    def check_status(self, issue_numbers: List[int]) -> Dict[str, Any]:
        """
        Check sweep status given a list of open issue numbers.
        
        Args:
            issue_numbers: List of open issue numbers
        
        Returns:
            Status dict with cursor info and sweep state
        """
        if not issue_numbers:
            return {
                "current_cursor": self.get_current_cursor(),
                "lowest_open_id": None,
                "highest_open_id": None,
                "open_issue_count": 0,
                "is_complete": True,
                "needs_reset": False,
                "status": "NO_OPEN_ISSUES"
            }
        
        current_cursor = self.get_current_cursor()
        lowest_open_id = min(issue_numbers)
        highest_open_id = max(issue_numbers)
        
        # Sweep is complete when cursor has gone below the lowest open issue
        is_complete = current_cursor < lowest_open_id
        
        # Needs reset if sweep is complete OR if cursor is way above highest
        needs_reset = is_complete or (current_cursor > highest_open_id + 100)
        
        return {
            "current_cursor": current_cursor,
            "lowest_open_id": lowest_open_id,
            "highest_open_id": highest_open_id,
            "open_issue_count": len(issue_numbers),
            "is_complete": is_complete,
            "needs_reset": needs_reset,
            "status": "SWEEP_COMPLETE" if is_complete else "SWEEP_IN_PROGRESS"
        }
    
    def invalidate_cache(self) -> Dict[str, Any]:
        """
        Clear the TSM handled_issue sets for fresh re-assessment.
        
        Returns:
            Dict with cleared keys
        """
        if not self.tsm:
            return {"cleared_keys": [], "error": "TSM not available"}
        
        cleared_keys = []
        
        # Clear GitHub handled issues
        try:
            gh_handled = self.tsm.get_value("handled_issue_gh")
            if gh_handled:
                self.tsm.set_value("handled_issue_gh", set())
                cleared_keys.append(f"handled_issue_gh (had {len(gh_handled) if isinstance(gh_handled, set) else 1} entries)")
        except Exception as e:
            logger.debug(f"Failed to clear handled_issue_gh: {e}")
        
        # Clear Forgejo handled issues
        try:
            fj_handled = self.tsm.get_value("handled_issue_fj")
            if fj_handled:
                self.tsm.set_value("handled_issue_fj", set())
                cleared_keys.append(f"handled_issue_fj (had {len(fj_handled) if isinstance(fj_handled, set) else 1} entries)")
        except Exception as e:
            logger.debug(f"Failed to clear handled_issue_fj: {e}")
        
        # Save state
        self.tsm.save()
        
        return {"cleared_keys": cleared_keys}
    
    def is_issue_handled(self, provider: str, issue_number: int) -> bool:
        """
        Check if an issue is marked as handled in TSM.
        
        Args:
            provider: Provider type (github/forgejo)
            issue_number: Issue number
        
        Returns:
            True if handled
        """
        if not self.tsm:
            return False
        
        try:
            key = f"handled_issue_{'gh' if provider == 'github' else 'fj'}"
            return self.tsm.is_tracked(key, str(issue_number))
        except Exception:
            return False
    
    def mark_issue_handled(self, provider: str, issue_number: int) -> bool:
        """
        Mark an issue as handled in TSM.
        
        Args:
            provider: Provider type (github/forgejo)
            issue_number: Issue number
        
        Returns:
            True if successful
        """
        if not self.tsm:
            return False
        
        try:
            key = f"handled_issue_{'gh' if provider == 'github' else 'fj'}"
            self.tsm.track_id(key, str(issue_number))
            self.tsm.save()
            return True
        except Exception as e:
            logger.debug(f"Failed to mark issue handled: {e}")
            return False
    
    def get_sweep_lock(self, provider: str, owner: str, repo: str, issue_number: int) -> bool:
        """
        Acquire a distributed lock for sweep processing.
        Uses Redis SETNX for atomic lock acquisition.
        
        Args:
            provider: Provider type
            owner: Repository owner
            repo: Repository name
            issue_number: Issue number
        
        Returns:
            True if lock acquired, False if already locked
        """
        try:
            from python.helpers.redis_helper import get_redis_connection
            redis_client = get_redis_connection()
            
            lock_key = f"expert_lock:{provider}:{owner}:{repo}:{issue_number}"
            lock_acquired = redis_client.setnx(lock_key, "processing")
            
            if lock_acquired:
                # Set TTL to prevent orphaned locks (5 minutes)
                redis_client.expire(lock_key, 300)
                logger.info(f"[SweepCoordinator] Acquired lock for #{issue_number}")
                return True
            else:
                logger.info(f"[SweepCoordinator] Skipping #{issue_number} - locked by parallel process")
                return False
        except Exception as e:
            logger.warning(f"[SweepCoordinator] Redis lock failed for #{issue_number}: {e}")
            # If Redis fails, proceed (best effort)
            return True
    
    def release_sweep_lock(
        self, 
        provider: str, 
        owner: str, 
        repo: str, 
        issue_number: int,
        success: bool = True
    ):
        """
        Release a distributed lock after sweep processing.
        
        Args:
            provider: Provider type
            owner: Repository owner
            repo: Repository name
            issue_number: Issue number
            success: Whether processing was successful
        """
        try:
            from python.helpers.redis_helper import get_redis_connection
            redis_client = get_redis_connection()
            
            lock_key = f"expert_lock:{provider}:{owner}:{repo}:{issue_number}"
            
            if success:
                # Keep key but mark as done (24h TTL)
                redis_client.set(lock_key, "done", ex=86400)
            else:
                # Delete lock on failure to allow retry
                redis_client.delete(lock_key)
        except Exception as e:
            logger.warning(f"[SweepCoordinator] Failed to release lock for #{issue_number}: {e}")


def parse_issue_numbers_from_list(issues_text: str) -> List[int]:
    """
    Parse issue numbers from a list_issues response.
    
    Args:
        issues_text: Text response from list_issues
    
    Returns:
        List of issue numbers
    """
    issue_numbers = []
    for line in issues_text.split("\n"):
        match = re.search(r'\*\*#(\d+)\*\*', line)
        if match:
            issue_numbers.append(int(match.group(1)))
    return issue_numbers


def build_sweep_summary(
    owner: str,
    repo: str,
    issues_scanned: int,
    responded: List[str],
    skipped: List[str],
    errors: List[str]
) -> str:
    """
    Build a formatted summary of sweep results.
    
    Args:
        owner: Repository owner
        repo: Repository name
        issues_scanned: Number of issues scanned
        responded: List of responded issue IDs
        skipped: List of skipped reasons
        errors: List of error messages
    
    Returns:
        Formatted markdown summary
    """
    return f"""## 🔍 Sweep Complete

**Repository**: {owner}/{repo}
**Issues Scanned**: {issues_scanned}
**Responses Posted**: {len(responded)}

### ✅ Responded ({len(responded)})
{', '.join(responded) if responded else 'None'}

### ⏭️ Skipped ({len(skipped)})
{chr(10).join(skipped[:10]) if skipped else 'None'}
{f'... and {len(skipped) - 10} more' if len(skipped) > 10 else ''}

### ❌ Errors ({len(errors)})
{chr(10).join(errors) if errors else 'None'}
"""


def build_expert_sweep_summary(
    owner: str,
    repo: str,
    issues_scanned: int,
    analyzed: List[str],
    skipped: List[str],
    errors: List[str]
) -> str:
    """
    Build a formatted summary of expert analysis sweep results.
    
    Args:
        owner: Repository owner
        repo: Repository name
        issues_scanned: Number of issues scanned
        analyzed: List of analyzed issue IDs
        skipped: List of skipped reasons
        errors: List of error messages
    
    Returns:
        Formatted markdown summary
    """
    return f"""## 🎯 Expert Analysis Sweep Complete

**Repository**: {owner}/{repo}
**Issues Scanned**: {issues_scanned}
**Analyses Posted**: {len(analyzed)}

### ✅ Analyzed ({len(analyzed)})
{', '.join(analyzed) if analyzed else 'None'}

### ⏭️ Skipped ({len(skipped)})
{chr(10).join(skipped[:10]) if skipped else 'None'}
{f'... and {len(skipped) - 10} more' if len(skipped) > 10 else ''}

### ❌ Errors ({len(errors)})
{chr(10).join(errors) if errors else 'None'}
"""


def check_for_expert_analysis_tag(body: str) -> bool:
    """
    Check if body contains expert analysis markers.
    
    Args:
        body: Comment or issue body
    
    Returns:
        True if expert analysis markers found
    """
    markers = [
        "<!-- expert-analysis:",
        "# 🎯 AGIX - Expert Issue Analysis",
        "# 🎯 Expert Solution Analysis:",
        "## 🏗️ Architecture Analysis (Architect",
    ]
    return any(marker in body for marker in markers)


def check_for_build_trigger(body: str) -> bool:
    """
    Check if comment contains build trigger phrase.
    
    Args:
        body: Comment body
    
    Returns:
        True if build trigger found
    """
    if not body: return False
    body_lower = body.lower()
    # Support "AGIX Build Branch", "AGIX Build Branch", "AGIX Build", "AGIX Build"
    import re
    return bool(re.search(r"(?i)witha(i|gi)\s+build", body_lower))
def check_for_integration_trigger(body: str) -> bool:
    """
    Check if comment contains integration approval marker.
    
    Args:
        body: Comment body
    
    Returns:
        True if integration trigger found
    """
    return "#approved" in body.lower()
def check_for_merge_trigger(body: str) -> bool:
    """
    Check if comment contains merge trigger phrase.
    
    Args:
        body: Comment body
    
    Returns:
        True if merge trigger found
    """
    # Pattern: @agix merge all, @agix merge #123, etc.
    pattern = r"(?i)(agix|agix)\s+merge(\s+all|\s+#\d+)*"
    return bool(re.search(pattern, body.lower()))

def check_for_deploy_trigger(body: str) -> bool:
    """
    Check if comment contains deploy trigger phrase.
    
    Args:
        body: Comment body
    
    Returns:
        True if deploy trigger found
    """
    # Pattern: "AGIX deploy", "agix deploy"
    pattern = r"(?i)(agix|agix)\s+deploy"
    return bool(re.search(pattern, body.lower()))

def check_for_monitor_trigger(body: str) -> bool:
    """
    Check if comment contains monitor trigger phrase.
    
    Args:
        body: Comment body
    
    Returns:
        True if monitor trigger found
    """
    # Pattern: "AGIX monitor", "agix monitor"
    pattern = r"(?i)(agix|agix)\s+monitor"
    return bool(re.search(pattern, body.lower()))
