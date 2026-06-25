from __future__ import annotations
"""
Build Navigation Map Tool for AGIX

Scans a project's source files to produce a navigation map artifact that
serves as the test plan for E2E verification. The map includes:

1. Frontend Routes — pages discovered from file-based routing (Next.js, etc.)
2. API Routes — backend endpoints discovered from route handlers
3. Navigation Links — links between pages from layout/component files
4. Interactive Elements — forms, buttons, and clickable elements per page

The output is a markdown file at docs/navigation-map.md that agents can
consume to verify "as-designed" vs "as-built".
"""

import logging
import os
import re
import json
from pathlib import Path
from python.helpers.tool import Tool, Response
from python.helpers.navigation_map_builder import build_map_markdown as _build_map_markdown_fn

logger = logging.getLogger("agix.tools.build_navigation_map")


class BuildNavigationMap(Tool):
    """
    Tool for building a navigation map from a project's source files.
    
    Scans the project directory for:
    - File-based routes (Next.js app router: src/app/**/page.tsx)
    - API route handlers (src/app/api/**/route.ts)
    - Navigation links (<Link>, <a> tags in layout and component files)
    - Interactive elements (forms, buttons) per page
    
    Produces a docs/navigation-map.md artifact as the E2E test plan.
    """

    async def execute(self, **kwargs) -> Response:
        """
        Build navigation map for a project.
        
        Args (via kwargs):
            project_dir: Path to the project root directory (required)
            framework: Framework hint (nextjs, vite, express, etc.) — auto-detected if omitted
            
        Returns:
            Response with the navigation map content and file path
        """
        project_dir = kwargs.get("project_dir", "").strip()
        framework = kwargs.get("framework", "").strip().lower()

        if not project_dir:
            return Response(
                message="Error: project_dir is required. Provide the absolute path to the project root.",
                break_loop=False,
            )

        if not os.path.isdir(project_dir):
            return Response(
                message=f"Error: project_dir '{project_dir}' does not exist or is not a directory.",
                break_loop=False,
            )

        try:
            # Auto-detect framework if not provided
            if not framework:
                framework = self._detect_framework(project_dir)

            # Scan for routes
            frontend_routes = self._scan_frontend_routes(project_dir, framework)
            api_routes = self._scan_api_routes(project_dir, framework)
            nav_links = self._scan_navigation_links(project_dir)
            
            # Build the navigation map markdown
            nav_map = self._build_map_markdown(
                project_dir, framework, frontend_routes, api_routes, nav_links
            )

            # Write to docs/navigation-map.md
            docs_dir = os.path.join(project_dir, "docs")
            os.makedirs(docs_dir, exist_ok=True)
            map_path = os.path.join(docs_dir, "navigation-map.md")
            
            with open(map_path, "w") as f:
                f.write(nav_map)

            # Also write JSON version for programmatic consumption
            json_map = {
                "framework": framework,
                "frontend_routes": frontend_routes,
                "api_routes": api_routes,
                "navigation_links": nav_links,
            }
            json_path = os.path.join(docs_dir, "navigation-map.json")
            with open(json_path, "w") as f:
                json.dump(json_map, f, indent=2)

            summary = (
                f"## Navigation Map Generated\n\n"
                f"- **Framework**: {framework}\n"
                f"- **Frontend Routes**: {len(frontend_routes)}\n"
                f"- **API Routes**: {len(api_routes)}\n"
                f"- **Navigation Links**: {len(nav_links)}\n"
                f"- **Map File**: `{map_path}`\n"
                f"- **JSON File**: `{json_path}`\n\n"
                f"### Frontend Routes\n"
            )
            for route in frontend_routes:
                summary += f"- `{route['path']}` ({route['file']})\n"
            
            summary += f"\n### API Routes\n"
            for route in api_routes:
                methods = ", ".join(route.get("methods", ["GET"]))
                summary += f"- `{methods} {route['path']}` ({route['file']})\n"

            if nav_links:
                summary += f"\n### Navigation Links Found\n"
                for link in nav_links[:20]:  # Cap at 20 for readability
                    summary += f"- `{link['href']}` in {link['source_file']}\n"

            return Response(message=summary, break_loop=False)

        except Exception as e:
            logger.exception(f"Failed to build navigation map: {e}")
            return Response(
                message=f"Error building navigation map: {e}",
                break_loop=False,
            )

    def _detect_framework(self, project_dir: str) -> str:
        """Auto-detect the project framework from package.json or file structure."""
        pkg_path = os.path.join(project_dir, "package.json")
        if os.path.isfile(pkg_path):
            try:
                with open(pkg_path, "r") as f:
                    pkg = json.load(f)
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "next" in deps:
                    return "nextjs"
                if "vite" in deps:
                    return "vite"
                if "express" in deps:
                    return "express"
                if "nuxt" in deps:
                    return "nuxt"
                if "@sveltejs/kit" in deps:
                    return "sveltekit"
            except json.JSONDecodeError:
                pass

        # Check for Python frameworks
        req_path = os.path.join(project_dir, "requirements.txt")
        if os.path.isfile(req_path):
            with open(req_path, "r") as f:
                content = f.read().lower()
            if "fastapi" in content:
                return "fastapi"
            if "flask" in content:
                return "flask"
            if "django" in content:
                return "django"

        return "unknown"

    def _scan_frontend_routes(self, project_dir: str, framework: str) -> list:
        """Scan for frontend page routes based on framework conventions."""
        routes = []
        
        if framework == "nextjs":
            # Next.js App Router: src/app/**/page.tsx
            for root, dirs, files in os.walk(project_dir):
                # Skip node_modules, .next, etc.
                dirs[:] = [d for d in dirs if d not in (
                    "node_modules", ".next", ".git", "dist", "build", "__pycache__"
                )]
                for f in files:
                    if f in ("page.tsx", "page.jsx", "page.ts", "page.js"):
                        full_path = os.path.join(root, f)
                        rel_path = os.path.relpath(full_path, project_dir)
                        
                        # Convert file path to URL route
                        route_path = self._file_to_route(rel_path, framework)
                        if route_path:
                            routes.append({
                                "path": route_path,
                                "file": rel_path,
                                "type": "page",
                            })

        elif framework in ("vite", "unknown"):
            # Generic SPA: look for route definitions
            for root, dirs, files in os.walk(project_dir):
                dirs[:] = [d for d in dirs if d not in (
                    "node_modules", ".next", ".git", "dist", "build", "__pycache__"
                )]
                for f in files:
                    if f in ("App.tsx", "App.jsx", "router.ts", "router.tsx", "routes.ts", "routes.tsx"):
                        full_path = os.path.join(root, f)
                        rel_path = os.path.relpath(full_path, project_dir)
                        try:
                            with open(full_path, "r") as fh:
                                content = fh.read()
                            # Find route definitions like path="/dashboard"
                            path_matches = re.findall(r'path[=:]\s*["\']([^"\']+)["\']', content)
                            for p in path_matches:
                                routes.append({
                                    "path": p,
                                    "file": rel_path,
                                    "type": "route-config",
                                })
                        except Exception:
                            pass

        return routes

    def _scan_api_routes(self, project_dir: str, framework: str) -> list:
        """Scan for API route handlers."""
        routes = []

        if framework == "nextjs":
            # Next.js API Routes: src/app/api/**/route.ts
            for root, dirs, files in os.walk(project_dir):
                dirs[:] = [d for d in dirs if d not in (
                    "node_modules", ".next", ".git", "dist", "build", "__pycache__"
                )]
                for f in files:
                    if f in ("route.ts", "route.tsx", "route.js"):
                        full_path = os.path.join(root, f)
                        rel_path = os.path.relpath(full_path, project_dir)
                        
                        # Only include files under an api/ directory
                        if "/api/" in rel_path or "\\api\\" in rel_path:
                            route_path = self._file_to_route(rel_path, framework)
                            if route_path:
                                # Detect HTTP methods
                                methods = self._detect_http_methods(full_path)
                                routes.append({
                                    "path": route_path,
                                    "file": rel_path,
                                    "type": "api",
                                    "methods": methods,
                                })

        elif framework in ("express", "fastapi", "flask"):
            # Look for route decorators / definitions
            for root, dirs, files in os.walk(project_dir):
                dirs[:] = [d for d in dirs if d not in (
                    "node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv"
                )]
                for f in files:
                    if f.endswith((".ts", ".js", ".py")):
                        full_path = os.path.join(root, f)
                        rel_path = os.path.relpath(full_path, project_dir)
                        try:
                            with open(full_path, "r") as fh:
                                content = fh.read()
                            
                            # Express: app.get('/api/...'), router.post('/api/...')
                            if framework == "express":
                                matches = re.findall(
                                    r'\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
                                    content, re.IGNORECASE
                                )
                                for method, path in matches:
                                    if "/api" in path or path.startswith("/"):
                                        routes.append({
                                            "path": path,
                                            "file": rel_path,
                                            "type": "api",
                                            "methods": [method.upper()],
                                        })
                            
                            # FastAPI: @app.get("/api/...")
                            elif framework == "fastapi":
                                matches = re.findall(
                                    r'@\w+\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
                                    content, re.IGNORECASE
                                )
                                for method, path in matches:
                                    routes.append({
                                        "path": path,
                                        "file": rel_path,
                                        "type": "api",
                                        "methods": [method.upper()],
                                    })

                        except Exception:
                            pass

        return routes

    def _scan_navigation_links(self, project_dir: str) -> list:
        """Scan layout and component files for navigation links."""
        links = []
        
        for root, dirs, files in os.walk(project_dir):
            dirs[:] = [d for d in dirs if d not in (
                "node_modules", ".next", ".git", "dist", "build", "__pycache__"
            )]
            for f in files:
                if f in ("layout.tsx", "layout.jsx", "Layout.tsx", "Layout.jsx",
                         "Navbar.tsx", "Navbar.jsx", "Nav.tsx", "Nav.jsx",
                         "Sidebar.tsx", "Sidebar.jsx", "Header.tsx", "Header.jsx",
                         "Navigation.tsx", "Navigation.jsx"):
                    full_path = os.path.join(root, f)
                    rel_path = os.path.relpath(full_path, project_dir)
                    try:
                        with open(full_path, "r") as fh:
                            content = fh.read()
                        
                        # Find <Link href="/..."> patterns
                        link_matches = re.findall(r'<Link\s+[^>]*href=["\']([^"\']+)["\']', content)
                        for href in link_matches:
                            links.append({
                                "href": href,
                                "source_file": rel_path,
                                "type": "link",
                            })
                        
                        # Find <a href="/..."> patterns
                        a_matches = re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\']', content)
                        for href in a_matches:
                            if href.startswith("/") or href.startswith("http"):
                                links.append({
                                    "href": href,
                                    "source_file": rel_path,
                                    "type": "anchor",
                                })

                    except Exception:
                        pass
        
        return links

    def _file_to_route(self, rel_path: str, framework: str) -> str:
        """Convert a file path to a URL route."""
        if framework == "nextjs":
            # Remove src/app prefix and page.tsx suffix
            route = rel_path
            for prefix in ("src/app/", "app/", "src\\app\\", "app\\"):
                if route.startswith(prefix):
                    route = route[len(prefix):]
                    break
            
            # Remove page.tsx/page.jsx suffix
            for suffix in ("/page.tsx", "/page.jsx", "/page.ts", "/page.js",
                           "\\page.tsx", "\\page.jsx", "\\page.ts", "\\page.js",
                           "/route.ts", "/route.tsx", "/route.js",
                           "\\route.ts", "\\route.tsx", "\\route.js"):
                if route.endswith(suffix):
                    route = route[:-len(suffix)]
                    break
            
            # Convert directory separators
            route = route.replace("\\", "/")
            
            # Handle root page
            if not route or route == "page.tsx":
                return "/"
            
            # Handle dynamic routes: [id] → :id
            route = re.sub(r'\[([^\]]+)\]', r':\1', route)
            
            return f"/{route}"
        
        return None

    def _detect_http_methods(self, file_path: str) -> list:
        """Detect which HTTP methods are exported from a route file."""
        methods = []
        try:
            with open(file_path, "r") as f:
                content = f.read()
            
            for method in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                # Check for: export async function GET, export function GET, export const GET
                if re.search(rf'export\s+(async\s+)?function\s+{method}', content):
                    methods.append(method)
                elif re.search(rf'export\s+const\s+{method}', content):
                    methods.append(method)
        except Exception:
            pass
        
        return methods if methods else ["GET"]

    def _build_map_markdown(
        self,
        project_dir: str,
        framework: str,
        frontend_routes: list,
        api_routes: list,
        nav_links: list,
    ) -> str:
        """Render scanned data into a structured markdown navigation map.

        Delegates to the standalone helper function for testability.
        """
        return _build_map_markdown_fn(
            project_dir=project_dir,
            framework=framework,
            frontend_routes=frontend_routes,
            api_routes=api_routes,
            nav_links=nav_links,
        )
