from __future__ import annotations
import os
import asyncio
import logging
import requests
from python.helpers import dotenv_manager as dotenv, memory, duckduckgo_search
from python.helpers.perplexity_search import perplexity_search
from python.helpers.tool import Tool, Response
from python.helpers.print_style import PrintStyle
from python.helpers.errors import handle_error
from python.helpers.searxng import search as searxng
from python.tools.aggregator_fidelity import flag_aggregator_hallucination

SEARCH_ENGINE_RESULTS = 10
logger = logging.getLogger("search-engine")



class SearchEngine(Tool):
    """
    Performs web searches using multiple providers for resilient search.
    Fallback chain: Perplexity → Tavily → SearxNG → DuckDuckGo → Crawl4AI.
    """
    async def execute(self, query="", **kwargs):
        errors_collected = []

        # TIER 1: Try Perplexity first for high-quality research
        try:
            perplexity_result = await asyncio.to_thread(perplexity_search, query)
            if perplexity_result and len(perplexity_result.strip()) > 50:
                # Check for aggregator hallucination before returning
                perplexity_result = flag_aggregator_hallucination(perplexity_result)
                return Response(message=f"Perplexity Search Results:\n{perplexity_result}", break_loop=False)
            else:
                errors_collected.append("Perplexity returned empty/insufficient results")
        except Exception as e:
            errors_collected.append(f"Perplexity: {e}")
            logger.warning(f"Perplexity search failed: {e}, falling back to Tavily")

        # TIER 2: Fallback to Tavily (URL-rich results for scrape_url follow-up)
        try:
            tavily_result = await self.tavily_search(query)
            if tavily_result and len(tavily_result.strip()) > 50:
                return Response(message=f"Tavily Search Results:\n{tavily_result}", break_loop=False)
            else:
                errors_collected.append(f"Tavily: {tavily_result[:200] if tavily_result else 'empty/no API key'}")
        except Exception as e:
            errors_collected.append(f"Tavily: {e}")
            logger.warning(f"Tavily search failed: {e}, falling back to SearxNG")

        # TIER 3: Fallback to SearxNG
        try:
            searxng_result = await self.searxng_search(query)
            if searxng_result and "Error" not in searxng_result and len(searxng_result.strip()) > 20:
                await self.agent.handle_intervention(searxng_result)
                return Response(message=searxng_result, break_loop=False)
            else:
                errors_collected.append(f"SearxNG: {searxng_result[:200] if searxng_result else 'empty result'}")
        except Exception as e:
            errors_collected.append(f"SearxNG: {e}")
            logger.warning(f"SearxNG search failed: {e}, falling back to DuckDuckGo")

        # TIER 4: Fallback to DuckDuckGo (free, no API key required)
        try:
            ddg_result = await self.duckduckgo_search(query)
            if ddg_result and len(ddg_result.strip()) > 20:
                return Response(message=ddg_result, break_loop=False)
            else:
                errors_collected.append("DuckDuckGo returned empty results")
        except Exception as e:
            errors_collected.append(f"DuckDuckGo: {e}")
            logger.error(f"DuckDuckGo search also failed: {e}")

        # TIER 5: Fallback to Firecrawl (search + scrape in one call)
        try:
            firecrawl_result = await self.firecrawl_search(query)
            if firecrawl_result and len(firecrawl_result.strip()) > 50:
                return Response(message=f"Firecrawl Search Results:\n{firecrawl_result}", break_loop=False)
            else:
                errors_collected.append(f"Firecrawl: {firecrawl_result[:200] if firecrawl_result else 'empty/no API key'}")
        except Exception as e:
            errors_collected.append(f"Firecrawl: {e}")
            logger.warning(f"Firecrawl search failed: {e}")

        # TIER 6: Fallback to Crawl4AI — scrape Google News RSS directly
        try:
            crawl4ai_result = await self.crawl4ai_news_search(query)
            if crawl4ai_result and len(crawl4ai_result.strip()) > 50:
                return Response(message=crawl4ai_result, break_loop=False)
            else:
                errors_collected.append("Crawl4AI/Google News returned empty results")
        except Exception as e:
            errors_collected.append(f"Crawl4AI: {e}")
            logger.error(f"Crawl4AI search also failed: {e}")

        # ALL SEARCH BACKENDS FAILED — surface clear error with code-execution fallback
        error_detail = "; ".join(errors_collected)
        logger.error(f"ALL SEARCH BACKENDS FAILED for query '{query}': {error_detail}")
        fail_msg = (
            f"⚠️ ALL SEARCH BACKENDS FAILED — no search results available.\n"
            f"Query: {query}\n"
            f"Errors: {error_detail}\n\n"
            f"CRITICAL INSTRUCTIONS — DO NOT HALLUCINATE:\n"
            f"1. Do NOT fabricate, invent, or guess ANY information to fill this gap.\n"
            f"2. Try `scrape_url` tool to directly scrape a news site (e.g., https://news.google.com, "
            f"https://news.ycombinator.com, or a relevant domain).\n"
            f"3. Use `code_execution_tool` to write a Python script or curl command "
            f"that manually searches the web (e.g., requests.get to Google, Brave Search API, "
            f"news RSS feeds, or any public API).\n"
            f"4. Delegate to a code-capable subordinate agent (e.g., multiagentdev) to write the search script.\n"
            f"5. Only as an absolute last resort, tell the user: 'All search systems are currently "
            f"unavailable. I cannot provide verified current information right now.'"
        )
        return Response(message=fail_msg, break_loop=False)

    async def tavily_search(self, question):
        """Tier 2: Tavily search via MCP. Context is properly packaged by the MCP layer."""
        from python.helpers.mcp_handler import MCPConfig
        result = await MCPConfig.call_tool("tavily-mcp.tavily_search", {
            "query": question,
            "max_results": SEARCH_ENGINE_RESULTS,
            "search_depth": "advanced",
        })
        return self._extract_mcp_text(result)

    async def tavily_extract(self, urls: list):
        """Extract content from URLs using Tavily extract via MCP."""
        from python.helpers.mcp_handler import MCPConfig
        result = await MCPConfig.call_tool("tavily-mcp.tavily_extract", {
            "urls": urls,
        })
        return self._extract_mcp_text(result)

    def _extract_mcp_text(self, result) -> str | None:
        """Extract text content from an MCP CallToolResult."""
        message = ""
        if hasattr(result, "content") and result.content:
            message = "\n\n".join(
                [item.text for item in result.content if hasattr(item, "type") and item.type == "text"]
            )
        elif isinstance(result, dict) and "content" in result:
            message = "\n\n".join(
                [item["text"] for item in result["content"] if item.get("type") == "text"]
            )
        if message and len(message.strip()) > 50:
            return message
        return None

    async def firecrawl_search(self, question):
        """Tier 5: Firecrawl search+scrape. Returns markdown content from search results."""
        api_key = os.environ.get("FIRECRAWL_API_KEY") or os.environ.get("API_KEY_FIRECRAWL")
        if not api_key:
            logger.warning("Firecrawl search skipped: no FIRECRAWL_API_KEY in environment")
            return None

        try:
            def _call_firecrawl():
                resp = requests.post(
                    "https://api.firecrawl.dev/v1/search",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": question,
                        "limit": SEARCH_ENGINE_RESULTS,
                        "scrapeOptions": {"formats": ["markdown"]},
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json()

            data = await asyncio.to_thread(_call_firecrawl)

            if not data.get("success"):
                return None

            outputs = []
            raw_data = data.get("data", [])
            items = raw_data if isinstance(raw_data, list) else raw_data.get("web", []) if isinstance(raw_data, dict) else []
            for item in items:
                if isinstance(item, dict):
                    title = item.get("title", item.get("metadata", {}).get("title", ""))
                    url = item.get("url", item.get("metadata", {}).get("sourceURL", ""))
                    content = item.get("markdown", item.get("description", ""))
                    outputs.append(f"{title}\n{url}\n{content[:500]}")

            formatted = "\n\n".join(outputs).strip()
            if formatted and len(formatted) > 50:
                return formatted
            return None
        except Exception as e:
            logger.warning(f"Firecrawl Search Error: {e}")
            raise

    async def searxng_search(self, question):
        try:
            # Clean query for SearXNG (strip advanced operators that cause Wikipedia 400s)
            # This is a safety measure for complex queries
            clean_query = question
            operators = ["site:", "after:", "before:", "inurl:", "intitle:"]
            for op in operators:
                if op in clean_query.lower():
                    # We strip the operator and the following word/domain to simplify for restricted engines
                    import re
                    # Match op and everything until next space or end of string
                    clean_query = re.sub(rf"{op}\S*", "", clean_query, flags=re.IGNORECASE).strip()
            
            if not clean_query:
                clean_query = question # Fallback if everything was stripped
            
            results = await searxng(clean_query)
            return self.format_result_searxng(results, "Search Engine")
        except Exception as e:
            logger.warning(f"SearxNG Search Error: {e}")
            return f"SearxNG Search Error: {e}. Please ensure SearxNG is correctly configured or try later."

    def format_result_searxng(self, result, source):
        if isinstance(result, Exception):
            handle_error(result)
            return f"{source} search failed: {str(result)}"

        outputs = []
        for item in result:
            outputs.append(f"{item['title']}\n{item['url']}\n{item['content']}")


        return "\n\n".join(outputs[:SEARCH_ENGINE_RESULTS]).strip()

    async def duckduckgo_search(self, question):
        """Fallback search using DuckDuckGo (free, no API key required)."""
        try:
            results = await asyncio.to_thread(
                duckduckgo_search.search, question, results=SEARCH_ENGINE_RESULTS
            )
            if not results:
                return ""
            # Format results consistently
            outputs = []
            for item in results:
                if isinstance(item, dict):
                    outputs.append(f"{item.get('title', '')}\n{item.get('href', item.get('url', ''))}\n{item.get('body', item.get('content', ''))}")
                else:
                    outputs.append(str(item))
            formatted = "\n\n".join(outputs).strip()
            if formatted:
                return f"DuckDuckGo Search Results:\n{formatted}"
            return ""
        except Exception as e:
            logger.warning(f"DuckDuckGo Search Error: {e}")
            raise

    async def crawl4ai_news_search(self, question):
        """Tier 4 fallback: scrape Google News RSS feed directly via urllib (no API key needed)."""
        import urllib.request
        import urllib.parse
        import xml.etree.ElementTree as ET
        
        try:
            encoded_query = urllib.parse.quote_plus(question)
            rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en"
            
            def fetch_rss():
                req = urllib.request.Request(rss_url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; AGIX/1.0)"
                })
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return resp.read().decode("utf-8")
            
            rss_content = await asyncio.to_thread(fetch_rss)
            root = ET.fromstring(rss_content)
            
            items = root.findall(".//item")
            if not items:
                return ""
            
            outputs = []
            for item in items[:SEARCH_ENGINE_RESULTS]:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")
                source = item.findtext("source", "")
                outputs.append(f"{title}\n{link}\nPublished: {pub_date} | Source: {source}")
            
            formatted = "\n\n".join(outputs).strip()
            if formatted:
                return f"Google News RSS Results:\n{formatted}"
            return ""
        except Exception as e:
            logger.warning(f"Crawl4AI/Google News RSS Error: {e}")
            raise
