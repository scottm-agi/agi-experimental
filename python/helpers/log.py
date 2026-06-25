from __future__ import annotations
from dataclasses import dataclass, field
import json
import os
import random
from typing import Any, Literal, Optional, Dict, TypeVar, TYPE_CHECKING, Callable, Awaitable, List

T = TypeVar("T")
import uuid
from collections import OrderedDict  # Import OrderedDict
from python.helpers.strings import truncate_text_by_ratio
import copy
import time
from datetime import datetime, timezone
from typing import TypeVar
from python.helpers.secrets_helper import get_secrets_manager


if TYPE_CHECKING:
    from python.agent import AgentContext

T = TypeVar("T")

Type = Literal[
    "agent",
    "browser",
    "code_exe",
    "error",
    "hint",
    "info",
    "progress",
    "response",
    "tool",
    "input",
    "user",
    "util",
    "warning",
]

ProgressUpdate = Literal["persistent", "temporary", "none"]


HEADING_MAX_LEN: int = 120
CONTENT_MAX_LEN: int = 15_000
RESPONSE_CONTENT_MAX_LEN: int = 250_000
KEY_MAX_LEN: int = 60
VALUE_MAX_LEN: int = 5000
PROGRESS_MAX_LEN: int = 120


def _truncate_heading(text: Optional[str]) -> str:
    if text is None:
        return ""
    return truncate_text_by_ratio(str(text), HEADING_MAX_LEN, "...", ratio=1.0)


def _truncate_progress(text: Optional[str]) -> str:
    if text is None:
        return ""
    return truncate_text_by_ratio(str(text), PROGRESS_MAX_LEN, "...", ratio=1.0)


def _truncate_key(text: str) -> str:
    return truncate_text_by_ratio(str(text), KEY_MAX_LEN, "...", ratio=1.0)


def _truncate_value(val: T) -> T:
    # If dict, recursively truncate each value
    if isinstance(val, dict):
        for k in list(val.keys()):
            v = val[k]
            del val[k]
            val[_truncate_key(k)] = _truncate_value(v)
        return val
    # If list or tuple, recursively truncate each item
    if isinstance(val, list):
        for i in range(len(val)):
            val[i] = _truncate_value(val[i])
        return val
    if isinstance(val, tuple):
        return tuple(_truncate_value(x) for x in val) # type: ignore

    # Convert non-str values to json for consistent length measurement
    if isinstance(val, str):
        raw = val
    else:
        try:
            raw = json.dumps(val, ensure_ascii=False)
        except Exception:
            raw = str(val)

    if len(raw) <= VALUE_MAX_LEN:
        return val  # No truncation needed, preserve original type

    # Do a single truncation calculation
    removed = len(raw) - VALUE_MAX_LEN
    replacement = f"\n\n<< {removed} Characters hidden >>\n\n"
    truncated = truncate_text_by_ratio(raw, VALUE_MAX_LEN, replacement, ratio=0.3)
    return truncated


def _truncate_content(text: Optional[str], type: Type) -> str:

    max_len = CONTENT_MAX_LEN if type != "response" else RESPONSE_CONTENT_MAX_LEN

    if text is None:
        return ""
    raw = str(text)
    if len(raw) <= max_len:
        return raw

    # Same dynamic replacement logic as value truncation
    removed = len(raw) - max_len
    while True:
        replacement = f"\n\n<< {removed} Characters hidden >>\n\n"
        truncated = truncate_text_by_ratio(raw, max_len, replacement, ratio=0.3)
        new_removed = len(raw) - (len(truncated) - len(replacement))
        if new_removed == removed:
            break
        removed = new_removed
    return truncated





@dataclass
class LogItem:
    log: "Log"
    no: int
    type: Type
    heading: str = ""
    content: str = ""
    temp: bool = False
    update_progress: Optional[ProgressUpdate] = "persistent"
    kvps: Optional[OrderedDict] = None  # Use OrderedDict for kvps
    id: Optional[str] = None  # Add id field
    timestamp: float = 0.0
    guid: str = ""
    protected: bool = False
    completion: bool = False
    icon: str = ""
    summary: Optional[str] = None
    sender_type: str = ""
    sender_id: str = ""
    verbose: bool = False
    seq_id: int = 0  # Immutable sequence ID set at creation time

    # Class-level counter for generating unique, monotonically increasing seq_ids
    _seq_counter: int = 0

    def __post_init__(self):
        self.guid = self.log.guid
        # Preserve timestamp if already set (e.g. during deserialization)
        if not self.timestamp:
            self.timestamp = time.time()
        # Set seq_id at creation time using monotonically increasing counter
        # This ensures seq_id never changes even if 'no' is re-indexed during pruning
        LogItem._seq_counter += 1
        self.seq_id = (int(self.timestamp * 1000) * 1000) + LogItem._seq_counter

    def update(
        self,
        type: Optional[Type] = None,
        heading: Optional[str] = None,
        content: Optional[str] = None,
        kvps: Optional[dict] = None,
        temp: Optional[bool] = None,
        update_progress: Optional[ProgressUpdate] = None,
        protected: Optional[bool] = None,
        completion: Optional[bool] = None,
        icon: Optional[str] = None,
        summary: Optional[str] = None,
        sender_type: Optional[str] = None,
        sender_id: Optional[str] = None,
        verbose: Optional[bool] = None,
        **kwargs,
    ):
        if self.guid == self.log.guid:
            self.log._update_item(
                self.no,
                type=type,
                heading=heading,
                content=content,
                kvps=kvps,
                temp=temp,
                update_progress=update_progress,
                protected=protected,
                completion=completion,
                icon=icon,
                summary=summary,
                sender_type=sender_type,
                sender_id=sender_id,
                verbose=verbose,
                **kwargs,
            )

    def stream(
        self,
        heading: Optional[str] = None,
        content: Optional[str] = None,
        **kwargs,
    ):
        if heading is not None:
            self.update(heading=self.heading + heading)
        if content is not None:
            self.update(content=self.content + content)

        for k, v in kwargs.items():
            prev = self.kvps.get(k, "") if self.kvps else ""
            self.update(**{k: prev + v})

    def output(self, summary=False):
        content = self.content
        if summary and self.type == "tool":
            # If explicit summary exists, use it. Otherwise, fallback to truncation.
            if self.summary:
                content = self.summary
            else:
                # Summarize tool output: first 10 lines or first 500 chars
                lines = content.split('\n')
                if len(lines) > 10:
                    content = '\n'.join(lines[:10]) + "\n... (tool output summarized)"
                elif len(content) > 500:
                    content = content[:500] + "... (tool output summarized)"

        # Use the immutable seq_id set at creation time
        # This ensures correct ordering even after pruning when 'no' gets re-indexed
        
        # Ensure sequence_id is included in kvps for frontend cache ordering
        output_kvps = dict(self.kvps) if self.kvps else {}
        if "sequence_id" not in output_kvps:
            output_kvps["sequence_id"] = self.seq_id
            
        # Add a content-based hash for robust reconciliation if not provided
        if "hash" not in output_kvps:
            from python.helpers.hashing import content_hash
            hash_str = f"{self.type}|{self.heading}|{self.content}"
            # Use surrogateescape to handle potential surrogates in the string safely
            # or 'replace' to ensure it never crashes the API poll
            output_kvps["hash"] = content_hash(hash_str)

        return {
            "no": self.no,
            "seq_id": self.seq_id,
            "id": self.id,
            "type": self.type,
            "heading": self.heading,
            "content": content,
            "temp": self.temp,
            "kvps": output_kvps,
            "timestamp": self.timestamp,
            "protected": self.protected,
            "completion": self.completion,
            "icon": self.icon,
            "sender_type": self.sender_type,
            "sender_id": self.sender_id,
            "verbose": self.verbose,
            "is_summary": summary
        }


class Log:

    def __init__(self):
        self.context: Optional["AgentContext"] = None # set from outside
        self.guid: str = str(uuid.uuid4())
        self.updates: list[int] = []
        self.logs: list[LogItem] = []
        self.set_initial_progress()

    def log(
        self,
        type: Type,
        heading: str = "",
        content: str = "",
        temp: bool = False,
        update_progress: Optional[ProgressUpdate] = "persistent",
        kvps: Optional[dict] = None,
        protected: bool = False,
        completion: bool = False,
        icon: str = "",
        summary: Optional[str] = None,
        sender_type: str = "",
        sender_id: str = "",
        verbose: bool = False,
        id: Optional[str] = None,
        **kwargs,
    ) -> LogItem:
        # 1. Apply some basic validation/normalization
        heading = _truncate_heading(heading)
        content = _truncate_content(content, type)

        # 2. Add to logs
        item = LogItem(
            log=self,
            no=len(self.logs),
            type=type,
            heading=heading,
            content=content,
            temp=temp,
            update_progress=update_progress,
            kvps=OrderedDict(kvps) if kvps else None,
            timestamp=time.time(),
            protected=protected,
            completion=completion,
            icon=icon,
            summary=summary,
            sender_type=sender_type,
            sender_id=sender_id,
            verbose=verbose,
            id=id,
        )
        self.logs.append(item)
        self.updates.append(item.no)

        # 3. Update current progress
        self._update_progress_from_item(item)

        return item

    def _update_item(
        self,
        no: int,
        type: Optional[Type] = None,
        heading: Optional[str] = None,
        content: Optional[str] = None,
        kvps: Optional[dict] = None,
        temp: Optional[bool] = None,
        update_progress: Optional[ProgressUpdate] = None,
        id: Optional[str] = None,
        protected: Optional[bool] = None,
        completion: Optional[bool] = None,
        icon: Optional[str] = None,
        summary: Optional[str] = None,
        sender_type: Optional[str] = None,
        sender_id: Optional[str] = None,
        verbose: Optional[bool] = None,
        **kwargs,
    ):
        if no >= len(self.logs):
            return

        item = self.logs[no]
        if type:
            item.type = type
        if heading is not None:
            item.heading = _truncate_heading(heading)
        if content is not None:
            item.content = _truncate_content(content, item.type)
        if kvps is not None:
            item.kvps = OrderedDict(kvps)
        if temp is not None:
            item.temp = temp
        if update_progress is not None:
            item.update_progress = update_progress
        if protected is not None:
            item.protected = protected
        if completion is not None:
            item.completion = completion
        if icon is not None:
            item.icon = icon
        if summary is not None:
            item.summary = summary
        if sender_type is not None:
            item.sender_type = sender_type
        if sender_id is not None:
            item.sender_id = sender_id
        if verbose is not None:
            item.verbose = verbose
        if id is not None:
            item.id = id


        # adjust all content before processing
        if heading is not None:
            heading = self._mask_recursive(heading)
            heading = _truncate_heading(heading)
            item.heading = heading
        if content is not None:
            content = self._mask_recursive(content)
            content = _truncate_content(content, item.type)
            item.content = content
        if kvps is not None:
            kvps = OrderedDict(copy.deepcopy(kvps))
            kvps = self._mask_recursive(kvps)
            kvps = _truncate_value(kvps)
            item.kvps = kvps
        elif item.kvps is None:
            item.kvps = OrderedDict()
        if kwargs:
            kwargs = copy.deepcopy(kwargs)
            kwargs = self._mask_recursive(kwargs)
            item.kvps.update(kwargs)

        self.updates += [item.no]
        self._update_progress_from_item(item)

    def set_progress(self, progress: str, no: int = 0, active: bool = True):
        progress = self._mask_recursive(progress)
        progress = _truncate_progress(progress)
        self.progress = progress
        if not no:
            no = len(self.logs)
        self.progress_no = no
        self.progress_active = active

    def set_initial_progress(self):
        self.set_progress("Waiting for input", 0, False)

    def output(self, start=None, end=None, limit=None):
        if start is None:
            start = 0
        if end is None:
            end = len(self.updates)

        # 1. Identify Skeleton (protected, user, completions, and tool summaries)
        skeleton = {}
        for i, item in enumerate(self.logs):
            if item.protected or item.type == "user" or item.completion or item.type == "tool":
                # For skeleton tools, we use summary unless explicitly in history tail
                summary = (item.type == "tool" and not item.protected)
                skeleton[item.no] = item.output(summary=summary)

        # 2. Get Tail (latest updates)
        updates = self.updates[start:end]
        if limit:
            updates = updates[-limit:]

        out_map = skeleton.copy()
        for log_no in updates:
            if log_no < len(self.logs):
                item = self.logs[log_no]
                # Tail items get full output (no summary)
                out_map[log_no] = item.output(summary=False)

        # Sort by 'no' to ensure sequence
        sorted_nos = sorted(out_map.keys())
        return [out_map[no] for no in sorted_nos]

    def reset(self):
        self.guid = str(uuid.uuid4())
        self.updates = []
        self.logs = []
        self.set_initial_progress()

    def remove_item(self, id: str) -> bool:
        # try to find by id (guid) or index (no)
        for i, item in enumerate(self.logs):
            if str(item.id) == str(id) or str(item.no) == str(id):
                self.logs.pop(i)
                # Re-index remaining logs and clear updates to force refresh
                for j, remaining in enumerate(self.logs):
                    remaining.no = j
                self.updates = []
                # Bump GUID to force frontend full re-fetch (Issue #860)
                # Without this, the frontend retains the orphaned DOM node
                # from the removed log item, causing double-response display
                # when gate extensions (fidelity/grounding) retry the response.
                self.guid = str(uuid.uuid4())
                return True
        return False

    async def prune_logs(
        self, 
        keep_last: int = 100, 
        archive_path: str = None, 
        context_id: str = None,
        summarizer: Optional[Callable[[List["LogItem"]], Awaitable[str]]] = None
    ):
        """
        Prune logs to the last N items with intelligent context preservation and summarization.
        Prevents unbounded growth of persisted state while preserving critical history.
        Always preserves the first user message (prompt), any items marked as 'protected',
        and final AI responses (completions).
        
        Args:
            keep_last: Number of log entries to target for keeping
            archive_path: Optional path to archive pruned logs for lineage tracking
            context_id: Context ID for default archive path generation
            summarizer: Optional async function to summarize blocks of pruned logs
        """
        # Optimization: Only prune if we have a significant surplus (at least 10% over keep_last)
        # This prevents frequent GUID changes on every new message once limit is reached
        threshold = int(keep_last * 1.1)
        if len(self.logs) <= threshold:
            return
        
        # 0. Snapshot the logs to avoid index out of range if they change concurrently
        logs_snapshot = list(self.logs)
        
        # 1. Identify items to preserve
        # We always keep the first item, all user prompts,
        # and any item explicitly marked as 'protected' or 'completion'.
        must_keep_indices = set()
        for i, item in enumerate(logs_snapshot):
            if i == 0:
                must_keep_indices.add(i)
            elif item.type == "user" or getattr(item, "protected", False) or getattr(item, "completion", False):
                must_keep_indices.add(i)

        # 2. Identify the range for "keep_last"
        # We want to keep the last 'keep_last' items. 
        last_n_start_idx = max(0, len(logs_snapshot) - keep_last)
        last_n_indices = set(range(last_n_start_idx, len(logs_snapshot)))
        
        # Combined indices to keep
        all_keep_indices = sorted(list(must_keep_indices | last_n_indices))
        all_keep_set = set(all_keep_indices)

        # 3. Identify blocks to summarize
        # Items NOT in all_keep_set are candidates for pruning/summarization
        prune_indices = [i for i in range(len(logs_snapshot)) if i not in all_keep_set]
        
        new_logs = []
        keep_ptr = 0
        prune_ptr = 0
        
        while keep_ptr < len(all_keep_indices) or prune_ptr < len(prune_indices):
            # If next item is a 'keep' item
            if keep_ptr < len(all_keep_indices) and (prune_ptr >= len(prune_indices) or all_keep_indices[keep_ptr] < prune_indices[prune_ptr]):
                new_logs.append(logs_snapshot[all_keep_indices[keep_ptr]])
                keep_ptr += 1
            else:
                # Start of a prune block
                block = []
                while prune_ptr < len(prune_indices) and (keep_ptr >= len(all_keep_indices) or prune_indices[prune_ptr] < all_keep_indices[keep_ptr]):
                    block.append(logs_snapshot[prune_indices[prune_ptr]])
                    prune_ptr += 1
                
                if not block:
                    continue

                # Summarize if requested and block is significant
                if summarizer and len(block) >= 3:
                    try:
                        summary_text = await summarizer(block)
                        summary_item = LogItem(
                            log=self,
                            no=0, # will be reset
                            type="info",
                            heading="📊 Condensed History",
                            content=summary_text,
                            protected=True # Ensure summary itself is not pruned soon
                        )
                        new_logs.append(summary_item)
                    except Exception as e:
                        # Fallback to a simple placeholder if summarization fails
                        import logging
                        logging.getLogger("agix.log").warning(f"Summarization failed in prune_logs: {e}")
                
                # Archive the block
                if archive_path or context_id:
                    self._archive_logs_to_lineage(block, archive_path, context_id)

        # 4. Filter logs and reset indices
        for i, item in enumerate(new_logs):
            item.no = i
            
        self.logs = new_logs
        
        # Change GUID to force UI to clear and reload everything
        self.guid = str(uuid.uuid4())
        self.updates = list(range(len(self.logs)))
        
        # Reset progress to current tail
        self.progress_no = len(self.logs) - 1

    def _archive_logs_to_lineage(self, logs_to_archive: list["LogItem"], archive_path: str = None, context_id: str = None):
        """
        Archive pruned log entries to a lineage file for full history tracking.
        
        Args:
            logs_to_archive: List of LogItem objects to archive
            archive_path: Explicit path to archive file
            context_id: Context ID for default archive path generation
        """
        try:
            from python.helpers.files import get_abs_path, make_dirs
            
            # Determine archive path
            if not archive_path and context_id:
                archive_path = get_abs_path("tmp", "chats", context_id, "log_lineage.jsonl")
            
            if not archive_path:
                return  # No archive path available
            
            # Ensure directory exists
            make_dirs(archive_path)
            
            # Convert logs to archive format
            archive_entries = []
            archive_timestamp = datetime.now(timezone.utc).isoformat()
            
            for log_item in logs_to_archive:
                entry = {
                    "archived_at": archive_timestamp,
                    "guid": log_item.guid,
                    "no": log_item.no,
                    "type": log_item.type,
                    "heading": log_item.heading,
                    "content": log_item.content[:5000] if log_item.content else "",  # Keep more content for lineage (Issue #274)
                    "timestamp": log_item.timestamp,
                    "temp": log_item.temp,
                }
                archive_entries.append(json.dumps(entry, ensure_ascii=False))
            
            # Append to lineage file (JSONL format for efficient append)
            # Use standard open(..., 'a') for efficient append without reading entire file
            with open(archive_path, 'a', encoding='utf-8', errors='replace') as f:
                f.write("\n".join(archive_entries) + "\n")
            
            # Check size for rotation (less frequent check)
            if random.random() < 0.1: # 10% chance to check size
                file_size = os.path.getsize(archive_path)
                if file_size > 5 * 1024 * 1024: # > 5MB
                    # Rotate: keep only last 1MB
                    with open(archive_path, 'r', encoding='utf-8') as f:
                        f.seek(max(0, file_size - 1024 * 1024))
                        content = f.read()
                        # Find first newline to avoid partial line
                        first_newline = content.find("\n")
                        if first_newline != -1:
                            content = content[first_newline+1:]
                    
                    from python.helpers.files import write_file_atomic
                    write_file_atomic(archive_path, content)
            
        except Exception as e:
            # Don't fail the main operation if archiving fails
            import logging
            logging.getLogger("agix.log").warning(f"Failed to archive logs to lineage: {e}")

    def _update_progress_from_item(self, item: LogItem):
        if item.heading and item.update_progress != "none":
            if item.no >= self.progress_no:
                self.set_progress(
                    item.heading,
                    (item.no if item.update_progress == "persistent" else -1),
                )

    def _mask_recursive(self, obj: T) -> T:
        """Recursively mask secrets in nested objects."""
        try:
            from python.agent import AgentContext
            secrets_mgr = get_secrets_manager(self.context or AgentContext.current())

            # debug helper to identify context mismatch
            # self_id = self.context.id if self.context else None
            # current_ctx = AgentContext.current()
            # current_id = current_ctx.id if current_ctx else None
            # if self_id != current_id:
            #     print(f"Context ID mismatch: {self_id} != {current_id}")

            if isinstance(obj, str):
                return secrets_mgr.mask_values(obj)
            elif isinstance(obj, dict):
                return {k: self._mask_recursive(v) for k, v in obj.items()}  # type: ignore
            elif isinstance(obj, list):
                return [self._mask_recursive(item) for item in obj]  # type: ignore
            else:
                return obj
        except Exception as _e:
            # If masking fails, return original object
            return obj
