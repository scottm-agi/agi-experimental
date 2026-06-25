"""
Integration Output Filter — Monologue End Extension

Applies deterministic content filtering to agent responses when the
context has source_type="integration". This is the OUTPUT-side counterpart
to the INPUT-side guardrails in _05_guardrails.py.

Runs after the agent completes its monologue, scanning the final response
for sensitive content and redacting it before returning to the API caller.
"""
import logging
from python.helpers.extension import Extension

logger = logging.getLogger("guardrails.output_filter")


class IntegrationOutputFilter(Extension):
    """
    Filters agent output for integration contexts to prevent
    leakage of sensitive system information, secrets, and paths.
    """

    async def execute(self, **kwargs):
        loop_data = kwargs.get("loop_data")
        if not loop_data:
            return

        # Determine if filtering is needed
        context = self.agent.context
        should_filter = False

        # Always filter in production mode
        from python.helpers import feature_flags
        if feature_flags.is_production_env():
            should_filter = True

        # Also filter for integration/external contexts
        if context:
            source_type = context.get_data("source_type")
            if source_type in ("integration", "webhook", "a2a", "mcp", "repository"):
                should_filter = True

        if not should_filter:
            return

        # Get the final response from loop_data
        # The response is stored in loop_data after the agent's monologue completes
        response = getattr(loop_data, 'result', None)
        if not response or not isinstance(response, str):
            return

        # Apply content filter
        try:
            from python.helpers.content_filter import ContentFilter
            result = ContentFilter.scan(response)
            
            if result.has_violations:
                logger.warning(
                    f"[OUTPUT_FILTER] {len(result.violations)} violations found in response "
                    f"for context {context.id} (source_type={source_type})"
                )
                
                # Log violations for audit trail
                for v in result.violations:
                    logger.info(f"  [{v.severity}] {v.rule_name}: {v.description}")
                    
                    # Also log to context for UI visibility
                    self.agent.log(
                        type="info",
                        heading=f"🛡️ Content Filter: {v.rule_name}",
                        content=f"[{v.severity.upper()}] {v.description}",
                    )
                
                if result.blocked:
                    # Full block: replace response entirely
                    logger.error(
                        f"[OUTPUT_FILTER] Response BLOCKED for context {context.id}. "
                        f"Too many critical violations."
                    )
                    loop_data.result = (
                        "I apologize, but I cannot provide this response due to security policies. "
                        "The response contained content that violates safety guidelines. "
                        "Please rephrase your request."
                    )
                    self.agent.log(
                        type="warning",
                        heading="🛡️ Response Blocked",
                        content="Response was blocked by content filter due to multiple critical violations.",
                    )
                else:
                    # Partial redaction: use the filtered text
                    loop_data.result = result.filtered
                    
        except Exception as e:
            # Filter failure should NOT block responses — log and continue
            logger.error(f"[OUTPUT_FILTER] Content filter error: {e}")
