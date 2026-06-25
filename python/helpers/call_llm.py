from __future__ import annotations
from typing import Callable, TypedDict, Union, Any, Optional
from langchain_core.prompts import (
    ChatPromptTemplate,
    FewShotChatMessagePromptTemplate,
)

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.llms import BaseLLM


class Example(TypedDict):
    input: str
    output: str


def _is_custom_wrapper(model: Any) -> bool:
    """Check if model is our custom LiteLLMChatWrapper (not a LangChain Runnable)."""
    model_class = type(model).__name__
    return model_class in ("LiteLLMChatWrapper", "BrowserCompatibleChatWrapper", "FallbackChatWrapper")


async def call_llm(
    system: str,
    model: Union[BaseChatModel, BaseLLM, Any],
    message: str,
    examples: list[Example] = [],
    callback: Optional[Callable[[str], None]] = None
):
    # Handle our custom LiteLLMChatWrapper which is NOT a LangChain Runnable
    if _is_custom_wrapper(model):
        # Build the prompt with examples manually
        example_lines = ""
        for ex in examples:
            example_lines += f"\nUser: {ex['input']}\nAssistant: {ex['output']}\n"
        
        full_system = system + example_lines if example_lines else system
        
        # Build a proper callback wrapper that returns something awaitable (None won't work with await)
        # Only pass callback if one was provided - unified_call checks "if response_callback:" and then awaits result
        # The callback needs to return an awaitable, so we create an async wrapper
        async def _wrap_callback(chunk: str, full: str):
            if callback:
                callback(chunk)
        
        # Use unified_call for custom wrappers
        response_text, reasoning, _, _ = await model.unified_call(
            system_message=full_system,
            user_message=message,
            response_callback=_wrap_callback if callback else None,
        )
        return response_text

    # Standard LangChain path for native BaseChatModel/BaseLLM
    example_prompt = ChatPromptTemplate.from_messages(
        [
            HumanMessage(content="{input}"),
            AIMessage(content="{output}"),
        ]
    )

    few_shot_prompt = FewShotChatMessagePromptTemplate(
        example_prompt=example_prompt,
        examples=examples,  # type: ignore
        input_variables=[],
    )

    few_shot_prompt.format()


    final_prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=system),
            few_shot_prompt,
            HumanMessage(content=message),
        ]
    )

    chain = final_prompt | model

    response = ""
    async for chunk in chain.astream({}):
        # await self.handle_intervention()  # wait for intervention and handle it, if paused

        if isinstance(chunk, str):
            content = chunk
        elif hasattr(chunk, "content"):
            content = str(chunk.content)
        else:
            content = str(chunk)

        if callback:
            callback(content)

        response += content

    return response

