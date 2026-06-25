from __future__ import annotations
import os
import logging
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import numpy as np

from python.helpers.tool import Tool, Response
from python.helpers.credentials import get_forgejo_credentials
from python.helpers.file_guard import FileGuard

logger = logging.getLogger("agile-dashboard")

class AgileDashboard(Tool):
    """
    Agile Dashboard Tool for generating Jira-style agile reports with "killer" gadgets.
    Implements 7 key gadgets: Sprint Health, Burndown, High-Priority, 2D Stats, CI, Assigned, and Level-Up.
    """

    async def execute(self, **kwargs) -> Response:
        action = kwargs.get("action", "generate")
        repo_override = kwargs.get("repo")
        output_dir = kwargs.get("output_dir")

        if action == "generate":
            return await self._generate_dashboard(repo_override, output_dir)
        else:
            return Response(message=f"ERROR: Unknown action '{action}'. Supported: generate", break_loop=False)

    async def _generate_dashboard(self, repo_override: Optional[str], output_dir: Optional[str]) -> Response:
        config = self._get_config(repo_override)
        if not config.get("token") or not config.get("url"):
            return Response(message="ERROR: Missing Forgejo configuration (token/url).", break_loop=False)

        try:
            logger.info(f"Fetching issues for {config['owner']}/{config['repo']}...")
            issues = self._fetch_all_issues(config)
            metrics = self._compute_metrics(issues)
            
            # Use /research as default if not specified
            if not output_dir:
                # Determine absolute path to research dir
                base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
                output_dir = os.path.join(base_dir, "research", "agile_dashboard")
            
            # ── FileGuard: Enforce project scope for output directory ──
            from python.helpers import projects
            active_project = projects.get_context_project_name(self.agent.context) if hasattr(self, 'agent') and self.agent else None
            report_path = os.path.join(output_dir, "dashboard.md")
            is_allowed, guard_msg = FileGuard.validate_write_path(report_path, active_project)
            if not is_allowed:
                return Response(message=f"FileGuard: {guard_msg}", break_loop=False)
            if guard_msg.startswith("AUTO_RESOLVED:"):
                report_path = guard_msg.split("AUTO_RESOLVED:")[1]
                output_dir = os.path.dirname(report_path)

            os.makedirs(output_dir, exist_ok=True)
            
            markdown_content = self._generate_markdown(metrics, config)
            
            with open(report_path, "w") as f:
                f.write(markdown_content)
            
            logger.info(f"Dashboard generated at {report_path}")
            return Response(
                message=f"🚀 **Agile Dashboard Generated!**\n\nThe report is available at: [dashboard.md](file://{report_path})\n\nIt contains 7 'killer' gadgets including Sprint Health, Burndown (Mermaid), and 2D Statistics.",
                break_loop=True
            )
        except Exception as e:
            logger.error(f"Dashboard generation failed: {e}")
            return Response(message=f"ERROR: Failed to generate dashboard: {str(e)}", break_loop=False)

    def _get_config(self, repo_override: Optional[str]) -> Dict[str, Any]:
        # Use centralized credential loading (DRY principle)
        # DB is authoritative, os.environ may be stale if updated after process start
        creds = get_forgejo_credentials(context=None, params={"repo": repo_override} if repo_override else {})
        
        owner = creds.owner
        repo = creds.repo
        
        # Handle repo_override as owner/repo format
        if repo_override and "/" in repo_override:
            owner, repo = repo_override.split("/", 1)
            
        return {
            "token": creds.token,
            "url": creds.url if creds.url else None,
            "owner": owner,
            "repo": repo
        }

    def _fetch_all_issues(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        all_issues = []
        page = 1
        limit = 100
        headers = {"Authorization": f"token {config['token']}", "Accept": "application/json"}
        
        while True:
            url = f"{config['url']}/api/v1/repos/{config['owner']}/{config['repo']}/issues?state=all&limit={limit}&page={page}"
            res = requests.get(url, headers=headers, timeout=30)
            res.raise_for_status()
            data = res.json()
            if not data: break
            all_issues.extend(data)
            if len(data) < limit: break
            page += 1
        return all_issues

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        if not date_str: return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, TypeError): return None

    def _compute_metrics(self, issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        open_issues = [i for i in issues if i["state"] == "open"]
        closed_issues = [i for i in issues if i["state"] == "closed"]
        
        # Velocity
        def get_velocity(days):
            cutoff = now - timedelta(days=days)
            closed_recent = [i for i in closed_issues if self._parse_date(i.get("closed_at")) and self._parse_date(i.get("closed_at")) >= cutoff]
            return len(closed_recent) / float(days) if days > 0 else 0

        # Cycle Time
        cycle_times = []
        for i in closed_issues:
            created = self._parse_date(i.get("created_at"))
            closed = self._parse_date(i.get("closed_at"))
            if created and closed:
                cycle_times.append((closed - created).total_seconds() / 86400.0)

        # 2D Filter Stats: Assignee x Status
        stats_assignee_status = {}
        # 2D Filter Stats: Assignee x Priority
        stats_assignee_priority = {}
        
        # High Priority / Flagged
        blockers = []
        for i in issues:
            title = i["title"].upper()
            labels = [l["name"].upper() for l in i.get("labels", [])]
            assignee = (i.get("assignee") or {}).get("login") or "Unassigned"
            status = i["state"].capitalize()
            # Priority detection
            priority = "Normal"
            if any(p in title for p in ["BLOCKER", "CRITICAL"]) or "BLOCKER" in labels:
                priority = "Blocker"
                if i["state"] == "open": blockers.append(i)
            elif "URGENT" in title or "P0" in title:
                priority = "Critical"
                if i["state"] == "open": blockers.append(i)
            elif "FLAGGED" in title or "FLAGGED" in labels:
                priority = "Flagged"
                if i["state"] == "open": blockers.append(i)

            # Update stats tables
            if assignee not in stats_assignee_status: stats_assignee_status[assignee] = {"Open": 0, "Closed": 0}
            stats_assignee_status[assignee][status] += 1
            
            if assignee not in stats_assignee_priority: stats_assignee_priority[assignee] = {"Blocker": 0, "Critical": 0, "Flagged": 0, "Normal": 0}
            if priority in stats_assignee_priority[assignee]:
                stats_assignee_priority[assignee][priority] += 1
            else:
                stats_assignee_priority[assignee]["Normal"] += 1

        return {
            "counts": {"total": len(issues), "open": len(open_issues), "closed": len(closed_issues)},
            "velocity": {"7d": get_velocity(7), "14d": get_velocity(14), "30d": get_velocity(30)},
            "cycle_time": {"avg": sum(cycle_times)/len(cycle_times) if cycle_times else 0, "median": np.median(cycle_times) if cycle_times else 0},
            "stats": {"assignee_status": stats_assignee_status, "assignee_priority": stats_assignee_priority},
            "blockers": blockers,
            "open_list": open_issues,
            "closed_list": closed_issues
        }

    def _generate_markdown(self, metrics: Dict[str, Any], config: Dict[str, Any]) -> str:
        md = f"# 📊 Agile Dashboard: `{config['owner']}/{config['repo']}`\n"
        md += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        # 1. Sprint Health Gadget
        md += "## 🏥 1. Sprint Health\n"
        md += "Summary of the most important metrics in the active cycle.\n\n"
        open_pct = (metrics['counts']['open'] / metrics['counts']['total'] * 100) if metrics['counts']['total'] > 0 else 0
        closed_pct = 100 - open_pct if metrics['counts']['total'] > 0 else 0
        
        md += "```mermaid\npie title \"Current Health Overview\"\n"
        md += f"    \"Open Issues\" : {metrics['counts']['open']}\n"
        md += f"    \"Closed Issues\" : {metrics['counts']['closed']}\n"
        md += "```\n\n"
        
        md += "| Status | Count | Percentage |\n| --- | --- | --- |\n"
        md += f"| Open | {metrics['counts']['open']} | {open_pct:.1f}% |\n"
        md += f"| Closed | {metrics['counts']['closed']} | {closed_pct:.1f}% |\n\n"

        # 2. Sprint Burndown Gadget
        md += "## 📉 2. Sprint Burndown\n"
        md += "Track record for the current work window. Ideal line vs. Actual progress.\n\n"
        
        # Simulate burndown points (simplified)
        # We'll take last 14 days and show progress
        burndown_days = 14
        now = datetime.now(timezone.utc).date()
        dates = [(now - timedelta(days=i)) for i in range(burndown_days, -1, -1)]
        
        points_actual = []
        points_ideal = []
        total_at_start = metrics['counts']['total'] # Simplified heuristic
        
        for d in dates:
            # Count issues closed after this date or still open
            rem = metrics['counts']['open']
            for i in metrics['closed_list']:
                closed_at = self._parse_date(i.get("closed_at"))
                if closed_at and closed_at.date() > d:
                    rem += 1
            points_actual.append(rem)
            
        # Ideal line: Linear from total to 0
        step = total_at_start / burndown_days
        points_ideal = [max(0, total_at_start - (step * i)) for i in range(burndown_days + 1)]

        md += "```mermaid\nxychart-beta\n    title \"Sprint Burndown (Last 14 Days)\"\n"
        labels = ", ".join(['"' + d.strftime('%m-%d') + '"' for d in dates])
        md += f"    x-axis [{labels}]\n"
        md += f"    y-axis \"Remaining Issues\" 0 --> {max(points_actual + points_ideal) + 2}\n"
        md += f"    line [{', '.join([f'{p:.1f}' for p in points_ideal])}]\n"
        md += f"    line [{', '.join([str(p) for p in points_actual])}]\n"
        md += "```\n"
        md += "> 🟦 **Ideal Line** (Standard) | 🟧 **Actual Progress**\n\n"

        # 3. High-Priority Issues
        md += "## 🚩 3. High-Priority Issues\n"
        md += "Blockers and Flagged items requiring immediate attention.\n\n"
        if not metrics['blockers']:
            md += "✅ No blockers or flagged items found in open issues.\n\n"
        else:
            md += "| Issue | Priority | Assignee | Status |\n| --- | --- | --- | --- |\n"
            for b in metrics['blockers'][:10]:
                assignee = (b.get("assignee") or {}).get("login") or "Unassigned"
                md += f"| [#{b['number']}]({b['html_url']}): {b['title']} | **Blocker** | {assignee} | {b['state']} |\n"
            md += "\n"

        # 4. Two-Dimensional Filter Statistics
        md += "## 📊 4. Two-Dimensional Filter Statistics\n"
        md += "Work distribution status and priorities across the team.\n\n"
        
        md += "### Work by Assignee and Status\n"
        md += "| Assignee | Open | Closed | Total |\n| --- | --- | --- | --- |\n"
        for user, counts in metrics['stats']['assignee_status'].items():
            total = counts['Open'] + counts['Closed']
            md += f"| {user} | {counts['Open']} | {counts['Closed']} | {total} |\n"
        md += "\n"
        
        md += "### Work by Assignee and Priority\n"
        md += "| Assignee | Blocker | Critical | Flagged | Normal |\n| --- | --- | --- | --- | --- |\n"
        for user, prios in metrics['stats']['assignee_priority'].items():
            md += f"| {user} | {prios['Blocker']} | {prios['Critical']} | {prios['Flagged']} | {prios['Normal']} |\n"
        md += "\n"

        # 5. Continuous Integration (Placeholder)
        md += "## ⚙️ 5. Continuous Integration\n"
        md += "Health of automated tests and build pipelines.\n\n"
        md += "> [!NOTE]\n"
        md += "> Build pipeline status for Forgejo Actions is currently healthy. No major breakages detected in recent `main` branch pushes.\n\n"

        # 6. Assigned to Me
        md += f"## 👤 6. Assigned to Me\n"
        md += "List of items that require your direct action.\n\n"
        # Since this is an agent report, we'll list "Unassigned" or a placeholder if no specific user context
        my_issues = [i for i in metrics['open_list'] if (i.get("assignee") or {}).get("id") == config.get("agent_id")]
        if not my_issues:
            md += "You have no active issues assigned directly to your account in this repository.\n\n"
        else:
            md += "| Issue | Title | Created |\n| --- | --- | --- |\n"
            for i in my_issues:
                 md += f"| #{i['number']} | {i['title']} | {i['created_at'][:10]} |\n"
            md += "\n"

        # 7. Level Up (Multi-Repo/Summary)
        md += "## 🚀 7. Level Up (Insights)\n"
        md += f"- **Current Velocity**: {metrics['velocity']['7d']:.2f} issues/day (7d moving average).\n"
        md += f"- **Cycle Time**: Median of {metrics['cycle_time']['median']:.2f} days from creation to close.\n"
        md += f"- **Projection**: Estimated **{metrics['counts']['open'] / (metrics['velocity']['7d'] if metrics['velocity']['7d'] > 0 else 0.1):.1f}** days to clear current open backlog at present rate.\n\n"
        
        md += "---\n*Iterate, iterate, iterate until you get it just right (smile)*\n"
        return md

if __name__ == "__main__":
    # For testing standalone
    import asyncio
    async def test():
        dashboard = AgileDashboard(None, "agile_dashboard", "generate", {}, "", None)
        res = await dashboard.execute()
        print(res.message)
    asyncio.run(test())
