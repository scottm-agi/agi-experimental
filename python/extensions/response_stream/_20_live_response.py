from __future__ import annotations
import logging
from python.helpers import persist_chat, tokens
from python.helpers.extension import Extension
from python.agent import LoopData
import asyncio
from python.helpers.log import LogItem
from python.helpers import log

logger = logging.getLogger("agix.live_response")


class LiveResponse(Extension):

    async def execute(
        self,
        loop_data: LoopData = LoopData(),
        text: str = "",
        parsed: dict = {},
        **kwargs,
    ):
        try:
            # Issue #973: Extract tool_args and guard against None/non-dict types
            # to avoid TypeError: argument of type 'NoneType' is not iterable
            tool_args = parsed.get("tool_args")
            if (
                "tool_name" not in parsed
                or parsed["tool_name"] != "response"
                or not isinstance(tool_args, dict)
                or "text" not in tool_args
                or not tool_args["text"]
            ):
                return  # not a response

            # Guard: context or log may not be initialized yet for subordinates
            if not self.agent.context or not self.agent.context.log:
                return

            # create log message and store it in loop data temporary params
            if "log_item_response" not in loop_data.params_temporary:
                loop_data.params_temporary["log_item_response"] = (
                    self.agent.context.log.log(
                        type="response",
                        heading=f"icon://chat {self.agent.agent_name}: Responding",
                    )
                )

            # update log message
            log_item = loop_data.params_temporary["log_item_response"]
            log_item.update(content=tool_args["text"])
        except Exception as e:
            # Forgejo #894: Never silently swallow response content errors.
            # This was causing "Agent finished but no response captured" in
            # 5 out of 164 HumanEval+ benchmark problems.
            logger.error(
                f"[LIVE RESPONSE] Failed to create/update response log: {e}",
                exc_info=True,
            )

