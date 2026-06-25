from __future__ import annotations
import asyncio
from typing import Optional, Any
from python.helpers.tool import Tool, Response
from python.helpers import files, strings
from python.helpers.print_style import PrintStyle

class ScrapeUrl(Tool):
    """
    Scrapes a URL using Crawl4AI to extract high-quality Markdown or JSON content.
    Handles dynamic content, JavaScript, and infinite scrolling.
    """
    async def execute(self, url: str, wait_for: Optional[str] = None, screenshot: bool = False, **kwargs):
        if not url:
            return Response(message="Please provide a URL to scrape.", break_loop=False)

        self.update_progress(f"Scraping {url}...")
        
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
            
            # Configure browser
            browser_config = BrowserConfig(
                headless=True,
                browser_type="chromium",
            )
            
            # Configure extraction
            run_config = CrawlerRunConfig(
                wait_for="css:body", # Robust fallback
                cache_mode=CacheMode.BYPASS, # Ensure fresh content
                screenshot=screenshot,
                js_code="window.scrollTo(0, document.body.scrollHeight);", # Trigger lazy loading
                page_timeout=60000, # 60 seconds
            )

            async with AsyncWebCrawler(config=browser_config) as crawler:
                result = await crawler.arun(url=url, config=run_config)
                
                if not result or not result.success:
                    error_msg = result.error_message if result else "Unknown error"
                    return Response(message=f"Failed to scrape {url}: {error_msg}", break_loop=False)

                output = f"## Scraped Content from {url}\n\n"
                
                # Use Markdown as primary output for LLMs
                if result.markdown and hasattr(result.markdown, 'raw_markdown'):
                    output += result.markdown.raw_markdown
                elif isinstance(result.markdown, str):
                    output += result.markdown
                else:
                    output += "No markdown content extracted."

                # Optional screenshot
                if screenshot and result.screenshot:
                    # Save screenshot to chat folder
                    path = files.get_abs_path(
                        "tmp/screenshots",
                        f"scrape_{self.agent.context.generate_id()}.png"
                    )
                    files.make_dirs(path)
                    with open(path, "wb") as f:
                        f.write(files.decode_base64(result.screenshot))
                    output += f"\n\nScreenshot saved to: {path}"

                return Response(message=output, break_loop=False)

        except ImportError:
            return Response(message="Crawl4AI is not installed. Please ensure it is added to requirements.txt and installed.", break_loop=False)
        except Exception as e:
            PrintStyle().error(f"Scrape error: {e}")
            return Response(message=f"An error occurred while scraping: {str(e)}", break_loop=False)

    def update_progress(self, text):
        progress = f"Scraper: {text}"
        self.agent.context.log.set_progress(progress)
