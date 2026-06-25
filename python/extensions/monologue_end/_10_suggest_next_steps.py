from __future__ import annotations
"""
Post-Message Next Step Suggestion Tiles
========================================
Extension hook (monologue_end) that generates 1-3 contextual
"next step" suggestion tiles after each agent response.

Uses lightweight pattern matching on the final response text
to generate relevant follow-up suggestions. Pushes hints to
the frontend via output_data["hints"], which the polling system
delivers to setDynamicHints() in input-store.js.

Issue: #775
"""

import re
import logging
from typing import List, Dict

from python.helpers.extension import Extension

logger = logging.getLogger("agix.suggest_next_steps")

MAX_SUGGESTIONS = 3

# Pattern → suggestions mapping
# Each pattern is (regex, list of suggestion dicts)
SUGGESTION_PATTERNS = [
    # Code creation / file creation
    (
        re.compile(r"(created|wrote|generated|saved)\s+(the\s+)?(file|script|module|class|function)", re.I),
        [
            {"text": "Run the tests", "label": "🧪 Test", "icon": "test"},
            {"text": "Review the file", "label": "👀 Review", "icon": "review"},
            {"text": "Deploy it", "label": "🚀 Deploy", "icon": "deploy"},
        ]
    ),
    # Bug fix
    (
        re.compile(r"(fixed|resolved|patched|corrected)\s+(the\s+)?(bug|issue|error|problem)", re.I),
        [
            {"text": "Run the tests to verify", "label": "🧪 Verify", "icon": "test"},
            {"text": "Show me the diff", "label": "📝 Diff", "icon": "diff"},
            {"text": "Check for similar issues", "label": "🔍 Audit", "icon": "search"},
        ]
    ),
    # Explanation / question answer
    (
        re.compile(r"(the\s+difference|this\s+means|in\s+summary|to\s+summarize|basically|in\s+short)", re.I),
        [
            {"text": "Show me an example", "label": "💡 Example", "icon": "example"},
            {"text": "Tell me more", "label": "📚 More", "icon": "learn"},
            {"text": "Move on to next task", "label": "➡️ Next", "icon": "next"},
        ]
    ),
    # Error / debugging
    (
        re.compile(r"(error|exception|traceback|stack\s*trace|failed|failure)", re.I),
        [
            {"text": "Show the full error", "label": "🔍 Details", "icon": "details"},
            {"text": "Try to fix it", "label": "🔧 Fix", "icon": "fix"},
            {"text": "Search for solutions", "label": "🌐 Search", "icon": "search"},
        ]
    ),
    # List / options presented
    (
        re.compile(r"(here\s+are|options?\s+(?:are|include)|you\s+(?:can|could)|available\s+(?:options|choices))", re.I),
        [
            {"text": "Go with option 1", "label": "1️⃣ First", "icon": "select"},
            {"text": "Tell me more about each option", "label": "📋 Compare", "icon": "compare"},
            {"text": "Suggest the best option", "label": "⭐ Best", "icon": "recommend"},
        ]
    ),
    # Installation / setup
    (
        re.compile(r"(installed|set\s*up|configured|initialized|bootstrapped)", re.I),
        [
            {"text": "Verify the installation", "label": "✅ Verify", "icon": "verify"},
            {"text": "Show the configuration", "label": "⚙️ Config", "icon": "config"},
            {"text": "What's next?", "label": "➡️ Next", "icon": "next"},
        ]
    ),
    # Commit / git operations
    (
        re.compile(r"(committed|pushed|merged|pull\s*request|branch)", re.I),
        [
            {"text": "Show the git log", "label": "📜 Log", "icon": "log"},
            {"text": "Create a pull request", "label": "🔀 PR", "icon": "pr"},
            {"text": "Continue to next task", "label": "➡️ Next", "icon": "next"},
        ]
    ),
]

# Default suggestions when no pattern matches
DEFAULT_SUGGESTIONS = [
    {"text": "Tell me more", "label": "📚 More", "icon": "learn"},
    {"text": "What should we do next?", "label": "➡️ Next", "icon": "next"},
]


def generate_suggestions(response_text: str) -> List[Dict[str, str]]:
    """
    Generate 1-3 contextual suggestions based on the agent's response.
    
    Uses pattern matching on the response text to determine relevant
    follow-up actions. Falls back to default suggestions if no patterns match.
    
    Args:
        response_text: The agent's final response text.
        
    Returns:
        List of suggestion dicts with 'text', 'label', and 'icon' keys.
    """
    if not response_text or not response_text.strip():
        return DEFAULT_SUGGESTIONS[:MAX_SUGGESTIONS]
    
    matched_suggestions = []
    
    for pattern, suggestions in SUGGESTION_PATTERNS:
        if pattern.search(response_text):
            matched_suggestions.extend(suggestions)
            # Stop after first match to keep suggestions focused
            break
    
    if not matched_suggestions:
        matched_suggestions = DEFAULT_SUGGESTIONS.copy()
    
    # Deduplicate by text
    seen = set()
    unique = []
    for s in matched_suggestions:
        if s["text"] not in seen:
            seen.add(s["text"])
            unique.append(s)
    
    return unique[:MAX_SUGGESTIONS]


class SuggestNextSteps(Extension):
    """
    Post-message extension that generates contextual next-step
    suggestion tiles after each agent response.
    """

    async def execute(self, loop_data=None, **kwargs):
        """
        Generate and push suggestion hints after monologue ends.
        
        Reads the agent's final response, generates contextual suggestions,
        and pushes them to the frontend via output_data["hints"].
        """
        try:
            # Get the final response text
            final_response = None
            if loop_data and hasattr(loop_data, 'last_response'):
                final_response = str(loop_data.last_response) if loop_data.last_response else None
            
            if not final_response:
                # Try to get from agent's last message
                if hasattr(self.agent, 'hist_get_last') and callable(self.agent.hist_get_last):
                    last = self.agent.hist_get_last()
                    if last:
                        final_response = str(last)
            
            # Generate suggestions
            suggestions = generate_suggestions(final_response or "")
            
            # Push to frontend via output_data
            context = self.agent.context
            if context:
                context.set_output_data("hints", suggestions)
                logger.debug(
                    f"Pushed {len(suggestions)} suggestion(s) to context {context.id}"
                )
        except Exception as e:
            logger.warning(f"Failed to generate next-step suggestions: {e}")
