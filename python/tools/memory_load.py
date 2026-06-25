from __future__ import annotations
from python.helpers.memory import Memory
from python.helpers.tool import Tool, Response

DEFAULT_THRESHOLD = 0.7
DEFAULT_LIMIT = 10


class MemoryLoad(Tool):
    """
    Searches and loads relevant memories or historical information from the project's vector database.
    Use this to recall past decisions, research findings, or previous interactions.
    """

    async def execute(self, query="", threshold=DEFAULT_THRESHOLD, limit=DEFAULT_LIMIT, filter="", **kwargs):
        try:
            db = await Memory.get(self.agent)
            docs = await db.search_similarity_threshold(query=query, limit=limit, threshold=threshold, filter=filter)

            if len(docs) == 0:
                result = self.agent.read_prompt("fw.memories_not_found.md", query=query)
            else:
                text = "\n\n".join(Memory.format_docs_plain(docs))
                result = str(text)

        except Exception as e:
            result = f"Memory system unavailable or failed to search: {e}"
            # Log the error but don't crash the tool entirely if possible
            # (Memory.get already logs to agent context)
            
        return Response(message=result, break_loop=False)
