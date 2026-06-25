from __future__ import annotations
from python.helpers.extension import Extension
from python.agent import LoopData, AgentContextType
from python.helpers.persistence_manager import PersistenceManager
from python.helpers.persist_chat import _serialize_context
import logging
import asyncio
import threading

logger = logging.getLogger("agix.save_chat_sql")


class SaveChatSQL(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        # Skip saving BACKGROUND contexts as they should be ephemeral
        if self.agent.context.type == AgentContextType.BACKGROUND:
            return

        try:
            # Serialize the context using the existing helper from persist_chat
            context_data = _serialize_context(self.agent.context)
            context_id = self.agent.context.id
            
            # Save directly using the current async context
            pm = PersistenceManager.get_instance()
            await pm.save_context_sql(context_data)
            logger.info(f"Saved context {context_id} to SQL database")
            
        except Exception as e:
            logger.error(f"Failed to save context {self.agent.context.id} to SQL: {e}")

