from __future__ import annotations
from python.helpers.tool import Tool

class Examine(Tool):
    async def execute(self, **kwargs):
        source = self.args.get("source")
        rationale = self.args.get("rationale")
        content_summary = self.args.get("content_summary", "")

        # Store the grounding point in the agent's context data for potential footer generation
        grounding_points = self.agent.context.get_data("grounding_points") or []
        point = {
            "source": source,
            "rationale": rationale,
            "summary": content_summary
        }
        grounding_points.append(point)
        self.agent.context.set_data("grounding_points", grounding_points)

        # Log identifying that this source has been vetted
        msg = f"Vetted Source: {source}\nRationale: {rationale}"
        if content_summary:
            msg += f"\nSummary: {content_summary}"
        
        self.log = self.agent.context.log.log(
            type="info",
            heading=f"{self.agent.agent_name}: Examining Source",
            content=msg
        )

        return f"Source '{source}' has been successfully examined and cataloged for grounding."
