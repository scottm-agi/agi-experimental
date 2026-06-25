from __future__ import annotations
from python.helpers.extension import Extension
from python.agent import LoopData
from python.helpers import memory
import asyncio


class MemoryInit(Extension):

    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        db = await memory.Memory.get(self.agent)
        

   