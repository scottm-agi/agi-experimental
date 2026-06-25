from __future__ import annotations
"""
Tool Output Fidelity Extension — Data Anchor + Response Injection

After MCP tool execution:
1. Extracts a data fingerprint from the result
2. APPENDS a fidelity anchor to the tool response message itself, making 
   real values hyper-salient and harder for the model to hallucinate over

Addresses: Agent hallucination of MCP tool output (e.g., fabricating Google Chat
space names/IDs instead of using real values from the tool result).
"""

import json
from python.helpers.hashing import dedup_hash_short
import logging
import re
from typing import Any, Dict, List, Optional

from python.helpers.extension import Extension
from python.helpers.tool import Response

logger = logging.getLogger("agix.tool_output_fidelity")


class ToolOutputFidelity(Extension):
    """Extract data fingerprints and inject fidelity anchors into MCP tool results."""

    # MCP tool prefixes that indicate external data sources
    # google-chat re-enabled: Issue #789 fixed the infinite loop by
    # gating the fidelity CHECK (message_loop_end) on the response tool,
    # so anchors are collected during intermediate calls but only
    # validated when the agent renders its final answer.
    MCP_PREFIXES = [
        "forgejo.", "github.",
        "perplexity",
        "google-chat", "google_chat",
    ]

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        """After tool execution, extract data anchor and inject into response."""
        if not tool_name or response is None:
            return

        # Only anchor MCP tool results (external data sources)
        if not self._is_mcp_tool(tool_name):
            return

        result_text = ""
        if hasattr(response, "message") and response.message:
            result_text = response.message
        else:
            return  # No message to anchor

        if not result_text or len(result_text) < 20:
            return

        try:
            anchor = self._extract_anchor(tool_name, result_text)
            if anchor and anchor.get("key_values"):
                # Store for verification
                self._store_anchor(tool_name, anchor)
                
                # INJECT anchor directly into the response message
                anchor_text = self._build_anchor_text(anchor)
                if anchor_text and hasattr(response, "message"):
                    response.message = response.message + anchor_text
                
                logger.info(
                    f"[FIDELITY] Anchored {tool_name}: {anchor.get('summary', 'N/A')}"
                )
        except Exception as e:
            logger.debug(f"[FIDELITY] Anchor extraction failed for {tool_name}: {e}")

    def _is_mcp_tool(self, tool_name: str) -> bool:
        """Check if this is an MCP tool that returns external data."""
        tool_lower = tool_name.lower()
        return any(tool_lower.startswith(p) for p in self.MCP_PREFIXES)

    def _extract_anchor(self, tool_name: str, result_text: str) -> Optional[Dict]:
        """Extract a compact data fingerprint from the tool result."""
        content_hash = dedup_hash_short(result_text[:2048])
        anchor: Dict[str, Any] = {
            "tool_name": tool_name,
            "hash": content_hash,
            "anchor_id": content_hash[:8],  # Short ID for {{verbatim:ID}} syntax
        }

        key_values: List[str] = []
        item_count = 0

        # Strategy 1: Try JSON parsing
        try:
            data = json.loads(result_text)
            key_values, item_count = self._extract_from_json(data)
        except (json.JSONDecodeError, TypeError):
            pass

        # Strategy 2: If JSON failed, try regex extraction for common patterns
        if not key_values:
            key_values, item_count = self._extract_from_text(result_text)

        anchor["key_values"] = key_values[:8]
        anchor["item_count"] = item_count

        if key_values:
            vals = ", ".join(f"'{v}'" for v in key_values[:3])
            anchor["summary"] = f"{item_count} items. Values: {vals}"
        else:
            anchor["summary"] = f"{item_count} items, {len(result_text)} chars"

        return anchor

    def _extract_from_json(self, data: Any) -> tuple[List[str], int]:
        """Extract key values from parsed JSON data."""
        key_values: List[str] = []
        item_count = 0

        if isinstance(data, list):
            item_count = len(data)
            for item in data[:8]:
                if isinstance(item, dict):
                    for field in ["displayName", "name", "title", "id", "space", "sender"]:
                        if field in item:
                            val = str(item[field])
                            if val and val not in key_values and len(val) < 200:
                                key_values.append(val)
                            break
                elif isinstance(item, str):
                    key_values.append(item[:100])
        elif isinstance(data, dict):
            item_count = 1
            # Check for nested lists
            for k, v in data.items():
                if isinstance(v, list):
                    nested_vals, nested_count = self._extract_from_json(v)
                    key_values.extend(nested_vals)
                    item_count = nested_count
                    break
            if not key_values:
                for k, v in list(data.items())[:5]:
                    if isinstance(v, str) and len(v) < 200:
                        key_values.append(f"{k}={v}")

        return key_values, item_count

    def _extract_from_text(self, text: str) -> tuple[List[str], int]:
        """Extract key values from text using regex patterns."""
        key_values: List[str] = []
        item_count = 0

        # Pattern 1: Google Chat message format:
        # - [date] Author: message text (ID: spaces/xxx/messages/yyy)
        gchat_pattern = re.compile(
            r'\[([\d-]+\s+[\d:]+)\]\s+([^:]+):\s+(.+?)\s*\(ID:\s*(spaces/[^)]+)\)',
            re.MULTILINE
        )
        gchat_matches = gchat_pattern.findall(text)
        if gchat_matches:
            for timestamp, author, snippet, msg_id in gchat_matches[:8]:
                author = author.strip()
                snippet = snippet.strip()[:100]
                # Store: author name, message snippet, and message ID as anchors
                if author and author not in key_values:
                    key_values.append(author)
                if snippet and len(snippet) > 3 and snippet not in key_values:
                    key_values.append(snippet)
                if msg_id and msg_id not in key_values:
                    key_values.append(msg_id)
            item_count = len(gchat_matches)
            return key_values[:8], item_count

        # Pattern 2: "displayName": "Something" or 'name': 'Something'
        name_pattern = re.compile(
            r'["\'](?:displayName|name|title)["\']:\s*["\']([^"\']+)["\']',
            re.IGNORECASE
        )
        matches = name_pattern.findall(text)
        for m in matches[:8]:
            val = m.strip()
            if val and val not in key_values and len(val) < 200:
                key_values.append(val)
        
        item_count = len(matches) if matches else 0

        # Pattern 3: ID pattern: "spaces/XXXX"
        if not key_values:
            id_pattern = re.compile(r'spaces/([A-Za-z0-9_-]+)')
            id_matches = id_pattern.findall(text)
            for m in id_matches[:5]:
                key_values.append(f"spaces/{m}")
            item_count = len(id_matches)

        return key_values, item_count

    def _build_anchor_text(self, anchor: Dict) -> str:
        """Build a concise fidelity anchor to append to the tool result.
        
        This text is injected directly into the tool result message seen by the model.
        It highlights real values and provides {{verbatim:ID}} syntax for
        zero-fabrication data inclusion in responses.
        """
        key_values = anchor.get("key_values", [])
        if not key_values:
            return ""

        anchor_id = anchor.get("anchor_id", "unknown")
        item_count = anchor.get("item_count", 0)
        vals = "\n".join(f"  - {v}" for v in key_values)
        
        return (
            f"\n\n--- DATA VERIFICATION ANCHOR ---\n"
            f"ANCHOR_ID: {anchor_id}\n"
            f"Total items: {item_count}\n"
            f"REAL values from this result:\n"
            f"{vals}\n"
            f"\n"
            f"⚡ USE THIS IN YOUR RESPONSE: {{{{verbatim:{anchor_id}}}}}\n"
            f"When you need to include this data in your response, write "
            f"{{{{verbatim:{anchor_id}}}}} and the system will automatically "
            f"inject the real values. This prevents copy-paste errors.\n"
            f"--- END ANCHOR ---"
        )

    def _store_anchor(self, tool_name: str, anchor: Dict):
        """Store anchor on agent.data for verification."""
        if "tool_data_anchors" not in self.agent.data:
            self.agent.data["tool_data_anchors"] = []

        self.agent.data["tool_data_anchors"].append(anchor)
        self.agent.data["tool_data_anchors"] = self.agent.data["tool_data_anchors"][-10:]
