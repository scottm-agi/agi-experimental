import os
import re
from typing import Dict, List, Any
from python.helpers.tool import Tool

class TelemetryCheck(Tool):
    """
    Issue #400: Implement a tool to check for telemetry patterns in code.
    This helps ensure that 0 telemetry leaves the system.
    """

    def __init__(self, agent=None, name="telemetry_check", method=None, args=None, message="", loop_data=None, **kwargs):
        # Handle the case where agent is None for standalone testing
        super().__init__(agent, name, method, args or {}, message, loop_data, **kwargs)
        self.telemetry_patterns = {
            "Segment": [r"segment\.com", r"analytics\.js", r"Segment\.analytics"],
            "Google Analytics": [r"google-analytics\.com", r"ua-\d+-\d+", r"gtag\(", r"ga\("],
            "Sentry": [r"sentry\.io", r"Sentry\.init", r"raven-js"],
            "Mixpanel": [r"mixpanel\.com", r"mixpanel\.track"],
            "Amplitude": [r"amplitude\.com", r"amplitude\.getInstance"],
            "FullStory": [r"fullstory\.com", r"FS\.identify"],
            "PostHog": [r"posthog\.com", r"posthog\.init"],
            "Hotjar": [r"hotjar\.com", r"hj\("],
            "LogRocket": [r"logrocket\.com", r"LogRocket\.init"],
            "Datadog Rum": [r"datadoghq\.com", r"datadogRum"],
            "Intercom": [r"intercom\.io", r"Intercom\("]
        }

    async def execute(self, path: str = ".", recursive: bool = True) -> Dict[str, Any]:
        """
        Scan files at the specified path for telemetry patterns.
        """
        results = []
        path = os.path.abspath(path)
        
        if not os.path.exists(path):
            return {"error": f"Path not found: {path}"}

        files_to_scan = []
        if os.path.isfile(path):
            files_to_scan.append(path)
        else:
            for root, dirs, files in os.walk(path):
                if not recursive and root != path:
                    continue
                
                # Skip common ignore dirs
                if any(ignored in root for ignored in [".git", "node_modules", "venv", ".venv", "__pycache__", "data"]):
                    continue
                    
                for file in files:
                    if file.endswith((".py", ".js", ".ts", ".html", ".css", ".md", ".json")):
                        files_to_scan.append(os.path.join(root, file))

        for file_path in files_to_scan:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    
                file_findings = []
                for service, patterns in self.telemetry_patterns.items():
                    for pattern in patterns:
                        matches = re.finditer(pattern, content, re.IGNORECASE)
                        for match in matches:
                            # Get line number
                            line_no = content.count("\n", 0, match.start()) + 1
                            file_findings.append({
                                "service": service,
                                "pattern": pattern,
                                "line": line_no,
                                "match": match.group(0)
                            })
                
                if file_findings:
                    rel_path = os.path.relpath(file_path, path)
                    results.append({
                        "file": rel_path,
                        "findings": file_findings
                    })
            except Exception as e:
                # Skip files that can't be read
                continue

        if not results:
            return Response(
                message="No telemetry patterns detected.",
                break_loop=False,
                additional={"status": "success", "findings": []}
            )
            
        return Response(
            message=f"Detected {sum(len(r['findings']) for r in results)} telemetry patterns in {len(results)} files.",
            break_loop=False,
            additional={
                "status": "warning",
                "findings": results
            }
        )
