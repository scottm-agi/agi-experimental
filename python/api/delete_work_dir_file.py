from __future__ import annotations
from python.helpers.api import ApiHandler, Input, Output, Request, Response


from python.helpers.file_browser import FileBrowser
from python.helpers import files, runtime
from python.api import get_work_dir_files
import os


class DeleteWorkDirFile(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        if os.getenv("AGIX_DISABLE_FILE_ACCESS", "false").lower() == "true":
            return Response("File access is disabled in this environment", status=403)
        file_path = input.get("path", "")
        if not file_path.startswith("/"):
            file_path = f"/{file_path}"

        current_path = input.get("currentPath", "")

        # browser = FileBrowser()
        res = await runtime.call_development_function(delete_file, file_path)

        if res:
            # Get updated file list
            # result = browser.get_files(current_path)
            result = await runtime.call_development_function(get_work_dir_files.get_files, current_path)
            return {"data": result}
        else:
            raise Exception(f"File not found or could not be deleted: {file_path}")


async def delete_file(file_path: str):
    browser = FileBrowser()
    return browser.delete_file(file_path)
