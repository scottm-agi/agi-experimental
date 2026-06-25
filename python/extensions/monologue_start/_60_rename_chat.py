"""
Chat naming extension - Automatically generates meaningful chat titles.

This extension runs at monologue_start to generate/update chat names based on
conversation content. It uses the utility model with prompts to create short,
descriptive titles (1-3 words).

Architecture note: This was originally designed as an extension, not embedded
in agent.py. It was accidentally deleted in commit fae42b75 and was being
incorrectly reimplemented in agent.py.
"""
from python.helpers import persist_chat, tokens
from python.helpers.extension import Extension
from python.agent import LoopData
import asyncio
import sys


class RenameChat(Extension):
    """
    Extension that generates meaningful chat names based on conversation content.
    
    Triggers: monologue_start
    Frequency: Every monologue (but skips if name is already meaningful)
    """

    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        """Execute the chat rename in background to not block main flow."""
        # Skip if context has no agent0 or no history yet
        if not self.agent or not self.agent.context:
            return
        
        # Only rename if current name is generic or missing
        current_name = self.agent.context.name
        skip_names = {"New Chat", "Chat Naming Assistant", "None", None, "", "with.", "with", "Chat Session"}
        is_bad_name = (
            current_name and 
            (len(current_name) < 5 or 
             current_name.strip('.').strip() == "" or
             current_name.lower() in {"with", "hello", "hi", "hey", "test"} or
             # RCA-270: Frontend generates "Chat #N" when name is null
             current_name.startswith("Chat #"))
        )
        
        if current_name and current_name not in skip_names and not is_bad_name:
            return  # Already has a meaningful name
        
        # Run rename in background
        asyncio.create_task(self._change_name())

    async def _change_name(self):
        """Generate and apply a new chat name using the utility model."""
        try:
            # Get conversation history
            if not self.agent.history:
                print(f"[RENAME_CHAT] No history available, skipping", file=sys.stderr)
                return
                
            history_text = self.agent.history.output_text()
            print(f"[RENAME_CHAT] History text length: {len(history_text) if history_text else 0}", file=sys.stderr)
            
            if not history_text or len(history_text) < 10:
                print(f"[RENAME_CHAT] History too short, skipping", file=sys.stderr)
                return
            
            # Trim history to reasonable size for utility model
            try:
                ctx_length = 5000
                if self.agent.config.utility_model and hasattr(self.agent.config.utility_model, 'ctx_length'):
                    ctx_length = min(int(self.agent.config.utility_model.ctx_length * 0.7), 5000)
            except (ValueError, TypeError, AttributeError):
                ctx_length = 5000
            
            history_text = tokens.trim_to_tokens(history_text, ctx_length, "start")
            
            # Prepare prompts
            system = self.agent.read_prompt("fw.rename_chat.sys.md")
            current_name = self.agent.context.name or "New Chat"
            message = self.agent.read_prompt(
                "fw.rename_chat.msg.md", 
                current_name=current_name, 
                history=history_text
            )
            
            # Call utility model
            print(f"[RENAME_CHAT] Calling utility model...", file=sys.stderr)
            new_name = await self.agent.call_utility_model(
                system=system, 
                message=message, 
                background=True
            )
            
            print(f"[RENAME_CHAT] Raw model response: '{new_name}'", file=sys.stderr)
            
            # Validate and apply new name
            valid_name = False
            if new_name:
                new_name = new_name.strip()
                # Remove common LLM artifacts
                for prefix in ["Chat name:", "Name:", "Title:", "**", "##", '"', "'"]:
                    if new_name.lower().startswith(prefix.lower()):
                        new_name = new_name[len(prefix):].strip()
                for suffix in ["**", '"', "'", ".", ":"]:
                    if new_name.endswith(suffix):
                        new_name = new_name[:-len(suffix)].strip()
                
                # Trim to max length
                if len(new_name) > 40:
                    new_name = new_name[:40] + "..."
                
                # Expanded generic title list
                generic_titles = {
                    "new chat", "chat", "none", "untitled", "untitled chat",
                    "untitled chat log", "chat naming assistant", "chat log",
                    "conversation", "assistant", "ai", "bot", "help", "agix", "agix",
                    "new project chat", "new task"
                }
                
                # Only apply if result is meaningful
                if (new_name and 
                    len(new_name) >= 3 and 
                    new_name.lower() not in generic_titles and
                    not new_name.lower().startswith("untitled") and
                    not new_name.lower().startswith("chat naming")):
                    valid_name = True
                
            if valid_name:
                old_name = self.agent.context.name
                self.agent.context.name = new_name
                await self._persist_name(old_name, new_name, "Model")
            else:
                print(f"[RENAME_CHAT] Rejected generic title: '{new_name}', using keyword fallback", file=sys.stderr)
                fallback_title = self._extract_keywords_fallback()
                if fallback_title:
                    old_name = self.agent.context.name
                    self.agent.context.name = fallback_title
                    await self._persist_name(old_name, fallback_title, "Fallback")
                    
        except Exception as e:
            print(f"[RENAME_CHAT] Error: {e}", file=sys.stderr)

    async def _persist_name(self, old_name: str, new_name: str, source: str):
        """Persist the new name to disk and database."""
        try:
            # RCA-280 FIX: Update last_message timestamp so UI polling detects the name change
            from datetime import datetime, timezone
            try:
                self.agent.context.last_message = datetime.now(timezone.utc)
            except Exception:
                pass
            
            # 1. Save to JSON context file
            persist_chat.save_tmp_chat(self.agent.context)
            
            # 2. Save to SQL database
            from python.helpers.persistence_manager import PersistenceManager
            from python.helpers.persist_chat import _serialize_context
            pm = PersistenceManager.get_instance()
            await pm.save_context_sql(_serialize_context(self.agent.context))
            print(f"[RENAME_CHAT] {source} SUCCESS: '{old_name}' -> '{new_name}'", file=sys.stderr)
        except Exception as e:
            print(f"[RENAME_CHAT] Persistence failed for {source} name: {e}", file=sys.stderr)

    def _extract_keywords_fallback(self) -> str:
        """Extract meaningful keywords from last user message for fallback title."""
        try:
            # Check context project
            project_prefix = ""
            project_name = self.agent.context.get_data("project_name") or ""
            if project_name:
                project_prefix = f"[{project_name}] "

            user_content = ""
            if hasattr(self.agent, 'last_raw_user_message') and self.agent.last_raw_user_message:
                user_content = str(self.agent.last_raw_user_message)
            else:
                for msg in self.agent.history.output():
                    if not msg.get("ai") and msg.get("content"):
                        user_content = str(msg["content"])
                        break
            
            if not user_content:
                return project_name if project_name else ""
            
            skip_words = {
                "i", "a", "the", "an", "is", "are", "was", "were", "can", "you", "please", "help", 
                "me", "with", "my", "to", "do", "how", "what", "why", "when", "where", "context", 
                "continue", "conversation", "tell", "about", "hello", "hi", "hey", "test", "testing",
                "chat", "naming", "feature", "story", "would", "could", "should", "have", "has",
                "this", "that", "these", "those", "just", "like", "for", "and", "but", "or", "so",
                "if", "of", "on", "in", "at", "by", "from", "as", "be", "been", "being", "will",
                "project", "task", "session", "log", "new", "using", "use", "using", "setup"
            }
            
            words = user_content.split()
            title_words = []
            for w in words:
                clean_w = w.strip(".,!?;:'\"()-#[]{}@*")
                if len(clean_w) >= 3 and clean_w.lower() not in skip_words:
                    if clean_w[0].isupper() and clean_w.lower() not in ["hello", "hi", "hey", "please"]:
                        title_words.insert(0, clean_w)
                    else:
                        title_words.append(clean_w)
                if len(title_words) >= 4:
                    break
            
            if title_words:
                suggestion = " ".join(title_words[:4])
                # Ensure it's not too long with prefix
                final_name = f"{project_prefix}{suggestion}"
                return final_name[:40]
            
            return project_name if project_name else ""
        except Exception as e:
            print(f"[RENAME_CHAT] Keyword fallback error: {e}", file=sys.stderr)
            return ""
