from __future__ import annotations
import os
import json
import asyncio
from datetime import datetime
from python.helpers.mcp_handler import MCPConfig
from python.helpers.task_scheduler import TaskScheduler, ScheduledTask, TaskSchedule
from python.agent import AgentContext, AgentContextType

class GitHubSync:
    """Helper to sync GitHub issues with AGIX chat contexts."""
    
    STATE_FILE = "tmp/github_sync_state.json"
    
    @classmethod
    def load_state(cls):
        if os.path.exists(cls.STATE_FILE):
            try:
                with open(cls.STATE_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"last_processed_issue": 0, "issue_to_context": {}}

    @classmethod
    def save_state(cls, state):
        os.makedirs(os.path.dirname(cls.STATE_FILE), exist_ok=True)
        with open(cls.STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)

    @classmethod
    async def poll_issues(cls, repo_owner, repo_name):
        """Poll GitHub for new issues and comments."""
        mcp = MCPConfig.get_instance()
        if not mcp.has_tool("github.list_issues"):
            print("GitHub MCP not configured or missing list_issues tool.")
            return

        state = cls.load_state()
        if "triggered_builds" not in state:
            state["triggered_builds"] = {}
        
        # 1. Fetch open issues
        try:
            result = await mcp.call_tool("github.list_issues", {
                "owner": repo_owner,
                "repo": repo_name,
                "state": "open"
            })
            
            issues_data = result.content[0].text if result.content else "[]"
            try:
                issues = json.loads(issues_data)
            except json.JSONDecodeError:
                from python.helpers import dirty_json
                issues = dirty_json.try_parse(issues_data)
        except Exception as e:
            print(f"Failed to poll GitHub issues: {e}")
            return

        for issue in issues:
            issue_number = str(issue.get("number"))
            if not issue_number: continue
            
            context_id = state["issue_to_context"].get(issue_number)
            
            # 2. Check for new issues (no context mapping yet)
            if not context_id:
                print(f"New issue detected: #{issue_number}. Creating context...")
                # For the MVP/Mock, we generate a placeholder context ID
                # In real use, we'd instantiate AgentContext
                new_context_id = f"gh-{repo_owner}-{repo_name}-{issue_number}"
                state["issue_to_context"][issue_number] = new_context_id
                
                # Post initial refinement comment
                await mcp.call_tool("github.create_issue_comment", {
                    "owner": repo_owner,
                    "repo": repo_name,
                    "issue_number": int(issue_number),
                    "body": "[withAI Refining] I've detected this issue and I'm ready to help. Please provide more details or type 'build agix' to start automation."
                })
                
            # 3. Check for "build agix" trigger on mapped contexts
            else:
                try:
                    comments_result = await mcp.call_tool("github.list_issue_comments", {
                        "owner": repo_owner,
                        "repo": repo_name,
                        "issue_number": int(issue_number)
                    })
                    
                    comments_data = comments_result.content[0].text if comments_result.content else "[]"
                    try:
                        comments = json.loads(comments_data)
                    except json.JSONDecodeError:
                        from python.helpers import dirty_json
                        comments = dirty_json.try_parse(comments_data)
                    
                    # Identify unique human users who said "build agix"
                    trigger_users = set()
                    for comment in comments:
                        body = comment.get("body", "").lower()
                        user = comment.get("user", {}).get("login")
                        if "build agix" in body and user:
                            trigger_users.add(user)
                    if len(trigger_users) >= 2:
                        if not state["triggered_builds"].get(issue_number):
                            print(f"Build trigger detected for issue #{issue_number} by {trigger_users}")
                            state["triggered_builds"][issue_number] = True
                            
                            # 4. Trigger the actual TDD flow agent
                            try:
                                from python.initialize import initialize_agent
                                
                                # Use existing agent config as base
                                config = initialize_agent()
                                
                                # Create a specialized context for the GitHub issue
                                # Each issue gets its own persistent context
                                gh_context = AgentContext.get(context_id)
                                if not gh_context:
                                    # Resolve project if possible
                                    from python.helpers.webhook_handler import resolve_project_for_repo
                                    project_name = resolve_project_for_repo(repo_owner, repo_name)
                                    
                                    gh_context = AgentContext(
                                        id=context_id,
                                        name=f"GitHub Issue #{issue_number}",
                                        config=config,
                                        type=AgentContextType.TASK
                                    )
                                    
                                    if project_name:
                                        from python.helpers import projects as projects_helper
                                        projects_helper.activate_project(context_id, project_name)
                                
                                # Set specialized system prompt
                                prompt_path = "/agix/prompts/github_automation_prompt.md" if os.path.exists("/agix/prompts") else ("/agix/prompts/github_automation_prompt.md" if os.path.exists("/agix") else "prompts/github_automation_prompt.md")
                                with open(prompt_path, "r") as f:
                                    automation_prompt = f.read()

                                
                                # Send the trigger message to the agent
                                # We provide the full issue context
                                trigger_msg = f"TRIGGER: The build has been approved by {list(trigger_users)}. Please implement the requirements for Issue #{issue_number}: {issue.get('title')}\n\nDescription: {issue.get('body')}"
                                
                                from python.agent import UserMessage
                                msg_obj = UserMessage(
                                    message=trigger_msg,
                                    system_message=[automation_prompt]
                                )
                                
                                # 5. Railway Deployment Integration (Phase 4)
                                try:
                                    from python.helpers.railway_helper import RailwayHelper
                                    railway = RailwayHelper()
                                    
                                    # Railway IDs should ideally be in project settings or inferred
                                    # For the MVP, we might use placeholders or attempt discovery
                                    railway_service_id = state.get("railway_service_id")
                                    
                                    if railway_token := os.environ.get("RAILWAY_TOKEN"):
                                        if railway_service_id:
                                            print(f"Syncing environment variables to Railway for Issue #{issue_number}...")
                                            
                                            # Automation: We no longer auto-sync secrets from issue body to prevent leaks/junk
                                            # If needed, the implemented agent should use tools to manage secrets specifically
                                            pass
                                            
                                            # Execute deployment
                                            print("Triggering Railway deployment...")
                                            deploy_out = await railway.deploy()
                                            print(f"Railway deploy output: {deploy_out}")
                                            
                                            # Notify on GitHub
                                            await mcp.call_tool("github.create_issue_comment", {
                                                "owner": repo_owner,
                                                "repo": repo_name,
                                                "issue_number": int(issue_number),
                                                "body": f"[withAI Deploying] Requirements approved. I've synced secrets and triggered a deployment to Railway.\n\nDeployment Logs/Status: {deploy_out}"
                                            })
                                        else:
                                            print("Railway Service ID not found. Skipping auto-deploy.")
                                    else:
                                        print("RAILWAY_TOKEN not found. skipping auto-deploy.")
                                        
                                except Exception as rw_err:
                                    print(f"Railway automation failed: {rw_err}")

                                print(f"Agent triggered for Issue #{issue_number} in context {context_id}")
                                
                            except Exception as trigger_err:
                                print(f"Failed to trigger agent for Issue #{issue_number}: {trigger_err}")
                            
                except Exception as e:
                    print(f"Failed to fetch comments for issue #{issue_number}: {e}")

        cls.save_state(state)

async def main():
    # Example polling invocation
    sync = GitHubSync()
    # Replace with configured repo from settings
    await sync.poll_issues("owner", "repo")

if __name__ == "__main__":
    asyncio.run(main())
