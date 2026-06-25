"""
git_publish Tool — Safe Git Push via Staging Directory
=======================================================
ITR-31 Fix #5: Agents previously ran raw `git init`, `git remote add`,
`git push` inside the project directory. GitGuard blocks these operations,
causing 16+ minute failure loops.

This tool implements the `tmp/push_staging/` pattern:
1. Clone the target repo into `<project>/tmp/push_staging/`
2. rsync project files (excluding node_modules, .next, .git, .agix.proj, tmp/)
3. git add → commit → push from the staging dir (has its own .git isolation)
4. Clean up staging dir

Because staging has its own `.git` (from the clone), GitGuard allows all
destructive git operations there. No host repo contamination risk.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from python.helpers.tool import Tool, Response

logger = logging.getLogger(__name__)

# Directories and files to exclude from rsync into staging
RSYNC_EXCLUDES = [
    "node_modules",
    ".next",
    ".git",
    ".agix.proj",
    "tmp/",
    "__pycache__",
    ".env",
    ".env.local",
    "dist/",
    "build/",
    ".turbo",
    "coverage/",
    # RC-8 (RCA-ITR36): Exclude database files — these are generated
    # from seed scripts and should never be committed to remote repos
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "*.db-journal",
    # Prisma generated client (large, regenerated on install)
    ".prisma",
]

STAGING_DIR_NAME = "push_staging"


class GitPublish(Tool):
    """
    Safely publishes project code to a remote git repository using the
    tmp/push_staging/ isolation pattern. Bypasses GitGuard by cloning the
    target repo (creating its own .git) before committing and pushing.
    """

    async def execute(self, **kwargs) -> Response:
        from python.helpers.async_subprocess import run_git_command, run_command

        await self.agent.handle_intervention()

        # F-4 (RCA-461): Pre-flight credential check — fail fast
        github_token = (
            os.environ.get("GITHUB_TOKEN")
            or os.environ.get("GH_TOKEN")
            or self.agent.config.get("github_token", "")
        )
        if not github_token:
            msg = (
                "⚠️ **Git Push Skipped — No GitHub Credentials**\n\n"
                "`GITHUB_TOKEN` or `GH_TOKEN` environment variable is not set. "
                "Cannot push to remote repository.\n"
                "All local work is preserved. Set the token to enable pushing."
            )
            self.agent.log(type="warning", heading="Git Push: No Credentials", content=msg)
            return Response(message=msg, break_loop=False)

        # ── Extract arguments ──
        repo_url = self.args.get("repo_url", "").strip()
        project_dir = self.args.get("project_dir", "").strip()
        commit_message = self.args.get("commit_message", "Deploy project").strip()
        branch = self.args.get("branch", "main").strip()

        if not repo_url:
            return Response(
                message="Error: 'repo_url' is required. Provide the full git URL "
                        "(e.g. https://github.com/user/repo.git).",
                break_loop=False,
            )

        if not project_dir:
            return Response(
                message="Error: 'project_dir' is required. Provide the absolute path "
                        "to the project directory to publish.",
                break_loop=False,
            )

        if not os.path.isdir(project_dir):
            return Response(
                message=f"Error: project_dir '{project_dir}' does not exist or is not a directory.",
                break_loop=False,
            )

        # ── Set up staging directory ──
        staging_dir = os.path.join(project_dir, "tmp", STAGING_DIR_NAME)

        # Clean up any previous staging remnant
        if os.path.exists(staging_dir):
            try:
                shutil.rmtree(staging_dir)
            except Exception as e:
                logger.warning(f"[git_publish] Failed to clean old staging dir: {e}")

        os.makedirs(staging_dir, exist_ok=True)

        try:
            # ── Step 1: Clone target repo into staging ──
            self.set_progress("Cloning target repository...")
            clone_result = await run_git_command(
                ["clone", repo_url, "."],
                cwd=staging_dir,
                timeout=120,
            )

            if clone_result.returncode != 0:
                # If clone fails (empty repo), try git init + remote add
                init_result = await run_git_command(
                    ["init"],
                    cwd=staging_dir,
                    timeout=30,
                )
                if init_result.returncode != 0:
                    return Response(
                        message=f"Error: Failed to init staging repo: {init_result.stderr}",
                        break_loop=False,
                    )

                remote_result = await run_git_command(
                    ["remote", "add", "origin", repo_url],
                    cwd=staging_dir,
                    timeout=30,
                )
                if remote_result.returncode != 0:
                    return Response(
                        message=f"Error: Failed to add remote: {remote_result.stderr}",
                        break_loop=False,
                    )

            # ── Step 2: Checkout target branch ──
            self.set_progress(f"Checking out branch '{branch}'...")
            checkout_result = await run_git_command(
                ["checkout", "-B", branch],
                cwd=staging_dir,
                timeout=30,
            )
            # Non-fatal if checkout fails (new repo, already on branch)

            # ── Step 3: rsync project files into staging ──
            self.set_progress("Syncing project files to staging...")

            # Build rsync exclude arguments
            exclude_args = []
            for exc in RSYNC_EXCLUDES:
                exclude_args.extend(["--exclude", exc])

            # Ensure source path ends with / for rsync content-copy semantics
            source_path = project_dir.rstrip("/") + "/"

            rsync_cmd = [
                "rsync", "-a", "--delete",
                *exclude_args,
                source_path,
                staging_dir + "/",
            ]

            rsync_result = await run_command(
                cmd=rsync_cmd,
                cwd=project_dir,
                raise_on_error=False,
            )

            if rsync_result.returncode != 0:
                return Response(
                    message=f"Error: rsync failed: {rsync_result.stderr}",
                    break_loop=False,
                )

            # ── Step 4: Stage all changes ──
            self.set_progress("Staging changes...")
            add_result = await run_git_command(
                ["add", "-A"],
                cwd=staging_dir,
                timeout=60,
            )
            if add_result.returncode != 0:
                return Response(
                    message=f"Error: git add failed: {add_result.stderr}",
                    break_loop=False,
                )

            # ── Step 5: Check if there are changes to commit ──
            status_result = await run_git_command(
                ["status", "--porcelain"],
                cwd=staging_dir,
                timeout=30,
            )

            if not status_result.output.strip():
                # No changes — clean up and report
                self._cleanup_staging(staging_dir)
                return Response(
                    message="No changes detected between the project and the remote repository. "
                            "Nothing to push.",
                    break_loop=False,
                )

            # ── Step 6: Commit ──
            self.set_progress("Committing changes...")

            # Configure git user for the staging repo
            await run_git_command(
                ["config", "user.email", "agent@agix.com"],
                cwd=staging_dir,
                timeout=10,
            )
            await run_git_command(
                ["config", "user.name", "AGIX Agent"],
                cwd=staging_dir,
                timeout=10,
            )

            commit_result = await run_git_command(
                ["commit", "-m", commit_message],
                cwd=staging_dir,
                timeout=60,
            )
            if commit_result.returncode != 0:
                return Response(
                    message=f"Error: git commit failed: {commit_result.stderr}",
                    break_loop=False,
                )

            # ── Step 7: Push ──
            self.set_progress(f"Pushing to {branch}...")

            # Stamp heartbeat before potentially long network push
            try:
                from python.helpers.subordinate_timeout import stamp_tool_activity_heartbeat
                stamp_tool_activity_heartbeat(self.agent)
            except Exception:
                pass

            push_result = await run_git_command(
                ["push", "-u", "origin", branch],
                cwd=staging_dir,
                timeout=180,
            )
            if push_result.returncode != 0:
                # Try force push if normal push fails (common for first push)
                push_result = await run_git_command(
                    ["push", "--force", "-u", "origin", branch],
                    cwd=staging_dir,
                    timeout=180,
                )
                if push_result.returncode != 0:
                    return Response(
                        message=f"Error: git push failed: {push_result.stderr}\n\n"
                                f"Staging directory preserved at: {staging_dir}\n"
                                f"You can inspect it manually or retry.",
                        break_loop=False,
                    )

            # ── Step 8: Clean up staging ──
            self._cleanup_staging(staging_dir)

            # ── Success ──
            changed_files = status_result.output.strip().count("\n") + 1
            return Response(
                message=f"✅ Successfully published to {repo_url} (branch: {branch})\n\n"
                        f"- Files changed: {changed_files}\n"
                        f"- Commit message: {commit_message}\n"
                        f"- Push output: {push_result.output.strip()[:500]}",
                break_loop=False,
            )

        except Exception as e:
            # Ensure cleanup even on unexpected errors
            self._cleanup_staging(staging_dir)
            logger.error(f"[git_publish] Unexpected error: {e}", exc_info=True)
            return Response(
                message=f"Error: git_publish failed unexpectedly: {str(e)}",
                break_loop=False,
            )

    @staticmethod
    def _cleanup_staging(staging_dir: str) -> None:
        """Remove the staging directory. Best-effort — never raises."""
        try:
            if os.path.exists(staging_dir):
                shutil.rmtree(staging_dir)
                logger.info(f"[git_publish] Cleaned up staging dir: {staging_dir}")
        except Exception as e:
            logger.warning(f"[git_publish] Failed to clean up staging dir: {e}")
