from __future__ import annotations
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Union, Optional

from python.agent import Agent, LoopData
from python.helpers.print_style import PrintStyle
from python.helpers.strings import sanitize_string


@dataclass
class Response:
    message:str
    break_loop: bool
    additional: Optional[dict[str, Any]] = None
    summary: Optional[str] = None

class Tool:

    def __init__(self, agent: Agent, name: str, method: Optional[str], args: dict[str,str], message: str, loop_data: Optional[LoopData], **kwargs) -> None:
        self.agent = agent
        self.name = name
        self.method = method
        self.args = args
        self.loop_data = loop_data
        self.message = message
        self.progress: str = ""

    @abstractmethod
    async def execute(self,**kwargs) -> Response:
        pass

    def set_progress(self, content: Optional[str]):
        self.progress = content or ""

    def add_progress(self, content: Optional[str]):
        if not content:
            return
        self.progress += content

    async def before_execution(self, **kwargs):
        PrintStyle(font_color="#1B4F72", padding=True, background_color="white", bold=True).print(f"{self.agent.agent_name}: Using tool '{self.name}'")
        self.log = self.get_log_object()
        if self.args and isinstance(self.args, dict):
            for key, value in self.args.items():
                PrintStyle(font_color="#85C1E9", bold=True).stream(self.nice_key(key)+": ")
                PrintStyle(font_color="#85C1E9", padding=isinstance(value,str) and "\n" in value).stream(value)
                PrintStyle().print()

    async def after_execution(self, response: Response, **kwargs):
        text = sanitize_string(response.message.strip())
        await self.agent.hist_add_tool_result(self.name, text, **(response.additional or {}))
        PrintStyle(font_color="#1B4F72", background_color="white", padding=True, bold=True).print(f"{self.agent.agent_name}: Response from tool '{self.name}'")
        PrintStyle(font_color="#85C1E9").print(text)
        # Merge additional data into log kvps for frontend rendering (e.g., A2UI tiles)
        # We use kvps= instead of **kwargs to avoid naming conflicts with log.update() params like 'type'
        if response.additional:
            merged_kvps = dict(self.log.kvps) if self.log.kvps else {}
            merged_kvps.update(response.additional)
            self.log.update(content=text, summary=response.summary, kvps=merged_kvps)
        else:
            self.log.update(content=text, summary=response.summary)

    def get_log_object(self):
        if self.method:
            heading = f"icon://construction {self.agent.agent_name}: Using tool '{self.name}:{self.method}'"
        else:
            heading = f"icon://construction {self.agent.agent_name}: Using tool '{self.name}'"
        return self.agent.context.log.log(type="tool", heading=heading, content="", kvps=self.args)

    def nice_key(self, key:str):
        words = key.split('_')
        words = [words[0].capitalize()] + [word.lower() for word in words[1:]]
        result = ' '.join(words)
        return result
