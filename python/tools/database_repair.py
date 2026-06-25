from python.helpers.tool import Tool, ToolResult
from python.scripts.repair_database import repair_database
import os

class DatabaseRepair(Tool):
    """
    Detects and repairs (resets) a malformed AGIX database.
    This is a 'Nuclear Option' that backups the current DB and starts fresh.
    """
    
    def get_name(self) -> str:
        return "database_repair"
        
    def get_description(self) -> str:
        return "Detects and repairs (resets) a malformed AGIX database. Use this if you encounter 'malformed disk image' errors."
        
    def get_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.get_name(),
                "description": self.get_description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "confirm": {
                            "type": "boolean",
                            "description": "Must be set to true to acknowledge that local chat history will be archived (reset)."
                        },
                        "db_path": {
                            "type": "string",
                            "description": "Optional custom database path. Defaults to the primary agix.db."
                        }
                    },
                    "required": ["confirm"]
                }
            }
        }
        
    async def execute(self, arguments: dict, context) -> ToolResult:
        confirm = arguments.get("confirm", False)
        if not confirm:
            return ToolResult(error="You must set 'confirm' to true to perform a database reset.")
            
        db_path = arguments.get("db_path")
        if not db_path:
            # Default to primary DB
            from python.helpers import files
            db_path = files.get_abs_path("tmp", "agix.db")
            
        success, message = repair_database(db_path)
        
        if success:
            return ToolResult(stdout=f"Database repair successful: {message}\nNote: A restart might be required for the change to take full effect in all components.")
        else:
            return ToolResult(error=f"Database repair failed: {message}")
