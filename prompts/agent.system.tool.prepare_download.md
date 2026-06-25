## prepare_download

Use this tool to prepare a file for download. This stages the file in a download area and provides a download link for the user.

**When to use:**
- When the user asks to download a file from the project
- When you've generated output files that the user needs
- When sharing build artifacts, exports, or results

**Arguments:**
- `file_path` (required): Path to the file within the project directory
- `display_name` (optional): Friendly name for the download (defaults to filename)

**Example:**
~~~json
{
    "file_path": "output/report.pdf",
    "display_name": "Monthly Report.pdf"
}
~~~

**Important:**
- Only files within the current project directory can be downloaded
- System files and files outside the project are not accessible
- For directories, the tool will create a ZIP archive automatically
