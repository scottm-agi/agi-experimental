### scrape_url

Use this tool to extract high-quality content or screenshots from any URL using the **Crawl4AI** engine. This is the **preferred tool for intensive scraping**, large data extraction, and handling JavaScript-heavy or complex sites at high speed. It is much faster and more robust than manual browsing for bulk content.

Args:
- **url**: The absolute URL to scrape (e.g., https://example.com).
- **mode**: Output format. One of: `markdown` (default), `text`, or `screenshot`.
- **wait_for**: Optional CSS selector to wait for before scraping (useful for slow apps).
- **screenshot**: Boolean. If true, returns a screenshot path alongside the text.

Usage:

~~~json
{
    "thoughts": [
        "I need to check the mission statement on the Ethereum Foundation website.",
        "I will use scrape_url to get the markdown content."
    ],
    "headline": "Scraping ethereum.foundation",
    "tool_name": "scrape_url",
    "tool_args": {
        "url": "https://ethereum.foundation",
        "mode": "markdown"
    }
}
~~~
