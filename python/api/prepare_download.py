from __future__ import annotations
"""
Prepare Download API — Endpoint for staging files for user download.

Allows the download API to serve files that have been staged by agents
via the prepare_download tool. This endpoint itself is used for direct
staging requests from external API consumers.
"""
from python.helpers.api import ApiHandler, Request, Response
from python.helpers import files, projects, feature_flags
from python.helpers.print_style import PrintStyle
import os
import shutil
import uuid
import logging

logger = logging.getLogger("api.prepare_download")


class PrepareDownload(ApiHandler):
    """API endpoint to prepare a file for download."""

    async def process(self, input: dict, request: Request) -> dict | Response:
        context_id = input.get("context_id", "")
        file_path = input.get("file_path", "").strip()
        project = input.get("project", "").strip()

        if not file_path:
            return Response('{"error": "file_path is required"}', status=400, mimetype="application/json")

        if not context_id:
            return Response('{"error": "context_id is required"}', status=400, mimetype="application/json")

        if not project:
            return Response('{"error": "project is required"}', status=400, mimetype="application/json")

        # Resolve path within project
        project_folder = projects.get_project_folder(project)
        project_folder = files.normalize_agix_path(project_folder)
        abs_path = os.path.normpath(os.path.join(project_folder, file_path))

        # Security: must be within project
        if not abs_path.startswith(os.path.normpath(project_folder)):
            return Response('{"error": "Path escapes project directory"}', status=403, mimetype="application/json")

        check_path = files.fix_dev_path(abs_path)
        if not os.path.exists(check_path):
            return Response('{"error": "File not found"}', status=404, mimetype="application/json")

        # Stage the file
        staging_dir = files.get_abs_path(f"tmp/downloads/{context_id}")
        os.makedirs(staging_dir, exist_ok=True)

        display_name = os.path.basename(file_path)
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
            logger.error(f"Failed to stage file: {e}")
            return Response(f'{{"error": "Failed to stage file: {str(e)}"}}', status=500, mimetype="application/json")

        download_url = f"/download_work_dir_file?path=tmp/downloads/{context_id}/{staged_name}"

        return {
            "download_url": download_url,
            "display_name": display_name,
            "context_id": context_id,
        }


instance = PrepareDownload
