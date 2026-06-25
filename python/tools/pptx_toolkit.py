import os
import json
import logging
import subprocess
from typing import Dict, Any, List, Optional
from python.helpers.tool import Tool, Response

logger = logging.getLogger("pptx-toolkit")

class PptxToolkit(Tool):
    """
    Advanced PPTX toolkit for inventory, rearrangement, and thumbnail generation.
    Wraps the scripts in .agent/skills/scientific/document-skills/pptx/scripts/
    """

    def __init__(self, agent=None, name="pptx_toolkit", method=None, args=None, message="", loop_data=None, **kwargs):
        super().__init__(agent, name, method, args or {}, message, loop_data, **kwargs)
        self.base_path = ".agent/skills/scientific/document-skills/pptx"
        self.scripts_path = os.path.join(self.base_path, "scripts")

    async def execute(self, action: str = None, **kwargs) -> Response:
        action = action or self.args.get("action")
        if not action:
            return Response(message="Error: Missing 'action' parameter.", break_loop=False)

        try:
            if action == "inventory":
                result = await self.run_inventory(kwargs.get("pptx_path") or self.args.get("pptx_path"))
            elif action == "rearrange":
                result = await self.run_rearrange(
                    source=kwargs.get("source") or self.args.get("source"),
                    target=kwargs.get("target") or self.args.get("target"),
                    order=kwargs.get("order") or self.args.get("order")
                )
            elif action == "thumbnail":
                result = await self.run_thumbnail(
                    pptx_path=kwargs.get("pptx_path") or self.args.get("pptx_path"),
                    output_dir=kwargs.get("output_dir") or self.args.get("output_dir")
                )
            else:
                result = {"status": "error", "message": f"Unknown action: {action}"}
            
            return Response(message=json.dumps(result, indent=2), break_loop=False)
        except Exception as e:
            return Response(message=f"Error in PptxToolkit: {str(e)}", break_loop=False)

    async def run_inventory(self, pptx_path: str) -> Dict[str, Any]:
        if not pptx_path: return {"status": "error", "message": "pptx_path is required."}
        output_json = "temp_inventory.json"
        cmd = f"python {self.scripts_path}/inventory.py {pptx_path} {output_json}"
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if proc.returncode != 0:
            return {"status": "error", "message": proc.stderr, "output": proc.stdout}
        
        if os.path.exists(output_json):
            with open(output_json, "r") as f:
                data = json.load(f)
            os.remove(output_json)
            return {"status": "success", "inventory": data}
        return {"status": "error", "message": "Inventory file not generated."}

    async def run_rearrange(self, source: str, target: str, order: str) -> Dict[str, Any]:
        if not all([source, target, order]): return {"status": "error", "message": "source, target, and order are required."}
        cmd = f"python {self.scripts_path}/rearrange.py {source} {target} {order}"
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if proc.returncode == 0:
            return {"status": "success", "message": f"Created {target} with order {order}", "output": proc.stdout}
        return {"status": "error", "message": proc.stderr}

    async def run_thumbnail(self, pptx_path: str, output_dir: str = "thumbnails") -> Dict[str, Any]:
        if not pptx_path: return {"status": "error", "message": "pptx_path is required."}
        cmd = f"python {self.scripts_path}/thumbnail.py {pptx_path} {output_dir}"
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if proc.returncode == 0:
            return {"status": "success", "message": f"Thumbnails generated in {output_dir}", "output": proc.stdout}
        return {"status": "error", "message": proc.stderr}
