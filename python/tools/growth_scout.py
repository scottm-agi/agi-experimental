from __future__ import annotations
import json
from typing import Dict, Any, Optional
from python.helpers.tool import Tool, Response

class GrowthScout(Tool):
    async def execute(self, **kwargs) -> Response:
        query = self.args.get("query")
        if not query:
            return Response(message="Error: Missing 'query' argument.", break_loop=False)

        try:
            # Use the existing search_engine tool logic
            # Since we are inside a tool, we can't easily call another tool via self.agent.execute_tool
            # without triggering recursion issues or misaligned state.
            # However, we can use the subordinate call or simply leverage the search_engine logic.
            
            # For now, let's suggest the user use search_engine combined with an agent call.
            # OR we can implement the logic directly if we have access to the search helper.
            
            from python.helpers.duckduckgo_search import search
            
            # Enhance query for growth hacks
            enhanced_query = f"growth hacks marketing strategies SMB reddit blogs {query}"
            search_results = search(enhanced_query, num_results=5)
            
            if not search_results:
                return Response(message="No growth hacks found for your query.", break_loop=False)

            # Format results
            summary = f"Found trending growth hacks for: {query}\n\n"
            for i, result in enumerate(search_results, 1):
                summary += f"{i}. **{result.get('title')}**\n"
                summary += f"   Source: {result.get('href')}\n"
                summary += f"   {result.get('body')}\n\n"
            
            summary += "\nUse this information to develop a marketing implementation plan."
            
            return Response(message=summary, break_loop=False)

        except Exception as e:
            return Response(message=f"Error scouting growth hacks: {str(e)}", break_loop=False)
