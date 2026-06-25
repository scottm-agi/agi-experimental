"""
Prepare Download Tool — Agent tool to stage files for user download.

In production, agents cannot directly expose the filesystem to users.
Instead, they use this tool to stage files in the chat's download area,
then present a download link to the user.

Works in both development and production environments.
"""
from python.helpers.tool import Tool, Response
from python.helpers import files, projects
import os
import shutil
import uuid
import logging

logger = logging.getLogger("tools.prepare_download")


class PrepareDownload(Tool):

    async def execute(self, **kwargs) -> Response:
        file_path = self.args.get("file_path", "").strip()
        display_name = self.args.get("display_name", "").strip()

        if not file_path:
            return Response(
                message="Error: 'file_path' argument is required. Provide the path to the file to make available for download.",
                break_loop=False
            )

        # Resolve the file path relative to the project directory
        project_name = projects.get_context_project_name(self.agent.context)
        if not project_name:
            return Response(
                message="Error: No project is active. Please activate a project first.",
                break_loop=False
            )

        project_folder = projects.get_project_folder(project_name)
        project_folder = files.normalize_agix_path(project_folder)

        # Security: ensure the file is within the project directory
        abs_path = os.path.normpath(os.path.join(project_folder, file_path))
        if not abs_path.startswith(os.path.normpath(project_folder)):
            return Response(
                message="Error: File path must be within the current project directory. Access to files outside the project is not allowed.",
                break_loop=False
            )

        # Check file exists
        check_path = files.fix_dev_path(abs_path)
        if not os.path.exists(check_path):
            return Response(
                message=f"Error: File not found: {file_path}. Please check the path and try again.",
                break_loop=False
            )

        # Use the display name or filename
        if not display_name:
            display_name = os.path.basename(file_path)

        # Create staging directory for this context
        context_id = self.agent.context.id
        staging_dir = files.get_abs_path(f"tmp/downloads/{context_id}")
        os.makedirs(staging_dir, exist_ok=True)

        # Copy file to staging with a unique name to prevent collisions
        unique_id = str(uuid.uuid4())[:8]
        staged_name = f"{unique_id}_{display_name}"
        staged_path = os.path.join(staging_dir, staged_name)

        try:
            if os.path.isdir(check_path):
                shutil.make_archive(staged_path, 'zip', check_path)
                staged_name = f"{staged_name}.zip"
                display_name = f"{display_name}.zip"
            else:
                shutil.copy2(check_path, staged_path)
        except Exception as e:
            logger.error(f"Failed to stage file for download: {e}")
            return Response(
                message=f"Error: Failed to prepare file for download: {str(e)}",
                break_loop=False
            )

        # Generate the download URL
        download_url = f"/download_work_dir_file?path=tmp/downloads/{context_id}/{staged_name}"

        logger.info(f"[DOWNLOAD] Staged file for download: {display_name} -> {staged_path}")

        return Response(
            message=f"File staged for download successfully.\n\n"
                    f"**File**: {display_name}\n"
                    f"**Download link**: {download_url}\n\n"
                    f"Present this download link to the user so they can download the file.",
            break_loop=False
        )
