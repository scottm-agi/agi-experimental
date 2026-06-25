from __future__ import annotations
import re
import httpx
import logging
import os
from typing import Optional, List, Dict, Any
from python.helpers.extension import Extension
from python.helpers.notification import NotificationManager, NotificationItem

logger = logging.getLogger(__name__)

class IssueClosureSuggestion(Extension):
    """
    Extension to suggest closing a Forgejo issue when an agent seems to have completed a task.
    """

    async def execute(self, **kwargs):
        try:
            # 1. Detect issue context
            issue_numbers = self._detect_issue_numbers()
            if not issue_numbers:
                return

            # 2. Check if the agent seems to have finished the task
            if not self._is_task_finished():
                return

            # 3. Get Forgejo config
            owner, repo, url, token = self._get_forgejo_config()
            if not all([owner, repo, url, token]):
                logger.debug("Forgejo configuration incomplete, skipping closure suggestion.")
                return

            # 4. For each detected issue, check status and suggest closure if open
            for issue_no in issue_numbers:
                is_open = await self._is_issue_open(issue_no, owner, repo, url, token)
                if is_open:
                    self._suggest_closure(issue_no, owner, repo)

        except Exception as e:
            logger.error(f"Error in IssueClosureSuggestion extension: {e}", exc_info=True)

    def _detect_issue_numbers(self) -> List[int]:
        """Detect issue numbers (e.g. #123) from chat history and parameters."""
        issue_numbers = set()

        # Check recent chat history (last few messages)
        # We look at both user and agent messages to find relevant issue context
        logs = self.agent.context.log.logs
        recent_logs = logs[-10:] # Check last 10 log items for better context
        for item in recent_logs:
            if item.content:
                matches = re.findall(r'#(\d+)', item.content)
                issue_numbers.update(int(m) for m in matches)

        # Check context parameters
        try:
            from python.helpers.parameters import get_parameters_manager
            pm = get_parameters_manager(self.agent.context.id)
            params = pm.load_parameters()
            if params.get("issue_id"):
                match = re.search(r'#(\d+)', str(params.get("issue_id")))
                if match:
                    issue_numbers.add(int(match.group(1)))
            elif params.get("issue_no"):
                try:
                    issue_numbers.add(int(params.get("issue_no")))
                except (ValueError, TypeError): pass
        except Exception as e:
            logger.debug(f"[ISSUE CLOSURE] Parameter access failed: {e}")
        
        # Check global parameters as fallback
        try:
            from python.helpers.parameters import get_parameters_manager
            gpm = get_parameters_manager()
            gparams = gpm.load_parameters()
            if gparams.get("REPO_ISSUE"):
                 match = re.search(r'#(\d+)', str(gparams.get("REPO_ISSUE")))
                 if match:
                     issue_numbers.add(int(match.group(1)))
        except Exception as e:
            logger.debug(f"[ISSUE CLOSURE] Parameter access failed: {e}")

        return list(issue_numbers)

    def _is_task_finished(self) -> bool:
        """Analyze agent's monologue for task completion indicators."""
        # Check if any recent agent message suggests task completion
        logs = self.agent.context.log.logs
        # Search backwards through logs
        for item in reversed(logs):
            if item.type == "response" and item.content:
                content_lower = item.content.lower()
                indicators = ["task complete", "finished", "fixed", "implemented", "resolved", "completed"]
                if any(ind in content_lower for ind in indicators):
                    return True
                # If we encounter a response that doesn't indicate completion, 
                # we keep looking for a bit, but usually the last response is the one.
                break
        return False

    def _get_forgejo_config(self):
        """Get Forgejo configuration from parameter/secrets manager."""
        try:
            from python.helpers.secrets_helper import get_secrets_manager
            from python.helpers.parameters import get_parameters_manager
            
            sm = get_secrets_manager()
            secrets = sm.load_secrets()
            token = secrets.get("FORGEJO_TOKEN") or os.getenv("FORGEJO_TOKEN", "")
            
            pm = get_parameters_manager()
            params = pm.load_parameters()
            
            owner = params.get("REPO_OWNER") or os.getenv("REPO_OWNER", "")
            repo = params.get("REPO_NAME") or os.getenv("REPO_NAME", "")
            url = params.get("FORGEJO_URL") or os.getenv("FORGEJO_URL", "")
            
            return owner, repo, url.rstrip('/'), token
        except Exception as e:
            logger.warning(f"Could not read Forgejo config: {e}")
            return None, None, None, None

    async def _is_issue_open(self, issue_no: int, owner: str, repo: str, url: str, token: str) -> bool:
        """Check if an issue is still open on Forgejo."""
        if not all([url, token, owner, repo]):
            return False
            
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/json"
        }
        api_url = f"{url}/api/v1/repos/{owner}/{repo}/issues/{issue_no}"
        
        try:
            # Use a short timeout for responsiveness
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(api_url, headers=headers)
                if response.status_code == 200:
                    issue = response.json()
                    return issue.get("state") == "open"
                elif response.status_code == 404:
                    logger.debug(f"Issue #{issue_no} not found in {owner}/{repo}")
        except Exception as e:
            logger.warning(f"Error checking issue #{issue_no} status: {e}")
        
        return False

    def _suggest_closure(self, issue_no: int, owner: str, repo: str):
        """Send a closure suggestion notification to the user."""
        try:
            from python.helpers import notification
            from python.agent import AgentContext
            
            nm = AgentContext.get_notification_manager()
            
            message = f"The agent seems to have completed the work related to issue **#{issue_no}** in **{owner}/{repo}**. Would you like to close this issue?"
            title = "Issue Closure Suggestion"
            
            nm.add_notification(
                type=notification.NotificationType.INFO,
                priority=notification.NotificationPriority.NORMAL,
                message=message,
                title=title,
                group=f"issue_closure_{issue_no}"
            )
            print(f"[ISSUE_CLOSURE] Suggesting closure for issue #{issue_no} in {owner}/{repo}", flush=True)
        except Exception as e:
            logger.error(f"Failed to add closure suggestion notification: {e}")
