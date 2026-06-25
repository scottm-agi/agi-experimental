import asyncio
import tempfile
import httpx
import logging
from pathlib import Path
from python.helpers.secrets_helper import get_secrets_manager
from python.helpers.parameters import get_parameters_manager

logger = logging.getLogger("mermaid-renderer")

async def render_mermaid_png(mmd_code: str, repo_owner: str, repo_name: str, issue_num: int) -> str:
    # Import playwright here to avoid failure if not installed
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        async_playwright = None
    """Render mermaid code to PNG using playwright chromium, upload to Forgejo issue assets."""
    secrets_manager = get_secrets_manager()
    secrets = secrets_manager.load_secrets()
    token = secrets.get('FORGEJO_TOKEN')

    params_manager = get_parameters_manager()
    params = params_manager.load_parameters()
    forgejo_url = params.get('FORGEJO_URL', '').rstrip('/')

    if not all([token, forgejo_url, repo_owner, repo_name]):
        raise ValueError('Missing Forgejo config: token, url, owner, repo')

    html_template = f"""<!DOCTYPE html>
<html>
<head>
    <script src='https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js'></script>
    <script>mermaid.initialize({{startOnLoad:true,theme:'default'}});</script>
    <style>.mermaid {{width: 800px; height: auto;}}</style>
</head>
<body>
    <div class='mermaid'>{mmd_code}</div>
</body>
</html>
    """

    png_path = None
    try:
        try:
            if async_playwright is None:
                raise ImportError("Playwright not installed")
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
                page = await browser.new_page()
                await page.set_viewport_size({"width": 1200, "height": 800})
                await page.set_content(html_template)
                await page.wait_for_selector('.mermaid svg', timeout=15000)
                png_path = Path(tempfile.mktemp(suffix='.png'))
                await page.locator('.mermaid').screenshot(path=str(png_path), type='png')
                await browser.close()
        except Exception as e:
            import logging
            logging.getLogger("mermaid").warning(f"Playwright rendering failed, falling back to mermaid.ink: {e}")
            import base64
            mmd_b64 = base64.b64encode(mmd_code.encode()).decode()
            ink_url = f"https://mermaid.ink/img/{mmd_b64}"
            png_path = Path(tempfile.mktemp(suffix='.png'))
            async with httpx.AsyncClient() as client:
                resp = await client.get(ink_url, timeout=30)
                resp.raise_for_status()
                with open(png_path, 'wb') as f:
                    f.write(resp.content)

        if not png_path.exists():
            raise ValueError('PNG screenshot failed')

        headers = {'Authorization': f'token {token}'}
        upload_url = f'{forgejo_url}/api/v1/repos/{repo_owner}/{repo_name}/issues/{issue_num}/assets'

        async with httpx.AsyncClient() as client:
            with open(png_path, 'rb') as f:
                files = {'attachment': ('mermaid.png', f, 'image/png')}
                resp = await client.post(upload_url, headers=headers, files=files)
                resp.raise_for_status()
                data = resp.json()
                # Forgejo/Gitea usually returns browser_download_url
                asset_url = data.get('browser_download_url') or data.get('download_url') or data.get('url')
                if not asset_url:
                    raise KeyError(f"Could not find download URL in Forgejo response. Available keys: {list(data.keys())}")
                return asset_url
    finally:
        if png_path and png_path.exists():
            png_path.unlink()

