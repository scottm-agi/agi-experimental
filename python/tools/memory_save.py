from python.helpers.memory import Memory
from python.helpers.tool import Tool, Response
from python.agent import AgentContextType
from python.helpers import settings


class MemorySave(Tool):
    """
    Saves a piece of information (text) into the project's vector database.
    This allows the agent (or other agents) to recall this information later using memory_load.
    """

    async def execute(self, text="", area="", **kwargs):
        """
        Saves text to memory.
        
        Args:
            text (str): The information to save.
            area (str): Optional memory area (main, fragments, solutions, instruments). Defaults to 'main'.
            **kwargs: Additional metadata to store with the memory.
        """
        if not area:
            area = Memory.Area.MAIN.value

        # Issue #392: Prevent memory pollution from TASK contexts
        if getattr(self.agent.context, 'type', None) == AgentContextType.TASK:
            if settings.get_settings().get('task_vector_memory_disabled', True):
                return Response(message="Memory saving skipped for TASK context to prevent pollution.", break_loop=False)

        metadata = {"area": area, **kwargs}

        db = await Memory.get(self.agent)
        id = await db.insert_text(text, metadata)

        # result = self.agent.read_prompt("fw.memory_saved.md", memory_id=id)
        # Using a direct message instead of a prompt to avoid potential missing prompt errors for now
        result = f"Memory saved successfully with ID: {id}"
        return Response(message=result, break_loop=False)
