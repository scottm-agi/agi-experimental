from __future__ import annotations
from abc import abstractmethod
import asyncio
from collections import OrderedDict
from collections.abc import Mapping
import json
import logging
from dataclasses import dataclass, field
import os
import uuid
import math
from python.helpers.hashing import content_hash, dedup_hash
from typing import Coroutine, Literal, TypedDict, cast, Union, Dict, List, Any
from python.helpers import messages, tokens, settings, call_llm, files
from python.helpers.llm_batcher import get_llm_batcher
from enum import Enum
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage

BULK_MERGE_COUNT = 3
TOPICS_KEEP_COUNT = 3
CURRENT_TOPIC_RATIO = 0.5
HISTORY_TOPIC_RATIO = 0.3
HISTORY_BULK_RATIO = 0.2
TOPIC_COMPRESS_RATIO = 0.40
MAX_COMPRESSION_PASSES = 5
LARGE_MESSAGE_TO_TOPIC_RATIO = 0.25
RAW_MESSAGE_OUTPUT_TEXT_TRIM = 100


class RawMessage(TypedDict):
    raw_content: "MessageContent"
    preview: str | None


MessageContent = Union[
    List["MessageContent"],
    Dict[str, "MessageContent"],
    List[Dict[str, "MessageContent"]],
    str,
    List[str],
    RawMessage,
]


class OutputMessage(TypedDict, total=False):
    id: str
    ai: bool
    content: MessageContent
    kvps: dict
    sender_type: str
    sender_id: str


class Record:
    def __init__(self):
        pass

    @abstractmethod
    def get_tokens(self) -> int:
        pass

    @abstractmethod
    async def compress(self) -> bool:
        pass

    @abstractmethod
    def output(self) -> list[OutputMessage]:
        pass

    @abstractmethod
    async def summarize(self) -> str:
        pass

    @abstractmethod
    def to_dict(self) -> dict:
        pass

    @staticmethod
    def from_dict(data: dict, history: "History"):
        cls = data["_cls"]
        return globals()[cls].from_dict(data, history=history)

    def output_langchain(self):
        return output_langchain(self.output())

    def output_text(self, human_label="user", ai_label="ai"):
        return output_text(self.output(), ai_label, human_label)

    def output_markdown(self, human_label="user", ai_label="ai"):
        return output_markdown(self.output(), ai_label, human_label)


class Message(Record):
    def __init__(self, ai: bool, content: MessageContent, tokens: int = 0, model: str = "", provider: str = "", id: str = "", protected: bool = False, sender_type: str = "", sender_id: str = "", sequence_id: int = 0, hash: str = "", **kwargs):
        self.ai = ai
        self.content = content
        self.summary: str = ""
        self.model = model
        self.provider = provider
        self.protected = protected
        self.sender_type = sender_type
        self.sender_id = sender_id
        self.id: str = id or str(uuid.uuid4())
        self.sequence_id = sequence_id
        # Calculate hash if not provided to ensure consistency and satisfy tests
        self.hash = hash or self.calculate_hash()
        self.tokens: int = tokens or self.calculate_tokens()
        self.kvps = kwargs

    def calculate_hash(self) -> str:
        """
        Calculates a unique hash for the message based on its content and metadata.
        """
        data = {
            "ai": self.ai,
            "content": str(self.content),
            "sender_type": self.sender_type,
            "sender_id": self.sender_id,
            "id": self.id
        }
        return dedup_hash(data)

    @property
    def type(self) -> str:
        return "ai" if self.ai else "human"

    def get_tokens(self) -> int:
        if not self.tokens:
            self.tokens = self.calculate_tokens()
        return self.tokens

    def calculate_tokens(self):
        text = self.output_text()
        return tokens.approximate_tokens(text)

    def set_summary(self, summary: str):
        self.summary = summary
        self.tokens = self.calculate_tokens()

    async def compress(self):
        return False

    def truncate(self, target_tokens: int) -> bool:
        """
        Truncates the message content to fit within target_tokens (Issue #416 Fix).
        Returns True if truncation occurred.
        """
        import logging
        logger = logging.getLogger("agix.history")
        
        # Strategy: Keep start and end, remove middle
        # target_chars ~ target_tokens * 4 (approximate bytes per token)
        target_chars = max(200, target_tokens * 4)
        
        def truncate_str(s: str, t_chars: int) -> str:
            if not isinstance(s, str) or len(s) <= t_chars: return s
            # Keep roughly equal parts of beginning and end
            half = max(100, (t_chars // 2) - 100) 
            # Marker must match unit test assertion
            msg = f"\n\n[... TRUNCATED {len(s) - 2*half} CHARS DUE TO CONTEXT LIMITS ...]\n\n"
            return s[:half] + msg + s[-half:]

        occurred = False
        if isinstance(self.content, str):
            if len(self.content) > target_chars:
                original_len = len(self.content)
                self.content = truncate_str(self.content, target_chars)
                logger.info(f"Truncated message {self.id[:8]} from {original_len:,} to {len(self.content):,} chars (~{target_tokens:,} tokens)")
                occurred = True
        elif isinstance(self.content, list):
            # Truncate text parts
            for part in self.content:
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                    if len(part["text"]) > (target_chars // len(self.content)):
                        part["text"] = truncate_str(part["text"], target_chars // len(self.content))
                        occurred = True
        elif isinstance(self.content, dict) and "text" in self.content:
            if isinstance(self.content["text"], str) and len(self.content["text"]) > target_chars:
                self.content["text"] = truncate_str(self.content["text"], target_chars)
                occurred = True
        
        if occurred:
            # Reset tokens and summary so they are re-calculated
            self.tokens = 0
            self.summary = "" 
            # Force update of cached tokens
            self.get_tokens()
            
        return occurred


    def output(self) -> list[OutputMessage]:
        kvps = self.kvps.copy() if hasattr(self, "kvps") else {}
        if self.model:
            kvps["actual_model"] = self.model
        if self.provider:
            kvps["actual_provider"] = self.provider
        
        # Add sequencing info to kvps for the UI
        kvps["sequence_id"] = getattr(self, "sequence_id", 0)
        kvps["hash"] = getattr(self, "hash", None) or self.calculate_hash()

        return [
            OutputMessage(
                id=self.id,
                ai=self.ai, 
                content=self.summary or self.content, 
                kvps=kvps,
                sender_type=self.sender_type,
                sender_id=self.sender_id
            )
        ]

    def output_langchain(self):
        return output_langchain(self.output())

    def output_text(self, human_label="user", ai_label="ai"):
        return output_text(self.output(), ai_label, human_label)

    def to_dict(self):
        res = {
            "_cls": "Message",
            "ai": self.ai,
            "content": self.content,
            "summary": self.summary,
            "tokens": self.tokens,
            "model": self.model,
            "provider": self.provider,
            "id": self.id,
            "protected": self.protected,
            "sender_type": self.sender_type,
            "sender_id": self.sender_id,
            "sequence_id": self.sequence_id,
            "hash": self.hash,
        }
        if hasattr(self, "kvps") and self.kvps:
            res["kvps"] = self.kvps
        return res

    @staticmethod
    def from_dict(data: dict, history: "History"):
        content = data.get("content", "Content lost")
        kvps = data.get("kvps", {})
        msg = Message(ai=data["ai"], content=content, **kvps)
        msg.summary = data.get("summary", "")
        msg.tokens = data.get("tokens", 0)
        msg.model = data.get("model", "")
        msg.provider = data.get("provider", "")
        msg.id = data.get("id", str(uuid.uuid4()))
        msg.protected = data.get("protected", False)
        msg.sender_type = data.get("sender_type", "")
        msg.sender_id = data.get("sender_id", "")
        msg.sequence_id = data.get("sequence_id", kvps.get("sequence_id", 0))
        msg.hash = data.get("hash", kvps.get("hash", ""))
        return msg


class Topic(Record):
    def __init__(self, history: "History"):
        self.history = history
        self.summary: str = ""
        self.messages: list[Message] = []

    def get_tokens(self):
        if self.summary:
            base_tokens = tokens.approximate_tokens(self.summary)
            protected_tokens = sum(m.get_tokens() for m in self.messages if getattr(m, 'protected', False))
            return base_tokens + protected_tokens
        else:
            return sum(msg.get_tokens() for msg in self.messages)

    def add_message(
        self, ai: bool, content: MessageContent, tokens: int = 0, model: str = "", provider: str = "", id: str = "", protected: bool = False, sender_type: str = "", sender_id: str = "", sequence_id: int = 0, hash: str = "", **kwargs
    ) -> Message:
        msg = Message(ai=ai, content=content, tokens=tokens, model=model, provider=provider, id=id, protected=protected, sender_type=sender_type, sender_id=sender_id, sequence_id=sequence_id, hash=hash, **kwargs)
        self.messages.append(msg)
        return msg

    def remove_message(self, id: str) -> bool:
        for i, msg in enumerate(self.messages):
            if msg.id == id:
                self.messages.pop(i)
                return True
        return False

    def output(self) -> list[OutputMessage]:
        if self.summary:
            # Include summary AND protected messages
            protected_msgs = [m for m in self.messages if getattr(m, 'protected', False)]
            
            # Sequencing for the summary itself
            seq_id = self.messages[0].sequence_id if self.messages else 0
            h = content_hash(self.summary)
            kvps = {"sequence_id": seq_id, "hash": h}
            
            result = [OutputMessage(id=f"sum_{seq_id}_{h[:8]}", ai=False, content=self.summary, kvps=kvps)]
            for pm in protected_msgs:
                result.extend(pm.output())
            return result
        else:
            msgs = [m for r in self.messages for m in r.output()]
            return msgs

    async def summarize(self):
        self.summary = await self.summarize_messages(self.messages)
        return self.summary

    async def compress_large_messages(self) -> bool:
        conf = settings.get_settings()
        msg_max_size = min(1000000, conf.get("chat_model_ctx_length", 128000))
        
        # Issue #931: Never compress the last 2 messages (active turn) to prevent
        # destroying the AI's latest completion/deliverable response
        safe_tail = set()
        if len(self.messages) >= 2:
            safe_tail = {id(self.messages[-1]), id(self.messages[-2])}
        elif len(self.messages) == 1:
            safe_tail = {id(self.messages[0])}
        
        large_msgs = []
        for m in self.messages:
            # Skip: already summarized, protected, in safe tail, or user prompts (Issue #931)
            if m.summary or getattr(m, 'protected', False) or id(m) in safe_tail:
                continue
            if not m.ai and not self._is_tool_result(m):
                continue  # Never compress user prompts
            tok = m.get_tokens()
            if tok > msg_max_size:
                large_msgs.append((m, tok))
        
        large_msgs.sort(key=lambda x: x[1], reverse=True)
        for msg, tok in large_msgs:
            # We use output() to get the content safely
            out = msg.output()
            # If output() returns list of dicts, take the first one
            content = out[0]["content"] if isinstance(out, list) and len(out) > 0 else out
            
            text = msg.output_text()
            leng = len(text)
            
            # Safeguard: if tok is 0, avoid division by zero (Issue #416)
            safe_tok = max(1, tok)
            
            # We want to reduce it down to slightly below the max size
            target_tok = int(msg_max_size * 0.9)
            trim_to_chars = int(leng * (target_tok / safe_tok))

            # raw messages will be replaced as a whole, they would become invalid when truncated
            # Issue #931: Summarize instead of replacing with empty placeholder
            if _is_raw_message(content):
                trunc_text = messages.truncate_text(
                    self.history.agent,
                    text,
                    max(trim_to_chars, 2000)  # Keep at least 2000 chars for raw messages
                )
                msg.set_summary(trunc_text if trunc_text.strip() else
                    "Message content replaced to save space in context window"
                )

            # regular messages will be truncated
            else:
                trunc_text = messages.truncate_text(
                    self.history.agent,
                    text,
                    trim_to_chars
                )
                msg.set_summary(trunc_text)

            return True
        return False

    async def compress(self) -> bool:
        compress = await self.compress_large_messages()
        if not compress:
            compress = await self.compress_attention()
        return compress

    async def compress_attention(self) -> bool:
        if len(self.messages) <= 6: # Keep at least 6 messages to avoid over-compressing active context
            return False

        # Identify messages to consider for summarization (skipping the first and last four)
        # First message is usually the topic starter, last four are the active turn.
        candidate_indices = list(range(1, len(self.messages) - 4))
        
        # Phase 2: Handle Lineage Preservation
        # We identify 'Lineage Threads' (Decision -> Action -> Result chains)
        # and ensure they are either summarized as a whole or preserved.
        threads = self.identify_lineage_threads()
        
        messages_to_sum_indices = []
        protected_indices = set()
        for thread in threads:
            # If a thread has critical Reasoning (Thoughts), protect it
            if any(self._is_critical(self.messages[idx]) for idx in thread):
                protected_indices.update(thread)
        
        skip_next = False
        for i, idx in enumerate(candidate_indices):
            if idx in protected_indices:
                continue
                
            if skip_next:
                skip_next = False
                continue
                
            msg = self.messages[idx]
            if self._is_critical(msg):
                # If this is a tool call, also skip the next message (the result)
                if self._is_tool_call(msg) and (idx + 1) in candidate_indices:
                    skip_next = True
                continue
            
            messages_to_sum_indices.append(idx)
        
        if not messages_to_sum_indices:
            return False

        # Determine how many to actually summarize based on ratio
        cnt_to_sum = math.ceil(len(messages_to_sum_indices) * TOPIC_COMPRESS_RATIO)
        if cnt_to_sum < 1:
            return False

        # Pick a contiguous block of non-critical messages
        sum_start = messages_to_sum_indices[0]
        sum_end = sum_start
        for i in range(1, len(messages_to_sum_indices)):
            if messages_to_sum_indices[i] == sum_end + 1:
                sum_end = messages_to_sum_indices[i]
                if (sum_end - sum_start + 1) >= cnt_to_sum:
                    break
            else:
                break
        
        msg_to_sum = self.messages[sum_start : sum_end + 1]
        if not msg_to_sum:
            return False

        # Token Delta Logging
        before_tokens = sum(m.get_tokens() for m in msg_to_sum)
        
        summary = await self.summarize_messages(msg_to_sum)
        sum_msg_content = self.history.agent.parse_prompt(
            "fw.msg_summary.md", summary=summary
        )
        # Assign sequence_id of the first message in the range to preserve order
        first_msg = msg_to_sum[0]
        sum_msg = Message(False, sum_msg_content, sequence_id=first_msg.sequence_id)
        
        after_tokens = sum_msg.get_tokens()
        self.history.agent.context.log.log(
            type="info",
            heading="📊 Context Compression Stats",
            content=f"Condensed {len(msg_to_sum)} messages: {before_tokens} → {after_tokens} tokens (Saved {before_tokens - after_tokens} tokens)."
        )

        self.messages[sum_start : sum_end + 1] = [sum_msg]
        return True

    def identify_lineage_threads(self) -> List[List[int]]:
        """
        Identify message sequences that form logical threads.
        Pattern: [HumanMessage? + AIMessage (Thought) + HumanMessage (Tool Result) + AIMessage (Analysis)]
        """
        threads = []
        i = 0
        while i < len(self.messages):
            thread = []
            # 1. Look for Thought/Plan (AIMessage)
            if self.messages[i].ai and self._has_reasoning(self.messages[i]):
                thread.append(i)
                # 2. Look for subsequent Tool Result (HumanMessage)
                if i + 1 < len(self.messages) and not self.messages[i+1].ai and self._is_tool_result(self.messages[i+1]):
                    thread.append(i + 1)
                    # 3. Look for follow-up Analysis (AIMessage)
                    if i + 2 < len(self.messages) and self.messages[i+2].ai:
                        thread.append(i + 2)
                
            if thread:
                threads.append(thread)
                i += len(thread)
            else:
                i += 1
        return threads

    def _has_reasoning(self, message: Message) -> bool:
        """Check if message contains <thought> or reasoning blocks."""
        text = str(message.content)
        return "<thought>" in text or "</thought>" in text or '"thought":' in text or '"reasoning":' in text

    def _is_tool_result(self, message: Message) -> bool:
        """Check if message is a tool result."""
        text = str(message.content)
        return '"type": "tool_result"' in text or '"tool_result":' in text or '"tool_output":' in text

    def _is_critical(self, message: Message) -> bool:
        """Check if a message is critical and should be preserved (e.g., contains tool calls, reasoning, or is explicitly protected)."""
        # Lineage Preservation: Always preserve explicitly protected messages
        if getattr(message, "protected", False):
            return True

        # Issue #931: User prompts must NEVER be removed — this is a design invariant.
        # Only intermediate tool call/result exchanges are eligible for summarization.
        if not message.ai and not self._is_tool_result(message):
            return True

        text = str(message.content)
        # Look for agix tool call pattern
        if self._is_tool_call(message):
            return True
        # Also check for "tool_result" keywords
        if '"type": "tool_result"' in text or '"tool_result":' in text:
            return True
        # Reasoning Preservation: Preserve thinking/thought blocks
        if "<thought>" in text or "</thought>" in text or '"thought":' in text or '"reasoning":' in text:
            return True
        # If it's a summary itself, we can summarize it again if needed, but usually we keep them
        if "### Previous Conversation Summary" in text:
            return True
        # Issue #931: Preserve AI completion messages that contain deliverables
        # These are the actual responses the user sees — losing them causes empty bubbles
        if message.ai and self._is_completion_message(message):
            return True
        return False

    def _is_completion_message(self, message: Message) -> bool:
        """Issue #931: Check if an AI message is a final completion/deliverable response."""
        text = str(message.content)
        # Check for response tool pattern (the standard way agents deliver responses)
        if '"tool_name": "response"' in text or '"tool_name":"response"' in text:
            return True
        # Check for deliverable markers
        if 'save_deliverable' in text or '[[DELIVERABLE]]' in text:
            return True
        # Check for subordinate result markers
        if 'call_subordinate' in text and ('"result"' in text or '"output"' in text):
            return True
        return False

    def _is_tool_call(self, message: Message) -> bool:
        """Helper to identify if a message contains a tool call."""
        text = str(message.content)
        return ('"tool_name":' in text and '"tool_args":' in text) or '"type": "tool_use"' in text

    async def summarize_messages(self, messages: list[Message]):
        # FIXME: vision bytes are sent to utility LLM, send summary instead
        msg_txt = [m.output_text() for m in messages]
        total_chars = sum(len(txt) for txt in msg_txt)
        logging.getLogger("agix.history").debug(f"Summarizing {len(messages)} messages ({total_chars} chars)...")
        
        # Check if structured summary prompt exists, otherwise fallback to old ones
        sys_prompt_path = files.get_abs_path("prompts/fw.structured_summary.sys.md")
        if os.path.exists(sys_prompt_path):
            system = self.history.agent.read_prompt("fw.structured_summary.sys.md")
        else:
            system = self.history.agent.read_prompt("fw.topic_summary.sys.md")

        # Get utility model limit for chunked summarization if needed
        # Fallback to roughly 500k chars for safety (~125k-250k tokens)
        MAX_SUMMARIZER_CHARS = 500000 
        
        full_text = "\n---\n".join(msg_txt)
        
        if len(full_text) > MAX_SUMMARIZER_CHARS:
            # Recursive Chunked Summarization (Issue #416 Fix)
            logging.getLogger("agix.history").info(f"Input too large for direct summarization ({len(full_text)} chars). Chunking...")
            
            if len(messages) == 1:
                logging.getLogger("agix.history").info("Single message too large for summary. Truncating...")
                # SAFEGUARD: If a single message is too large, truncate it first instead of recursing
                # Truncate to roughly 90% of the limit to allow for template overhead
                messages[0].truncate(MAX_SUMMARIZER_CHARS // 5) 
                # Re-calculate text
                msg_txt = [messages[0].output_text()]
                full_text = msg_txt[0]
                if len(full_text) <= MAX_SUMMARIZER_CHARS:
                    # Continue to normal summarization below
                    pass
                else:
                    # If still too large (unlikely), just return a hard truncation
                    return f"Large message truncation: {full_text[:MAX_SUMMARIZER_CHARS//2]}... [TRUNCATED]"

            if len(messages) > 1:
                # Split messages into two halves
                mid = len(messages) // 2
                first_half = await self.summarize_messages(messages[:mid])
                second_half = await self.summarize_messages(messages[mid:])
                
                # Final merge summary
                summary = await self.history.agent.call_utility_model(
                    system=system,
                    message=self.history.agent.read_prompt(
                        "fw.topic_summary.msg.md", content=[f"PART 1 SUMMARY: {first_half}", f"PART 2 SUMMARY: {second_half}"]
                    ),
                )
                return summary

        summary = await self.history.agent.call_utility_model(
            system=system,
            message=self.history.agent.read_prompt(
                "fw.topic_summary.msg.md", content=msg_txt
            ),
        )
        return summary


    def to_dict(self):
        return {
            "_cls": "Topic",
            "summary": self.summary,
            "messages": [m.to_dict() for m in self.messages],
        }

    @staticmethod
    def from_dict(data: dict, history: "History"):
        topic = Topic(history=history)
        topic.summary = data.get("summary", "")
        topic.messages = [
            Message.from_dict(m, history=history) for m in data.get("messages", [])
        ]
        return topic


def _extract_protected_messages_recursive(record) -> list:
    """
    Issue #1013: Recursively extract all protected Message objects from a record tree.
    Handles nested Bulks (from merge_bulks), Topics, and bare Messages at any depth.
    """
    protected = []
    if isinstance(record, Bulk):
        for r in record.records:
            protected.extend(_extract_protected_messages_recursive(r))
    elif isinstance(record, Topic):
        for m in record.messages:
            if getattr(m, 'protected', False):
                protected.append(m)
    elif isinstance(record, Message) and getattr(record, 'protected', False):
        protected.append(record)
    return protected


class Bulk(Record):
    def __init__(self, history: "History"):
        self.history = history
        self.summary: str = ""
        self.records: list[Record] = []

    def get_tokens(self):
        if self.summary:
            base_tokens = tokens.approximate_tokens(self.summary)
            # Find protected messages in records (Topics or Messages)
            p_tokens = 0
            for r in self.records:
                if isinstance(r, Topic):
                    p_tokens += sum(m.get_tokens() for m in r.messages if getattr(m, 'protected', False))
                elif isinstance(r, Message) and getattr(r, 'protected', False):
                    p_tokens += r.get_tokens()
            return base_tokens + p_tokens
        else:
            return sum([r.get_tokens() for r in self.records])

    def output(
        self, human_label: str = "user", ai_label: str = "ai"
    ) -> list[OutputMessage]:
        if self.summary:
            # Sequencing for the bulk summary
            seq_id = 0
            if self.records:
                first = self.records[0]
                if hasattr(first, "sequence_id"):
                    seq_id = first.sequence_id
                elif hasattr(first, "messages") and first.messages:
                    seq_id = first.messages[0].sequence_id
            
            h = content_hash(self.summary)
            kvps = {"sequence_id": seq_id, "hash": h}
            
            result = [OutputMessage(ai=False, content=self.summary, kvps=kvps)]
            # Include all protected messages from underlying records (recursive for nested Bulks — #1013)
            for pm in _extract_protected_messages_recursive(self):
                result.extend(pm.output())
            return result
        else:
            msgs = [m for r in self.records for m in r.output()]
            return msgs

    async def compress(self):
        return False

    async def summarize(self):
        self.summary = await self.history.agent.call_utility_model(
            system=self.history.agent.read_prompt("fw.topic_summary.sys.md"),
            message=self.history.agent.read_prompt(
                "fw.topic_summary.msg.md", content=self.output_text()
            ),
        )
        return self.summary

    def to_dict(self):
        return {
            "_cls": "Bulk",
            "summary": self.summary,
            "records": [r.to_dict() for r in self.records],
        }

    @staticmethod
    def from_dict(data: dict, history: "History"):
        bulk = Bulk(history=history)
        bulk.summary = data["summary"]
        cls = data["_cls"]
        bulk.records = [Record.from_dict(r, history=history) for r in data["records"]]
        return bulk


class History(Record):
    @property
    def messages_all(self) -> list[Message]:
        """Returns a flat list of all Message objects in the history (bulks, topics, current)."""
        all_msgs = []
        for bulk in self.bulks:
            for record in bulk.records:
                if isinstance(record, Topic):
                    all_msgs.extend(record.messages)
                elif isinstance(record, Message):
                    all_msgs.append(record)
        for topic in self.topics:
            all_msgs.extend(topic.messages)
        all_msgs.extend(self.current.messages)
        return all_msgs

    @property
    def messages(self) -> list[Message]:
        """Convenience property for compatibility (same as messages_all)."""
        return self.messages_all

    def __init__(self, agent):
        from python.agent import Agent

        self.counter = 0
        self.bulks: list[Bulk] = []
        self.topics: list[Topic] = []
        self.current = Topic(history=self)
        self.agent: Agent = agent
        self._first_message_protected = False  # Issue #227: Track if the very first prompt is protected

    def get_tokens(self) -> int:
        return (
            self.get_bulks_tokens()
            + self.get_topics_tokens()
            + self.get_current_topic_tokens()
        )

    def is_over_limit(self):
        limit = self.get_ctx_limit()
        total = self.get_tokens()
        return total > limit

    def get_ctx_limit(self) -> int:
        """
        Calculates the token limit for history compression.
        Prioritizes agent-specific model configuration, falls back to global settings.
        """
        limit = 0
        source = "global"
        
        try:
            from python.helpers.settings import get_settings
            s = get_settings()
            
            # 1. Check Global Model Override (Highest Priority for Context Resolution)
            if s.get("global_model_enabled") and s.get("global_model_ctx_length", 0) > 0:
                ctx_len = s["global_model_ctx_length"]
                history_ratio = s.get("chat_model_ctx_history", 0.8)
                limit = int(ctx_len * history_ratio)
                source = "global_model_override_ctx"
            
            # 2. Try to get limit from current agent's model config
            elif hasattr(self, "agent") and self.agent:
                model = self.agent.get_data("chat_model")
                if model and hasattr(model, "ctx_length") and model.ctx_length > 0:
                    ctx_len = model.ctx_length
                    history_ratio = s.get("chat_model_ctx_history", 0.8)
                    limit = int(ctx_len * history_ratio)
                    source = f"agent_model_ctx ({getattr(model, 'model_name', 'unknown')})"
        except Exception:
            pass

        if not limit:
            # 3. Fallback to global settings (the old default)
            limit = _get_ctx_size_for_history()
            source = "global_settings_fallback"

        # Log for debugging context awareness
        # from python.helpers.log import Log
        # self.agent.log(Log.Type.DEBUG, f"Context limit for {self.agent.agent_name}: {limit} tokens (source: {source})")
        
        return limit

    def get_bulks_tokens(self) -> int:
        return sum(record.get_tokens() for record in self.bulks)

    def get_topics_tokens(self) -> int:
        return sum(record.get_tokens() for record in self.topics)

    def get_current_topic_tokens(self) -> int:
        return self.current.get_tokens()

    def add_message(
        self, ai: bool, content: MessageContent, tokens: int = 0, model: str = "", provider: str = "", id: str = "", protected: bool = False, sender_type: str = "", sender_id: str = "", sequence_id: int = 0, hash: str = "", **kwargs
    ) -> Message:
        # Increment global counter
        self.counter += 1
        
        # Use provided sequence_id ONLY if it's greater than 0, otherwise use the monotonic counter
        # This ensures that even if the client sends 0 (default), we assign a real sequence.
        seq_id = sequence_id if sequence_id > 0 else self.counter
        
        # Ensure counter stays in sync if a high sequence_id was provided
        if seq_id > self.counter:
            self.counter = seq_id

        msg = self.current.add_message(ai, content=content, tokens=tokens, model=model, provider=provider, id=id, protected=protected, sender_type=sender_type, sender_id=sender_id, sequence_id=seq_id, hash=hash, **kwargs)
        
        # Issue #227: Protect the VERY FIRST message (origin insensitive)
        # This keeps the initial prompt (Human, Agent, or API) as the first tile and sequence anchor
        if not self._first_message_protected:
            msg.protected = True
            
            # Detect and label origin if not explicitly provided
            if not msg.kvps.get("origin"):
                sender_type_lower = sender_type.lower() if sender_type else ""
                if sender_type_lower == "human":
                    msg.kvps["origin"] = "Human"
                elif sender_type_lower == "agent":
                    msg.kvps["origin"] = "Agent"
                elif sender_type_lower == "api":
                    msg.kvps["origin"] = "API"
                elif not ai:
                    msg.kvps["origin"] = "Human"
                else:
                    msg.kvps["origin"] = "Agent"
            
            self._first_message_protected = True
            
        return msg

    def remove_message(self, id: str) -> bool:
        # Check current topic
        if self.current.remove_message(id):
            return True
        # Check topics
        for topic in self.topics:
            if topic.remove_message(id):
                return True
        # Check bulks
        for bulk in self.bulks:
            for record in bulk.records:
                if isinstance(record, Topic):
                    if record.remove_message(id):
                        return True
        return False

    def new_topic(self):
        if self.current.messages:
            self.topics.append(self.current)
            self.current = Topic(history=self)

    def output(self) -> list[OutputMessage]:
        result: list[OutputMessage] = []
        result += [m for b in self.bulks for m in b.output()]
        result += [m for t in self.topics for m in t.output()]
        result += self.current.output()
        return result

    @staticmethod
    def from_dict(data: dict, history: "History"):
        history.counter = data.get("counter", 0)
        history.bulks = [Bulk.from_dict(b, history=history) for b in data["bulks"]]
        history.topics = [Topic.from_dict(t, history=history) for t in data["topics"]]
        history.current = Topic.from_dict(data["current"], history=history)
        return history

    def to_dict(self):
        return {
            "_cls": "History",
            "counter": self.counter,
            "bulks": [b.to_dict() for b in self.bulks],
            "topics": [t.to_dict() for t in self.topics],
            "current": self.current.to_dict(),
        }

    def prune_to_turns(self, turns: int):
        """
        Prune history to the last N turns.
        A turn is one user message and one AI response (2 messages).
        """
        max_messages = turns * 2
        
        # Collect all messages in order
        all_messages: list[Message] = []
        for bulk in self.bulks:
            for record in bulk.records:
                if isinstance(record, Topic):
                    all_messages.extend(record.messages)
        for topic in self.topics:
            all_messages.extend(topic.messages)
        all_messages.extend(self.current.messages)
        
        if len(all_messages) <= max_messages:
            return
    
        # Lineage Preservation: identify all protected messages
        protected_indices = {i for i, msg in enumerate(all_messages) if getattr(msg, "protected", False)}
        # Always keep the very first message
        protected_indices.add(0)
        
        # Also protect the first human message if it's not at index 0 (Issue #238)
        for i, msg in enumerate(all_messages):
            if not msg.ai and i not in protected_indices:
                protected_indices.add(i)
                break
        # Auto-Lineage Protection: protect messages with certain keywords (Issue #167)
        lineage_keywords = ["Lineage:", "Mission:", "Task Context:", "Status Report:"]
        for i, msg in enumerate(all_messages):
            if any(kw in msg.output_text() for kw in lineage_keywords) and i not in protected_indices:
                protected_indices.add(i)
        
        # Calculate how many "fresh" messages we can keep from the tail
        # We ALWAYS want to keep the requested number of turns (tail), even if we have protected ones
        tail_indices = set(range(max(0, len(all_messages) - max_messages), len(all_messages)))
            
        # Combine and sort to maintain order
        keep_indices = sorted(list(protected_indices | tail_indices))
        new_messages = [all_messages[i] for i in keep_indices]
        
        # Keep things simple for now: Clear and put everything into 'current' topic
        self.bulks = []
        self.topics = []
        self.current = Topic(history=self)
        self.current.messages = new_messages
        # Note: We don't reset self.counter as it represents the absolute msg index

    def prune_to_tokens(self, max_tokens: int):
        """
        Prune history to stay within a token limit while protecting key messages (Issue #238).
        """
        all_messages: list[Message] = []
        for bulk in self.bulks:
            for record in bulk.records:
                if isinstance(record, Topic):
                    all_messages.extend(record.messages)
        for topic in self.topics:
            all_messages.extend(topic.messages)
        all_messages.extend(self.current.messages)
        
        if not all_messages:
            return

        # 1. Identify protected indices
        protected_indices = {i for i, msg in enumerate(all_messages) if getattr(msg, "protected", False)}
        protected_indices.add(0)
        
        # Also protect first human message if it's not at index 0 (Issue #238)
        for i, msg in enumerate(all_messages):
            if not msg.ai and i not in protected_indices:
                protected_indices.add(i)
                break
        # Auto-Lineage Protection: protect messages with certain keywords (Issue #167)
        lineage_keywords = ["Lineage:", "Mission:", "Task Context:", "Status Report:"]
        for i, msg in enumerate(all_messages):
            if any(kw in msg.output_text() for kw in lineage_keywords) and i not in protected_indices:
                protected_indices.add(i)
        
        # 2. Add candidates from the tail until we hit the token limit
        keep_indices = set(protected_indices)
        current_tokens = sum(all_messages[i].get_tokens() for i in keep_indices)
        
        # Iterate from tail to head, skipping already protected ones
        for i in range(len(all_messages) - 1, -1, -1):
            if i in keep_indices:
                continue
            
            msg_tokens = all_messages[i].get_tokens()
            if current_tokens + msg_tokens <= max_tokens:
                keep_indices.add(i)
                current_tokens += msg_tokens
            else:
                break # Hit the limit
        
        # 3. Reconstruct history
        keep_indices_sorted = sorted(list(keep_indices))
        new_messages = [all_messages[i] for i in keep_indices_sorted]
        
        # 4. Final safety check: If we are still over limit (because of huge protected messages),
        # truncate the deepest/longest messages until we fit.
        current_total = sum(m.get_tokens() for m in new_messages)
        if current_total > max_tokens:
            logging.getLogger("agix.history").warning(
                f"Pruning insufficient ({current_total:,} > {max_tokens:,}). "
                f"Truncating individual messages to fit..."
            )
            
            # Multiple passes to aggressively reduce
            for pass_num in range(5):  # Up to 5 passes
                if current_total <= max_tokens:
                    break
                    
                # Sort by size for most impact
                to_truncate = sorted(new_messages, key=lambda x: x.get_tokens(), reverse=True)
                for msg in to_truncate:
                    if current_total <= max_tokens: 
                        break
                    
                    msg_tokens = msg.get_tokens()
                    # Target: reduce to fit within budget proportionally
                    # Each pass: try to reduce large messages by 50%
                    if msg_tokens > (max_tokens * 0.05):  # Target anything over 5% of budget
                        before = msg_tokens
                        target = max(1000, int(before * 0.5))  # At least halve it
                        if msg.truncate(target):
                            after = msg.get_tokens()
                            current_total -= (before - after)
                            logging.getLogger("agix.history").info(
                                f"Pass {pass_num+1}: Reduced message from {before:,} to {after:,} tokens. "
                                f"Total now: {current_total:,}"
                            )

            # If still over after 5 passes, apply proportional brute force
            if current_total > max_tokens:
                # Safeguard: if current_total is 0, avoid division by zero (Issue #416)
                safe_total_val = max(1, current_total)
                ratio = max_tokens / safe_total_val
                logging.getLogger("agix.history").warning(
                    f"Still over budget ({current_total:,} > {max_tokens:,}). "
                    f"Applying proportional reduction (ratio={ratio:.2f})..."
                )
                for msg in new_messages:
                    before = msg.get_tokens()
                    if before > 100:  # Don't touch tiny messages
                        msg.truncate(max(50, int(before * ratio * 0.8)))
                
                # Recalculate after brute force
                current_total = sum(m.get_tokens() for m in new_messages)
                if current_total > max_tokens:
                    logging.getLogger("agix.history").critical(
                        f"CRITICAL: Could not reduce history to {max_tokens:,} tokens. "
                        f"Final size: {current_total:,}. This may cause API errors."
                    )
        
        self.bulks = []
        self.topics = []
        self.current = Topic(history=self)
        self.current.messages = new_messages



    def serialize(self):
        data = self.to_dict()
        return _json_dumps(data)

    async def compress(self):
        compressed = False
        while True:
            curr, hist, bulk = (
                self.get_current_topic_tokens(),
                self.get_topics_tokens(),
                self.get_bulks_tokens(),
            )
            # Use instance method to respect per-agent model config (Issue #399)
            total = self.get_ctx_limit()
            ratios = [
                (curr, CURRENT_TOPIC_RATIO, "current_topic"),
                (hist, HISTORY_TOPIC_RATIO, "history_topic"),
                (bulk, HISTORY_BULK_RATIO, "history_bulk"),
            ]
            # Safeguard: if total is 0 or ratio is 0, avoid DivisionByZero but allow sorting (Issue #416)
            safe_total = max(1, total)
            
            def get_sort_key(x):
                curr_tok, ratio_val, _ = x
                denominator = safe_total * ratio_val
                if denominator == 0: return 0 
                return curr_tok / denominator

            ratios = sorted(ratios, key=get_sort_key, reverse=True)
            compressed_part = False
            for ratio in ratios:
                if ratio[0] > ratio[1] * total:
                    over_part = ratio[2]
                    logging.getLogger("agix.history").debug(f"Compressing {over_part}: {ratio[0]} tokens > {ratio[1]}*ctx_limit")
                    if over_part == "current_topic":
                        compressed_part = await self.current.compress()
                    elif over_part == "history_topic":
                        # RCA-475 P3: Check cooldown before retrying after timeout
                        from python.helpers.compression_progress import (
                            should_defer_compression,
                        )
                        if should_defer_compression(self.agent.data):
                            logging.getLogger("agix.history").debug(
                                "[HISTORY] Deferring compression — rate limit cooldown active"
                            )
                            return compressed
                        compressed_part = await self.compress_topics()
                    else:
                        compressed_part = await self.compress_bulks()
                    if compressed_part:
                        break

            if compressed_part:
                compressed = True
                continue
            else:
                return compressed

    async def compress_topics(self, max_time_per_batch: float = 120.0) -> bool:
        # identify topics without summary
        topics_to_summarize = [t for t in self.topics if not t.summary]
        
        if topics_to_summarize:
            # summarize in batch
            batcher = get_llm_batcher()
            
            # Use utility model for summarization
            utility_model = self.agent.get_data("utility_model") or self.agent.get_data("chat_model")
            
            # F-2 (RCA-467): Middle-out deduplication — collapse consecutive
            # identical messages before sending to LLM.  Prevents LLM
            # degeneration when agent history contains repetitive tool calls.
            from python.helpers.history_utils import (
                deduplicate_messages_middle_out,
                fallback_truncate_messages,
            )

            # Prepare batch requests
            system_prompt = self.agent.read_prompt("fw.topic_summary.sys.md")
            message_sets = []
            for t in topics_to_summarize:
                raw_texts = [m.output_text() for m in t.messages]
                # Deduplicate before sending to LLM
                deduped_texts = deduplicate_messages_middle_out(raw_texts)
                message_sets.append([
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": self.agent.read_prompt(
                        "fw.topic_summary.msg.md",
                        content=deduped_texts,
                    )},
                ])
            
            try:
                # Get model string from LazyModelWrapper (batch_complete expects str, not wrapper)
                from python.helpers.models_lazy import LazyModelWrapper
                if isinstance(utility_model, LazyModelWrapper):
                    model_str = f"{utility_model.provider}/{utility_model.model_name}"
                elif hasattr(utility_model, 'model_name'):
                    model_str = utility_model.model_name
                else:
                    model_str = str(utility_model)
                
                # F-3 (RCA-467): Skip repetition detection for utility
                # summarization calls — the input itself may be repetitive
                # and _is_degenerate_repetition produces false positives.
                #
                # RCA-475 Fix 4: Wrap with asyncio.wait_for to enforce
                # max_time_per_batch. Without this, rate-limited LLM calls
                # can stall the entire compression pipeline for 5+ minutes.
                responses = await asyncio.wait_for(
                    batcher.batch_complete(
                        model=model_str,
                        message_sets=message_sets,
                        temperature=0.0,
                        agix_skip_repetition_check=True,
                    ),
                    timeout=max_time_per_batch,
                )
                
                # Apply summaries
                for topic, resp in zip(topics_to_summarize, responses):
                    # F-1 (RCA-467): Check for exception FIRST — batcher
                    # stores exceptions as result values (llm_batcher.py:88).
                    if isinstance(resp, Exception):
                        logging.getLogger("agix.history").error(
                            f"Failed to summarize topic: {resp}"
                        )
                        # F-5 (RCA-467): Brute-force fallback — truncate
                        # messages directly instead of useless placeholder.
                        raw_texts = [m.output_text() for m in topic.messages]
                        truncated = fallback_truncate_messages(raw_texts)
                        topic.summary = "\n".join(truncated)
                        continue

                    # Robust extraction of content from response
                    content = ""
                    if hasattr(resp, "choices") and resp.choices:
                        choice = resp.choices[0]
                        if hasattr(choice, "message"):
                            content = getattr(choice.message, "content", "")
                        elif isinstance(choice, dict) and "message" in choice:
                            msg = choice["message"]
                            content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
                    elif isinstance(resp, dict) and "choices" in resp:
                        choices = resp.choices if hasattr(resp, "choices") else resp["choices"]
                        if choices:
                            choice = choices[0]
                            msg = choice.get("message", {}) if isinstance(choice, dict) else getattr(choice, "message", {})
                            content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
                    elif isinstance(resp, str) and resp: # Fallback for string-return wrappers
                        content = resp

                    if content:
                        topic.summary = content
                    else:
                        # F-5 (RCA-467): Fallback truncation instead of
                        # useless "Summary generation failed." placeholder
                        raw_texts = [m.output_text() for m in topic.messages]
                        truncated = fallback_truncate_messages(raw_texts)
                        topic.summary = "\n".join(truncated)
                        logging.getLogger("agix.history").warning(
                            f"Topic summarization produced empty content — "
                            f"used fallback truncation ({len(raw_texts)} msgs → "
                            f"{len(truncated)} segments)"
                        )
            except asyncio.TimeoutError:
                # RCA-475 P3: Track progress so compression resumes where it
                # left off on next activation instead of restarting from scratch.
                from python.helpers.compression_progress import (
                    record_compression_progress,
                )
                already_done = sum(1 for t in topics_to_summarize if t.summary)
                remaining = [t for t in topics_to_summarize if not t.summary]
                logging.getLogger("agix.history").warning(
                    f"[HISTORY] Batch summarization timed out after {max_time_per_batch}s "
                    f"(likely rate-limited). {already_done}/{len(topics_to_summarize)} "
                    f"topics done — will resume {len(remaining)} on next activation."
                )
                record_compression_progress(
                    self.agent.data,
                    summarized=already_done,
                    total=len(topics_to_summarize),
                    timed_out=True,
                )
                # Fallback truncation only for still-unsummarized topics
                for topic in remaining:
                    raw_texts = [m.output_text() for m in topic.messages]
                    truncated = fallback_truncate_messages(raw_texts)
                    topic.summary = "\n".join(truncated)
            except Exception as e:
                logging.getLogger("agix.history").error(f"Error in batch summarization: {e}")
                # F-5 (RCA-467): Fallback truncation for ALL topics on batch error
                for topic in topics_to_summarize:
                    raw_texts = [m.output_text() for m in topic.messages]
                    truncated = fallback_truncate_messages(raw_texts)
                    topic.summary = "\n".join(truncated)
            
            # RCA-475 P3: All topics summarized — clear progress tracker
            from python.helpers.compression_progress import (
                clear_compression_progress,
            )
            clear_compression_progress(self.agent.data)
            return True

        # move oldest topic to bulks and summarize
        if self.topics:
            topic = self.topics[0]
            bulk = Bulk(history=self)
            bulk.records.append(topic)
            if topic.summary:
                bulk.summary = topic.summary
            else:
                await bulk.summarize()
            self.bulks.append(bulk)
            self.topics.remove(topic)
            return True
        return False

    async def compress_bulks(self):
        # merge bulks if possible
        compressed = await self.merge_bulks_by(BULK_MERGE_COUNT)
        # remove oldest bulk if necessary
        if not compressed:
            # Issue #931: Log when dropping bulk history so we can trace history loss
            if self.bulks:
                dropped_tokens = self.bulks[0].get_tokens()
                logging.getLogger("agix.history").warning(
                    f"Dropping oldest bulk ({dropped_tokens:,} tokens) to stay within context limit"
                )
                # Issue #1013: Extract protected messages BEFORE dropping the bulk
                dropped_bulk = self.bulks[0]
                protected_msgs = _extract_protected_messages_recursive(dropped_bulk)
                self.bulks.pop(0)
                # Re-inject protected messages into current topic at the front
                if protected_msgs:
                    logging.getLogger("agix.history").info(
                        f"Preserved {len(protected_msgs)} protected message(s) from dropped bulk"
                    )
                    for i, msg in enumerate(protected_msgs):
                        self.current.messages.insert(i, msg)
            return True
        return compressed

    async def merge_bulks_by(self, count: int):
        # if bulks is empty, return False
        if len(self.bulks) == 0:
            return False
        # merge bulks in groups of count, even if there are fewer than count
        # Phase 3 hardening: asyncio.wait with timeout replaces bare asyncio.gather
        # to prevent LLM summarization hangs from blocking history compression
        merge_coros = [
            self.merge_bulks(self.bulks[i : i + count])
            for i in range(0, len(self.bulks), count)
        ]
        if not merge_coros:
            return False
        futures = [asyncio.ensure_future(c) for c in merge_coros]
        done, pending = await asyncio.wait(futures, timeout=300.0)
        if pending:
            import logging
            logging.getLogger(__name__).warning(
                f"History merge_bulks_by: {len(pending)}/{len(futures)} tasks timed out after 300s, cancelling"
            )
            for p in pending:
                p.cancel()
            # Brief wait for cancellation to propagate
            await asyncio.wait(pending, timeout=5.0)
        # Collect results: completed bulks or None for timed-out ones
        bulks = []
        for f in futures:
            if f in done and not f.cancelled():
                try:
                    bulks.append(f.result())
                except Exception:
                    bulks.append(None)
            else:
                bulks.append(None)
        # Filter out None (failed/timed-out merges) — keep original bulks for those
        self.bulks = [b for b in bulks if b is not None]
        return True

    async def merge_bulks(self, bulks: list[Bulk]) -> Bulk:
        bulk = Bulk(history=self)
        bulk.records = cast(list[Record], bulks)
        await bulk.summarize()
        return bulk


def deserialize_history(json_data: Union[str, dict], agent) -> History:
    history = History(agent=agent)
    if json_data:
        if isinstance(json_data, str):
            data = _json_loads(json_data)
        else:
            data = json_data
        history = History.from_dict(data, history=history)
        
        # Issue #227: Apply retroactive first-prompt protection for old chats
        # Find the first human message across all messages and ensure it's protected
        _apply_retroactive_first_prompt_protection(history)
        
    return history


def _apply_retroactive_first_prompt_protection(history: History) -> None:
    """
    Issue #227: Ensure the first human message is protected, even for old chats
    that were saved before the protection feature was added.
    """
    # Collect all messages in order
    all_messages: list[Message] = []
    for bulk in history.bulks:
        for record in bulk.records:
            if isinstance(record, Topic):
                all_messages.extend(record.messages)
    for topic in history.topics:
        all_messages.extend(topic.messages)
    all_messages.extend(history.current.messages)
    
    # Find and protect the first human message
    for msg in all_messages:
        if not msg.ai:
            if not msg.protected:
                msg.protected = True
            # Mark that we found and protected the first human message
            history._first_human_protected = True
            break


def _get_ctx_size_for_history() -> int:
    set = settings.get_settings()
    # Respect global override even in pure-settings fallback
    ctx_len = set.get("global_model_ctx_length", 0) if set.get("global_model_enabled") else 0
    if ctx_len <= 0:
        ctx_len = set.get("chat_model_ctx_length", 128000)
    
    return int(ctx_len * set.get("chat_model_ctx_history", 0.8))


def _stringify_output(output: OutputMessage, ai_label="ai", human_label="human"):
    return f'{ai_label if output["ai"] else human_label}: {_stringify_content(output["content"])}'


def _stringify_content(content: MessageContent) -> str:
    # already a string
    if isinstance(content, str):
        return content
    
    # raw messages return preview or trimmed json
    if _is_raw_message(content):
        preview: str = content.get("preview", "") # type: ignore
        if preview:
            return preview
        text = _json_dumps(content)
        if len(text) > RAW_MESSAGE_OUTPUT_TEXT_TRIM:
            return text[:RAW_MESSAGE_OUTPUT_TEXT_TRIM] + "... TRIMMED"
        return text
    
    # regular messages of non-string are dumped as json
    text = _json_dumps(content)
    # NOTE: 10000 chars to preserve large user prompts (project briefs, vision docs).
    # Previous 2000-char limit silently destroyed user intent — see Forgejo #972.
    if len(text) > 10000:
        return text[:10000] + "... (content trimmed for summarization)"
    return text


def _output_content_langchain(content: MessageContent):
    if isinstance(content, str):
        return content
    if _is_raw_message(content):
        return content["raw_content"]  # type: ignore
    try:
        return _json_dumps(content)
    except Exception as e:
        raise e


def group_outputs_abab(outputs: list[OutputMessage]) -> list[OutputMessage]:
    result = []
    for out in outputs:
        if result and result[-1]["ai"] == out["ai"]:
            result[-1] = OutputMessage(
                ai=result[-1]["ai"],
                content=_merge_outputs(result[-1]["content"], out["content"]),
            )
        else:
            result.append(out)
    return result


def group_messages_abab(messages: list[BaseMessage]) -> list[BaseMessage]:
    result = []
    for msg in messages:
        if result and isinstance(result[-1], type(msg)):
            # create new instance of the same type with merged content
            result[-1] = type(result[-1])(content=_merge_outputs(result[-1].content, msg.content))  # type: ignore
        else:
            result.append(msg)
    return result


def output_langchain(messages: list[OutputMessage]):
    result = []
    for m in messages:
        content = _output_content_langchain(content=m["content"])
        
        # CRITICAL FIX: Validate AI messages have content (LLM API requires content OR tool_calls)
        # Empty AI messages cause: "Assistant messages must have either content or tool_calls"
        if m["ai"]:
            # RCA-251 §5.5 NullContentPreserver: Check if this is a tool-call-only
            # message (content=None but tool_calls present). These MUST be preserved
            # — skipping them corrupts history by losing the agent's action record.
            has_tool_calls = False
            if isinstance(content, dict):
                # raw_content dict from _output_content_langchain
                raw = content
                has_tool_calls = bool(raw.get("tool_calls"))
                # Extract actual content from the raw dict, or use tool call summary
                actual_content = raw.get("content")
                if actual_content:
                    content = actual_content
                elif has_tool_calls:
                    # Preserve tool-call-only messages with a placeholder
                    tool_names = [
                        tc.get("function", {}).get("name", "unknown")
                        if isinstance(tc, dict) else "unknown"
                        for tc in raw["tool_calls"]
                    ]
                    content = f"[Tool calls: {', '.join(tool_names)}]"
                else:
                    content = ""

            # Skip empty AI messages entirely to prevent LLM API validation errors
            if not content or (isinstance(content, str) and not content.strip()):
                logging.getLogger("agix.history").warning(
                    "Skipping empty AI message to prevent LLM API error"
                )
                continue
            result.append(AIMessage(content))  # type: ignore
        else:
            # Human messages can be empty (though rare) - add placeholder if needed
            if not content or (isinstance(content, str) and not content.strip()):
                content = "(continued)"
            # Handle raw_content dicts for human messages too
            if isinstance(content, dict):
                actual = content.get("content", "")
                content = actual if actual else "(continued)"
            result.append(HumanMessage(content))  # type: ignore
    
    # ensure message type alternation
    result = group_messages_abab(result)
    return result


def output_text(messages: list[OutputMessage], ai_label="ai", human_label="human"):
    return "\n".join(_stringify_output(o, ai_label, human_label) for o in messages)


def output_markdown(messages: list[OutputMessage], ai_label="ai", human_label="human"):
    return "\n\n".join(_markdownify_output(o, ai_label, human_label) for o in messages)


def _markdownify_output(output: OutputMessage, ai_label="ai", human_label="human"):
    label = ai_label if output["ai"] else human_label
    content = _stringify_content(output["content"])
    return f"### {label}\n\n{content}"


def _merge_outputs(a: MessageContent, b: MessageContent) -> MessageContent:
    if isinstance(a, str) and isinstance(b, str):
        return a + "\n" + b

    def make_list(obj: MessageContent) -> list[MessageContent]:
        if isinstance(obj, list):
            return obj  # type: ignore
        if isinstance(obj, dict):
            return [obj]
        if isinstance(obj, str):
            return [{"type": "text", "text": obj}]
        return [obj]

    a = make_list(a)
    b = make_list(b)

    return cast(MessageContent, a + b)


def _merge_properties(
    a: Dict[str, MessageContent], b: Dict[str, MessageContent]
) -> Dict[str, MessageContent]:
    result = a.copy()
    for k, v in b.items():
        if k in result:
            result[k] = _merge_outputs(result[k], v)
        else:
            result[k] = v
    return result


def _is_raw_message(obj: object) -> bool:
    return isinstance(obj, Mapping) and "raw_content" in obj


def _json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


def _json_loads(obj):
    return json.loads(obj)
