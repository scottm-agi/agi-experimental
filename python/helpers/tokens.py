from __future__ import annotations
from typing import Literal
import tiktoken

APPROX_BUFFER = 1.1
TRIM_BUFFER = 0.8


def count_tokens(text: str, encoding_name="cl100k_base") -> int:
    if not text:
        return 0

    # Get the encoding
    encoding = tiktoken.get_encoding(encoding_name)

    # Encode the text and count the tokens
    tokens = encoding.encode(text, disallowed_special=())
    token_count = len(tokens)

    return token_count


def approximate_tokens(
    text: str,
) -> int:
    return int(count_tokens(text) * APPROX_BUFFER)


def trim_to_tokens(
    text: str,
    max_tokens: int,
    direction: Literal["start", "end"],
    ellipsis: str = "...",
    encoding_name="cl100k_base"
) -> str:
    if not text:
        return ""
    
    encoding = tiktoken.get_encoding(encoding_name)
    tokens = encoding.encode(text, disallowed_special=())
    
    if len(tokens) <= max_tokens:
        return text
        
    if direction == "start":
        # Keep the end of the text
        trimmed_tokens = tokens[-max_tokens:]
        return ellipsis + encoding.decode(trimmed_tokens)
    else:
        # Keep the start of the text
        trimmed_tokens = tokens[:max_tokens]
        return encoding.decode(trimmed_tokens) + ellipsis
