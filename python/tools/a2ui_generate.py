from __future__ import annotations
import json
import logging
from typing import Any, Optional
from python.helpers.tool import Tool, Response

logger = logging.getLogger("a2ui-generate")

# UI spec version
UI_VERSION = "v0.9"
CATALOG_ID = "https://a2ui.org/specification/v0_9/basic_catalog.json"

# Aliases for backward compatibility and test imports
A2UI_VERSION = UI_VERSION

# Icon name mapping: Material Symbols → UI spec icon names
ICON_MAP = {
    "info": "info",
    "warning": "warning",
    "error": "error",
    "check": "check",
    "settings": "settings",
    "person": "person",
    "mail": "mail",
    "home": "home",
    "search": "search",
    "star": "star",
    "favorite": "favorite",
    "help": "help",
    "edit": "edit",
    "delete": "delete",
    "add": "add",
    "close": "close",
    "refresh": "refresh",
    "download": "download",
    "upload": "upload",
    "folder": "folder",
    "notifications": "notifications",
    "visibility": "visibility",
    "lock": "lock",
    "payment": "payment",
    "phone": "phone",
    "photo": "photo",
    "share": "share",
    "send": "send",
    "play": "play",
    "pause": "pause",
    "stop": "stop",
}


class A2UIGenerate(Tool):
    """
    Generates rich UI JSON payloads for rendering
    interactive tiles in the chat interface.

    Supports preset component types:
      - info_card: Card with title, body text, optional icon
      - data_table: Key-value data rendered as a list
      - status_dashboard: Multi-section card with labeled metrics
      - action_form: Form with input fields (display-only for now)
    UI Spec: https://a2ui.org/specification/v0.9-a2ui/
    """

    async def execute(self, **kwargs: Any) -> Response:
        await self.agent.handle_intervention()

        component_type = self.args.get("component_type", "info_card")
        title = self.args.get("title", "")
        data = self.args.get("data", {})
        icon = self.args.get("icon", "")

        # Parse data if string
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return Response(
                    message="Error: 'data' must be valid JSON.",
                    break_loop=False,
                )

        try:
            surface_id = f"view_{self.agent.context.generate_id()}"
            builder = UIGenerator(surface_id)

            # Auto-detect composite payloads (e.g. Test Suite/Test Cases)
            is_composite = False
            if isinstance(data, dict):
                # If it's a dict with Test_Cases or if a2ui_builder._auto_unnest returns a list of components
                unnested = builder._auto_unnest(data)
                if isinstance(unnested, list) and len(unnested) > 0:
                    first_item = unnested[0]
                    if isinstance(first_item, dict) and ("component_metadata" in first_item or "type" in first_item):
                        is_composite = True
                        data = unnested

            if is_composite:
                payload = builder.build_composite(title, data, icon)
            elif component_type == "info_card":
                payload = builder.build_info_card(title, data, icon)
            elif component_type == "data_table":
                payload = builder.build_data_table(title, data, icon)
            elif component_type == "status_dashboard":
                payload = builder.build_status_dashboard(title, data, icon)
            elif component_type == "action_form":
                payload = builder.build_action_form(title, data, icon)
            elif component_type == "scatter_chart":
                payload = builder.build_scatter_chart(title, data, icon)
            elif component_type == "bar_chart":
                payload = builder.build_bar_chart(title, data, icon)
            elif component_type == "line_chart":
                payload = builder.build_line_chart(title, data, icon)
            elif component_type == "pie_chart":
                payload = builder.build_pie_chart(title, data, icon)
            elif component_type == "radar_chart":
                payload = builder.build_radar_chart(title, data, icon)
            elif component_type == "gauge_chart":
                payload = builder.build_gauge_chart(title, data, icon)
            elif component_type == "funnel_chart":
                payload = builder.build_funnel_chart(title, data, icon)
            elif component_type == "treemap_chart":
                payload = builder.build_treemap_chart(title, data, icon)
            elif component_type == "area_chart":
                payload = builder.build_area_chart(title, data, icon)
            elif component_type == "echart":
                payload = builder.build_echart(title, data, icon)
            elif component_type == "custom":
                components = data.get("components", [])
                if not components:
                    return Response(
                        message="Error: 'custom' type requires 'data.components' array with A2UI component objects.",
                        break_loop=False,
                    )
                payload = builder.build_custom(components)
            else:
                valid = "info_card, data_table, status_dashboard, action_form, scatter_chart, bar_chart, line_chart, pie_chart, radar_chart, gauge_chart, funnel_chart, treemap_chart, area_chart, echart, custom"
                return Response(
                    message=f"Error: Unknown component_type '{component_type}'. Use: {valid}.",
                    break_loop=False,
                )

            # Build text summary for conversation history
            summary = self._build_summary(component_type, title, data)

            return Response(
                message=summary,
                break_loop=False,
                additional={
                    "type": "a2ui",
                    "payload": payload,
                },
            )

        except Exception as e:
            logger.error(f"A2UI generation failed: {e}", exc_info=True)
            return Response(
                message=f"Error: Failed to generate A2UI payload. {str(e)}",
                break_loop=False,
            )

    def _build_summary(self, component_type: str, title: str, data: Any) -> str:
        """Build a text summary of the generated UI for the conversation."""
        type_labels = {
            "info_card": "information card",
            "data_table": "data table",
            "status_dashboard": "status dashboard",
            "action_form": "input form",
            "scatter_chart": "scatter chart",
            "bar_chart": "bar chart",
            "line_chart": "line chart",
            "pie_chart": "pie chart",
            "radar_chart": "radar chart",
            "gauge_chart": "gauge chart",
            "funnel_chart": "funnel chart",
            "treemap_chart": "treemap chart",
            "area_chart": "area chart",
            "echart": "interactive chart",
            "custom": "custom UI",
        }
        label = type_labels.get(component_type, component_type)
        summary = f"Generated {label}"
        if title:
            summary += f": **{title}**"

        if isinstance(data, dict):
            if component_type == "data_table":
                rows = data.get("rows", data)
                if isinstance(rows, dict):
                    summary += f"\n\n| Key | Value |\n|-----|-------|\n"
                    for k, v in rows.items():
                        summary += f"| {k} | {v} |\n"
            elif component_type == "status_dashboard":
                metrics = data.get("metrics", data)
                if isinstance(metrics, dict):
                    items = [f"**{k}**: {v}" for k, v in metrics.items()]
                    summary += "\n\n" + " · ".join(items)
                elif isinstance(metrics, list):
                    items = []
                    for m in metrics:
                        if isinstance(m, dict):
                            items.append(f"**{m.get('label', m.get('name', 'Metric'))}**: {m.get('value', '')}")
                        else:
                            items.append(str(m))
                    summary += "\n\n" + " · ".join(items)
            elif component_type == "info_card":
                body = data.get("body", data.get("text", data.get("content", "")))
                if body:
                    summary += f"\n\n{body}"
            elif component_type == "scatter_chart":
                points = data.get("points", [])
                summary += f"\n\n{len(points)} data points plotted"
                if data.get("quadrants"):
                    summary += " (Eisenhower quadrant view)"
            elif component_type == "bar_chart":
                bars = data.get("bars", {})
                count = len(bars) if isinstance(bars, (list, dict)) else 0
                summary += f"\n\n{count} bars"
            elif component_type == "line_chart":
                series_count = len(data.get("series", data.get("values", [])))
                summary += f"\n\n{series_count} data series"
            elif component_type == "pie_chart":
                slices = data.get("slices", data.get("segments", []))
                summary += f"\n\n{len(slices)} segments"
            elif component_type in ("radar_chart", "gauge_chart", "funnel_chart", "treemap_chart", "area_chart"):
                summary += " (interactive ECharts visualization)"
            elif component_type == "echart":
                chart_type = data.get("options", {}).get("series", [{}])[0].get("type", "unknown") if isinstance(data.get("options"), dict) else "custom"
                summary += f" ({chart_type} type)"

        return summary


class UIGenerator:
    """Builds spec-compliant UI v0.9 JSON payloads."""

    def __init__(self, surface_id: str):
        self.surface_id = surface_id
        self._counter = 0

    def _id(self, prefix: str = "c") -> str:
        """Generate a unique component ID."""
        self._counter += 1
        return f"{prefix}_{self._counter}"

    # Premium "agix" Theme Palette
    PALETTE = {
        "primary": "#6366f1",   # Indigo/Purple (Frame accent)
        "secondary": "#94a3b8", # Slate 400
        "emerald": "#10b981",   # Emerald 500 (Success)
        "rose": "#f43f5e",      # Rose 500 (Error)
        "amber": "#fbbf24",     # Amber 400 (Warning) - slightly lighter for better contrast
        "slate": "#334155",     # Slate 700 (Text)
        "neutral": "#f8fafc",   # Slate 50 (Row Zebra/Card Background)
        "border": "#e2e8f0",    # Slate 200 (Divider)
    }
    CHART_COLORS = ['#6366f1', '#10b981', '#f59e0b', '#06b6d4', '#f43f5e']

    def _envelope(self, components: list[dict], data_model: Optional[dict] = None) -> dict:
        """Create the full A2UI envelope with createSurface + updateComponents."""
        payload = {
            "messages": [
                {
                    "version": A2UI_VERSION,
                    "createSurface": {
                        "surfaceId": self.surface_id,
                        "catalogId": CATALOG_ID,
                    },
                },
                {
                    "version": A2UI_VERSION,
                    "updateComponents": {
                        "surfaceId": self.surface_id,
                        "components": components,
                    },
                },
            ]
        }
        if data_model:
            payload["messages"].append({
                "version": A2UI_VERSION,
                "updateDataModel": {
                    "surfaceId": self.surface_id,
                    "value": data_model,
                },
            })
        return payload

    def _normalize_data(self, data: any, key: str) -> dict | list[dict]:
        """
        Normalize data that might be a list or a dict.
        - If it's a simple list of {label, value}, returns a dict.
        - If it's a record set (list of complex dicts), returns a list of dicts.
        - If it's a dict containing the target key, extracts that.
        """
        if isinstance(data, dict):
            # Check if actual data is wrapped in common enterprise keys
            data = self._auto_unnest(data)
            
            # If it's still a dict, check if it's a dict of dicts (nested table rows)
            if isinstance(data, dict) and data and all(isinstance(v, dict) for v in data.values()):
                flattened = []
                for k, v in data.items():
                    row = {"Row Key": k}
                    row.update(v)
                    flattened.append(row)
                data = flattened

        if isinstance(data, list):
            if not data:
                return {}
            
            # Check if it's a list of dicts
            if all(isinstance(x, dict) for x in data):
                # Is it a simple label/value list?
                is_simple = True
                for item in data:
                    keys = set(item.keys())
                    # Must have exactly label/value or key/value
                    if not (({"label", "value"} <= keys and len(keys) == 2) or 
                            ({"key", "value"} <= keys and len(keys) == 2)):
                        is_simple = False
                        break
                
                if is_simple:
                    normalized = {}
                    for item in data:
                        l = item.get("label") or item.get("key")
                        v = item.get("value")
                        normalized[str(l)] = v
                    return normalized
                else:
                    # It's a complex record set (multi-column)
                    return data
            
            # List of scalars -> simple dict
            normalized = {}
            for i, item in enumerate(data):
                normalized[f"Item {i+1}"] = item
            return normalized
        
        if isinstance(data, dict):
            # If the specific key (e.g. 'rows' or 'metrics') is present, try to use its value
            target_val = data.get(key)
            if target_val is not None:
                # Recursively normalize the inner data
                return self._normalize_data(target_val, key)
            
            # Otherwise return the dict as-is
            return data
            
        return {key.capitalize(): str(data)}

    def _auto_unnest(self, data: Any) -> Any:
        """Recursively search for a usable data payload in complex envelopes."""
        if not isinstance(data, dict):
            return data
            
        # Common enterprise keys that wrap the actual data
        target_keys = ["Test_Cases", "data_payload", "results", "items", "data", "rows"]
        
        for key in target_keys:
            if key in data:
                return data[key]
        
        # If no common keys found, but it's a single-key dict, try digging deeper
        if len(data) == 1:
            key = list(data.keys())[0]
            val = data[key]
            if isinstance(val, (dict, list)):
                return self._auto_unnest(val)
                
        return data

    def build_composite(self, title: str, data: Any, icon: str = "") -> dict:
        """Build a composite card containing multiple sub-components."""
        components = []
        
        # Add title if provided
        if title:
            components.append({
                "id": "comp_title", 
                "component": "Text", 
                "text": title, 
                "variant": "h4",
                "align": "center",
                "margin": "0 0 8px 0"
            })
            components.append({"id": "comp_divider", "component": "Divider", "margin": "0 0 16px 0"})

        # If data is a list, treat each item as a potential sub-component
        if not isinstance(data, list):
            data = [data]

        all_ids = []
        for i, item in enumerate(data):
            # Try to identify component type from item metadata or structure
            comp_type = "data_table" # default fallback
            item_data = item
            item_title = ""
            
            if isinstance(item, dict):
                # Check for standard metadata wrappers
                meta = item.get("component_metadata") or item.get("metadata") or {}
                if meta:
                    comp_type = meta.get("type") or meta.get("component_type") or comp_type
                    item_title = meta.get("title") or ""
                    item_data = item.get("data_payload") or item.get("data") or item
                elif "type" in item:
                    comp_type = item["type"]
                    item_data = item.get("data") or item.get("rows") or item
                    item_title = item.get("title") or ""
                
                # Further heuristics if type is still default
                if comp_type == "data_table":
                    it_str = str(item_data).lower()
                    if "sparkline" in it_str:
                        comp_type = "status_dashboard"
                    elif any(x in it_str for x in ("market", "price", "percent")):
                        comp_type = "status_dashboard"

            # Use existing builders to get component definitions
            try:
                sub_payload = None
                if comp_type in ("data_table", "monitoring_table"):
                    sub_payload = self.build_data_table(item_title, item_data)
                elif comp_type in ("status_dashboard", "market_overview"):
                    sub_payload = self.build_status_dashboard(item_title, item_data)
                elif "chart" in str(comp_type):
                    builder_map = {
                        "bar_chart": self.build_bar_chart,
                        "line_chart": self.build_line_chart,
                        "pie_chart": self.build_pie_chart,
                        "scatter_chart": self.build_scatter_chart,
                        "area_chart": self.build_area_chart,
                        "radar_chart": self.build_radar_chart,
                        "gauge_chart": self.build_gauge_chart,
                        "funnel_chart": self.build_funnel_chart,
                        "treemap_chart": self.build_treemap_chart,
                    }
                    builder_fn = builder_map.get(comp_type, self.build_line_chart)
                    sub_payload = builder_fn(item_title, item_data)
                else:
                    sub_payload = self.build_data_table(item_title, item_data)

                if sub_payload:
                    # Find the components list in the payload
                    sub_comps = []
                    for msg in sub_payload.get("messages", []):
                        if "updateComponents" in msg:
                            sub_comps = msg["updateComponents"]["components"]
                            break
                    
                    if sub_comps:
                        # Prefix all IDs to avoid collisions
                        id_prefix = f"s{i}_"
                        id_map = {c["id"]: f"{id_prefix}{c['id']}" for c in sub_comps}
                        
                        def remap(val):
                            if isinstance(val, str):
                                return id_map.get(val, val)
                            if isinstance(val, list):
                                return [remap(x) for x in val]
                            if isinstance(val, dict):
                                return {k: remap(v) for k, v in val.items()}
                            return val

                        for sc in sub_comps:
                            # Remap ID and internal references
                            old_id = sc["id"]
                            sc["id"] = id_map[old_id]
                            
                            for ref in ("child", "children"):
                                if ref in sc:
                                    sc[ref] = remap(sc[ref])
                            
                            # Add to master list
                            components.append(sc)
                            if old_id == "root":
                                all_ids.append(sc["id"])
                        
                        # Spacing
                        if i < len(data) - 1:
                            sep_id = self._id("sep")
                            components.append({"id": sep_id, "component": "Divider", "style": {"margin": "24px 0"}})
                            all_ids.append(sep_id)

            except Exception as e:
                logger.error(f"Failed to build composite sub-component {i}: {e}")
                components.append({
                    "id": f"err_{i}", 
                    "component": "Text", 
                    "text": f"Error rendering {comp_type}: {str(e)}", 
                    "color": "#ef4444"
                })

        # Final Layout
        all_ids = [c["id"] for c in components]
        components.append({
            "id": "composite_col",
            "component": "Column",
            "children": all_ids,
            "padding": "12px"
        })
        components.append({
            "id": "root",
            "component": "Card",
            "child": "composite_col"
        })
        
        return self._envelope(components)

    def _get_status_icon(self, status: str) -> str:
        """Map status strings to Material Symbols."""
        s = str(status).lower()
        if any(x in s for x in ("success", "ok", "pass", "done", "complete", "active", "online")):
            return "check_circle"
        if any(x in s for x in ("fail", "error", "critical", "stop", "offline", "broken")):
            return "error"
        if any(x in s for x in ("warn", "pend", "wait", "hold", "slow")):
            return "warning"
        if any(x in s for x in ("run", "process", "sync", "syncing")):
            return "sync"
        return "info"

    def build_info_card(self, title: str, data: dict, icon: str = "") -> dict:
        """Build a simple info card with title, optional icon, and body text."""
        if isinstance(data, str):
            body = data
        else:
            body = data.get("body", data.get("text", data.get("content", "")))

        children_ids = []
        components = []

        # Header row (icon + title)
        if title:
            title_id = self._id("title")
            components.append({
                "id": title_id,
                "component": "Text",
                "text": title,
                "variant": "h2",
            })

            if icon and icon in ICON_MAP:
                icon_id = self._id("icon")
                header_row_id = self._id("header")
                components.append({"id": icon_id, "component": "Icon", "name": ICON_MAP[icon]})
                components.append({
                    "id": header_row_id,
                    "component": "Row",
                    "children": [icon_id, title_id],
                    "align": "center",
                })
                children_ids.append(header_row_id)
            else:
                children_ids.append(title_id)

        # Body text
        if body:
            body_id = self._id("body")
            components.append({
                "id": body_id,
                "component": "Text",
                "text": str(body),
                "variant": "body",
            })
            children_ids.append(body_id)

        # Build tree
        content_col_id = self._id("col")
        components.append({
            "id": content_col_id,
            "component": "Column",
            "children": children_ids,
            "justify": "start",
            "align": "stretch",
        })

        components.append({
            "id": "root",
            "component": "Card",
            "child": content_col_id,
        })

        return self._envelope(components)

    def build_data_table(self, title: str, data: any, icon: str = "") -> dict:
        """Build a data table. Supports key-value dict or multi-column record list."""
        rows = self._normalize_data(data, "rows")
        children_ids = []
        components = []

        # Title / Header
        if title:
            title_id = self._id("title")
            components.append({
                "id": title_id,
                "component": "Text",
                "text": title,
                "variant": "h3",
            })
            children_ids.append(title_id)
            components.append({"id": self._id("div"), "component": "Divider"})
            children_ids.append(components[-1]["id"])

        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            # MULTI-COLUMN TABLE MODE
            columns = list(rows[0].keys())
            header_row_ids = []
            for col in columns:
                col_id = self._id("hcol")
                components.append({
                    "id": col_id,
                    "component": "Text",
                    "text": str(col).upper(),
                    "variant": "caption",
                })
                header_row_ids.append(col_id)
            
            hrow_id = self._id("hrow")
            components.append({
                "id": hrow_id,
                "component": "Row",
                "children": header_row_ids,
                "justify": "spaceBetween",
                "style": {
                    "backgroundColor": self.PALETTE["neutral"],
                    "padding": "8px 12px",
                    "borderRadius": "4px",
                    "marginBottom": "4px"
                }
            })
            children_ids.append(hrow_id)
            # divider is skipped if we use background padding

            for idx, row_data in enumerate(rows):
                row_cell_ids = []
                for col in columns:
                    cell_val = row_data.get(col, "")
                    cell_id = self._id("cell")
                    
                    # Detect status columns for color/icon treatment
                    is_status = any(x in str(col).lower() for x in ("status", "state", "health"))
                    
                    comp = {
                        "id": cell_id,
                        "component": "Text",
                        "text": str(cell_val),
                        "variant": "body",
                    }
                    if is_status:
                        icon_name = self._get_status_icon(str(cell_val))
                        icon_id = self._id("sicon")
                        components.append({"id": icon_id, "component": "Icon", "name": icon_name})
                        
                        cell_row_id = self._id("cellrow")
                        components.append({
                            "id": cell_row_id,
                            "component": "Row",
                            "children": [icon_id, cell_id],
                            "align": "center",
                        })
                        row_cell_ids.append(cell_row_id)
                    else:
                        row_cell_ids.append(cell_id)
                    components.append(comp)

                row_wrapper_id = self._id("row")
                components.append({
                    "id": row_wrapper_id,
                    "component": "Row",
                    "children": row_cell_ids,
                    "justify": "spaceBetween",
                    "align": "center",
                    "style": {
                        "padding": "10px 12px",
                        "borderBottom": f"1px solid {self.PALETTE['border']}" if idx < len(rows) - 1 else "none",
                        "backgroundColor": "transparent" if idx % 2 == 0 else "rgba(248, 250, 252, 0.5)" # Zebra
                    }
                })
                children_ids.append(row_wrapper_id)
        else:
            # KEY-VALUE MODE (Fallback/Standard)
            rows_dict = rows if isinstance(rows, dict) else {}
            for key, value in rows_dict.items():
                row_id = self._id("row")
                key_id = self._id("key")
                val_id = self._id("val")

                # If value is a dict and we missed flattening it, stringify it cleanly
                val_text = json.dumps(value) if isinstance(value, (dict, list)) else str(value)

                components.append({"id": key_id, "component": "Text", "text": str(key), "variant": "caption", "style": {"color": self.PALETTE["primary"]}})
                components.append({"id": val_id, "component": "Text", "text": val_text, "variant": "body"})
                components.append({
                    "id": row_id,
                    "component": "Row",
                    "children": [key_id, val_id],
                    "justify": "spaceBetween",
                    "align": "center",
                    "style": {"padding": "8px 0"}
                })
                children_ids.append(row_id)

        content_col_id = self._id("col")
        components.append({
            "id": content_col_id, "component": "Column", "children": children_ids,
            "justify": "start", "align": "stretch",
        })
        components.append({"id": "root", "component": "Card", "child": content_col_id})
        return self._envelope(components)

    def build_status_dashboard(self, title: str, data: any, icon: str = "") -> dict:
        """Build a dashboard with metrics. Supports status-rich data."""
        metrics_list = self._normalize_data(data, "metrics")
        children_ids = []
        components = []
        data_model = {}

        # Title row
        if title:
            title_id = self._id("title")
            components.append({"id": title_id, "component": "Text", "text": title, "variant": "h2"})
            if icon and icon in ICON_MAP:
                icon_id = self._id("icon")
                header_id = self._id("header")
                components.append({"id": icon_id, "component": "Icon", "name": ICON_MAP[icon]})
                components.append({"id": header_id, "component": "Row", "children": [icon_id, title_id], "align": "center"})
                children_ids.append(header_id)
            else:
                children_ids.append(title_id)
            components.append({"id": self._id("div"), "component": "Divider"})
            children_ids.append(components[-1]["id"])

        # Metrics / Gauges (Dict for easier lookup)
        metrics_dict = {}
        if isinstance(metrics_list, list):
            for i, m in enumerate(metrics_list):
                lbl = f"Metric {i+1}"
                if isinstance(m, dict):
                    lbl = m.get("label", m.get("name", m.get("Asset Name", lbl)))
                metrics_dict[lbl] = m
        elif isinstance(metrics_list, dict):
            metrics_dict = metrics_list
        
        # Build composite structure
        metric_card_ids = []
        for label, val_data in metrics_dict.items():
            val_id = self._id("mval")
            lbl_id = self._id("mlbl")
            mcol_id = self._id("mcol")

            # Handle status-rich metrics: {"value": 10, "status": "success", "sparkline_data": [...]}
            display_val = val_data
            status_icon = None
            sparkline = None
            if isinstance(val_data, dict):
                # Try to find a value field
                display_val = val_data.get("value", val_data.get("Current Price", ""))
                status_str = val_data.get("status") or val_data.get("state")
                if status_str:
                    status_icon = self._get_status_icon(status_str)
                
                # Check for sparkline data
                sdata = val_data.get("sparkline_data")
                if isinstance(sdata, list) and len(sdata) > 1:
                    sparkline = sdata

            val_row_ids = []
            if status_icon:
                icon_id = self._id("msicon")
                components.append({"id": icon_id, "component": "Icon", "name": status_icon})
                val_row_ids.append(icon_id)
            
            components.append({"id": val_id, "component": "Text", "text": str(display_val), "variant": "h3"})
            val_row_ids.append(val_id)
            
            vrow_id = self._id("vrow")
            components.append({"id": vrow_id, "component": "Row", "children": val_row_ids, "align": "center", "justify": "center"})

            # Optional Sparkline
            m_children = [vrow_id]
            if sparkline:
                spark_id = self._id("spark")
                # Cast sparkline values to floats
                spark_vals = [float(x) for x in sparkline if str(x).replace('.','',1).replace('-','',1).isdigit()]
                components.append({
                    "id": spark_id,
                    "component": "EChart",
                    "style": {"height": "60px", "width": "100%"},
                    "options": {
                        "xAxis": {"type": "category", "show": False},
                        "yAxis": {"type": "value", "show": False, "min": "dataMin", "max": "dataMax"},
                        "series": [{
                            "type": "line",
                            "data": spark_vals,
                            "smooth": True,
                            "showSymbol": False,
                            "lineStyle": {"width": 2, "color": self.PALETTE["primary"]},
                            "areaStyle": {"opacity": 0.1, "color": self.PALETTE["primary"]}
                        }],
                        "grid": {"left": 2, "right": 2, "top": 5, "bottom": 5}
                    }
                })
                m_children.append(spark_id)

            components.append({"id": lbl_id, "component": "Text", "text": str(label), "variant": "caption", "style": {"color": self.PALETTE["slate"]}})
            m_children.append(lbl_id)

            components.append({
                "id": mcol_id, 
                "component": "Column", 
                "children": m_children, 
                "align": "center", 
                "justify": "center",
                "style": {
                    "flex": "1",
                    "minWidth": "120px",
                    "padding": "12px",
                    "margin": "4px",
                    "backgroundColor": self.PALETTE["neutral"],
                    "borderRadius": "8px",
                    "border": f"1px solid {self.PALETTE['border']}"
                }
            })
            metric_card_ids.append(mcol_id)

        metrics_row_id = self._id("mrow")
        components.append({
            "id": metrics_row_id, "component": "Row", "children": metric_card_ids,
            "justify": "spaceEvenly", "align": "start",
            "style": {"flexWrap": "wrap"}
        })
        children_ids.append(metrics_row_id)

        description = ""
        if isinstance(data, dict):
            description = data.get("description", data.get("subtitle", ""))
        if description:
            desc_id = self._id("desc")
            components.append({"id": desc_id, "component": "Text", "text": str(description), "variant": "caption"})
            children_ids.append(desc_id)

        content_col_id = self._id("col")
        components.append({
            "id": content_col_id, "component": "Column", "children": children_ids,
            "justify": "start", "align": "stretch",
        })
        components.append({"id": "root", "component": "Card", "child": content_col_id})
        return self._envelope(components)

    def build_action_form(self, title: str, data: dict, icon: str = "") -> dict:
        """Build a form with input fields (display-only in Phase 1)."""
        fields = data.get("fields", [])
        if not isinstance(fields, list):
            # Convert dict to field list
            if isinstance(fields, dict) or isinstance(data, dict):
                source = fields if isinstance(fields, dict) else data
                fields = [
                    {"label": k, "value": str(v)}
                    for k, v in source.items()
                    if k not in ("fields", "title", "submit_label")
                ]

        children_ids = []
        components = []

        # Title
        if title:
            title_id = self._id("title")
            components.append({
                "id": title_id,
                "component": "Text",
                "text": title,
                "variant": "h2",
            })
            children_ids.append(title_id)

            div_id = self._id("div")
            components.append({"id": div_id, "component": "Divider"})
            children_ids.append(div_id)

        # Form fields
        data_model = {}
        for i, field in enumerate(fields):
            label = field.get("label", f"Field {i + 1}")
            value = field.get("value", "")
            field_type = field.get("type", "shortText")
            field_id = self._id("field")
            data_key = label.lower().replace(" ", "_")

            components.append({
                "id": field_id,
                "component": "TextField",
                "label": label,
                "value": {"path": f"/form/{data_key}"},
                "variant": field_type if field_type in ("shortText", "longText", "number", "obscured") else "shortText",
            })
            children_ids.append(field_id)
            data_model[data_key] = value

        # Submit button (display-only)
        submit_label = data.get("submit_label", "Submit")
        btn_label_id = self._id("btnlbl")
        btn_id = self._id("btn")
        components.append({
            "id": btn_label_id,
            "component": "Text",
            "text": submit_label,
        })
        components.append({
            "id": btn_id,
            "component": "Button",
            "child": btn_label_id,
            "variant": "primary",
            "action": {
                "event": {"name": "submit", "context": {"formId": self.surface_id}},
            },
        })
        children_ids.append(btn_id)

        content_col_id = self._id("col")
        components.append({
            "id": content_col_id,
            "component": "Column",
            "children": children_ids,
            "justify": "start",
            "align": "stretch",
        })

        components.append({
            "id": "root",
            "component": "Card",
            "child": content_col_id,
        })

        return self._envelope(components, {"form": data_model} if data_model else None)

    def build_custom(self, components: list[dict]) -> dict:
        """Pass through raw A2UI components (advanced usage)."""
        # Validate root exists
        has_root = any(c.get("id") == "root" for c in components)
        if not has_root:
            raise ValueError("Custom components must include a component with id='root'.")
        return self._envelope(components)

    def build_scatter_chart(self, title: str, data: dict, icon: str = "") -> dict:
        """Build a scatter chart, optionally with Eisenhower quadrant layout."""
        points = data.get("points", [])
        if not points:
            raise ValueError("scatter_chart requires 'data.points' as array of {x, y, label?, color?}")

        chart_comp = {
            "id": "chart",
            "component": "ScatterChart",
            "points": points,
            "xLabel": data.get("x_label", data.get("xLabel", "X")),
            "yLabel": data.get("y_label", data.get("yLabel", "Y")),
            "title": title or data.get("title", ""),
        }

        # Optional axis range
        for key in ("xMin", "xMax", "yMin", "yMax", "x_min", "x_max", "y_min", "y_max"):
            camel = key.replace("_m", "M").replace("_M", "M")
            if key in data:
                chart_comp[camel] = data[key]

        # Quadrant labels (Eisenhower matrix style)
        quadrants = data.get("quadrants")
        if quadrants:
            chart_comp["quadrants"] = quadrants

        components = [
            chart_comp,
            {"id": "root", "component": "Card", "child": "chart"},
        ]

        return self._envelope(components)

    def build_bar_chart(self, title: str, data: dict, icon: str = "") -> dict:
        """Build a bar chart (vertical or horizontal)."""
        bars = data.get("bars", data.get("items", []))
        if not bars:
            raise ValueError("bar_chart requires 'data.bars' as array of {label, value} or dict {label: value}")

        chart_comp = {
            "id": "chart",
            "component": "BarChart",
            "bars": bars,
            "title": title or data.get("title", ""),
            "yLabel": data.get("y_label", data.get("yLabel", "Value")),
        }

        if data.get("horizontal"):
            chart_comp["horizontal"] = True

        components = [
            chart_comp,
            {"id": "root", "component": "Card", "child": "chart"},
        ]

        return self._envelope(components)

    def _safe_float(self, v):
        """Convert a value to float, preserving None/null for ECharts gap handling."""
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return v

    def build_line_chart(self, title: str, data: dict, icon: str = "") -> dict:
        """Build a line chart with premium styling.
        
        Supports:
          - logarithmic: true → log-scale Y-axis
          - Per-series color, width, style ("dashed"/"dotted")
          - null values in series for gaps (discontinuous lines)
          - data-level mark_line: {label, y_value} for reference lines
          - area: true per-series or data-level for area fill
          - mark_line per-series: {average: true} or {value: N, label: "..."}
        """
        labels = data.get("labels", [])
        series_data = data.get("series", [])
        colors = self.CHART_COLORS
        show_labels = data.get("show_labels", False)
        label_prefix = data.get("label_format", {}).get("prefix", "") if isinstance(data.get("label_format"), dict) else ""
        label_suffix = data.get("label_format", {}).get("suffix", "") if isinstance(data.get("label_format"), dict) else ""
        is_log = data.get("logarithmic", False)

        if not series_data and data.get("values"):
            series_data = [{"name": title or "Value", "values": data["values"]}]

        echart_series = []
        for i, s in enumerate(series_data):
            name = s.get("name", f"Series {i+1}") if isinstance(s, dict) else f"Series {i+1}"
            vals = s.get("values", s) if isinstance(s, dict) else s
            # Map values to floats, preserving None for gaps
            if isinstance(vals, list):
                vals = [self._safe_float(v) for v in vals]
            
            series_type = s.get("type", "line") if isinstance(s, dict) else "line"
            is_area = s.get("area", False) if isinstance(s, dict) else False

            # Per-series color override (fall back to palette rotation)
            series_color = (s.get("color") if isinstance(s, dict) else None) or colors[i % len(colors)]
            # Per-series line width override
            series_width = (s.get("width") if isinstance(s, dict) else None) or (3 if i == 0 else 2)
            # Per-series line style: "solid", "dashed", "dotted"
            line_style_type = (s.get("style") if isinstance(s, dict) else None) or "solid"

            series_entry = {
                "type": series_type,
                "name": name,
                "data": vals,
                "smooth": data.get("smooth", True),
                "connectNulls": False,  # Respect null gaps
                "itemStyle": {"color": series_color},
                "lineStyle": {"width": series_width, "type": line_style_type},
            }

            # Premium area fill
            if (is_area or data.get("area")) and series_type == "line":
                series_entry["areaStyle"] = {
                    "opacity": 0.1,
                    "color": {
                        "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                        "colorStops": [
                            {"offset": 0, "color": series_color},
                            {"offset": 1, "color": "rgba(255, 255, 255, 0)"}
                        ]
                    }
                }

            # Data labels
            if show_labels or (isinstance(s, dict) and s.get("show_labels")):
                label_config = {"show": True, "position": "top", "fontSize": 10}
                if label_prefix or label_suffix:
                    label_config["formatter"] = f"{label_prefix}{{c}}{label_suffix}"
                series_entry["label"] = label_config

            # Mark lines per-series (e.g., reference/average lines)
            if isinstance(s, dict) and s.get("mark_line"):
                ml = s["mark_line"]
                mark_data = []
                if isinstance(ml, dict):
                    if ml.get("average"):
                        mark_data.append({"type": "average", "name": "Average"})
                    if ml.get("value") is not None:
                        mark_data.append({"yAxis": ml["value"], "name": ml.get("label", "")})
                series_entry["markLine"] = {"data": mark_data, "silent": True}

            echart_series.append(series_entry)

        # Data-level mark_line (applied to first series)
        data_mark_line = data.get("mark_line")
        if data_mark_line and isinstance(data_mark_line, dict) and echart_series:
            mark_data = []
            if data_mark_line.get("y_value") is not None:
                mark_data.append({
                    "yAxis": data_mark_line["y_value"],
                    "name": data_mark_line.get("label", ""),
                    "lineStyle": {"type": "dashed", "color": "#fbbf24", "width": 2},
                    "label": {"formatter": data_mark_line.get("label", ""), "position": "end"}
                })
            if data_mark_line.get("average"):
                mark_data.append({"type": "average", "name": "Average"})
            if mark_data:
                if "markLine" not in echart_series[0]:
                    echart_series[0]["markLine"] = {"data": [], "silent": True}
                echart_series[0]["markLine"]["data"].extend(mark_data)

        # Y-axis: logarithmic or value
        y_axis_type = "log" if is_log else "value"
        y_axis = {"type": y_axis_type, "name": data.get("y_label", "")}
        if is_log:
            y_axis["min"] = "dataMin"
            y_axis["logBase"] = 10

        options = {
            "title": {"text": title, "padding": [5, 0, 15, 0]},
            "xAxis": {"type": "category", "data": labels},
            "yAxis": y_axis,
            "series": echart_series,
            "tooltip": {"trigger": "axis"},
            "legend": {"show": len(echart_series) > 1, "top": 35, "right": 10},
            "grid": {"left": 60, "right": 20, "top": 90, "bottom": 60 if len(echart_series) > 1 else 40},
        }

        # Y-axis formatting
        if label_prefix or label_suffix:
            options["yAxis"]["axisLabel"] = {"formatter": f"{label_prefix}{{value}}{label_suffix}"}

        chart_comp = {"id": "chart", "component": "EChart", "options": options}
        components = [chart_comp, {"id": "root", "component": "Card", "child": "chart"}]
        return self._envelope(components)

    def build_area_chart(self, title: str, data: dict, icon: str = "") -> dict:
        """Build an area chart (line with filled area).
        
        Supports same advanced features as line_chart:
          - logarithmic, per-series color/style, null gaps
        """
        labels = data.get("labels", [])
        series_data = data.get("series", [])
        colors = ['#6366f1', '#06b6d4', '#10b981', '#f59e0b', '#ef4444']
        is_log = data.get("logarithmic", False)

        if not series_data and data.get("values"):
            series_data = [{"name": title or "Value", "values": data["values"]}]

        echart_series = []
        for i, s in enumerate(series_data):
            name = s.get("name", f"Series {i+1}") if isinstance(s, dict) else f"Series {i+1}"
            vals = s.get("values", s) if isinstance(s, dict) else s
            if isinstance(vals, list):
                vals = [self._safe_float(v) for v in vals]
            series_color = (s.get("color") if isinstance(s, dict) else None) or colors[i % len(colors)]
            series_width = (s.get("width") if isinstance(s, dict) else None) or 3
            line_style_type = (s.get("style") if isinstance(s, dict) else None) or "solid"
            echart_series.append({
                "type": "line",
                "name": name,
                "data": vals,
                "smooth": True,
                "connectNulls": False,
                "itemStyle": {"color": series_color},
                "areaStyle": {"opacity": 0.1, "color": series_color},
                "lineStyle": {"width": series_width, "type": line_style_type},
            })

        y_axis_type = "log" if is_log else "value"
        y_axis = {"type": y_axis_type, "name": data.get("y_label", "")}
        if is_log:
            y_axis["min"] = "dataMin"
            y_axis["logBase"] = 10

        options = {
            "title": {"text": title, "padding": [5, 0, 15, 0]},
            "xAxis": {"type": "category", "data": labels, "boundaryGap": False},
            "yAxis": y_axis,
            "series": echart_series,
            "legend": {"show": len(echart_series) > 1, "top": 35, "right": 10},
            "grid": {"left": 50, "right": 20, "top": 90, "bottom": 60 if len(echart_series) > 1 else 40},
        }

        chart_comp = {"id": "chart", "component": "EChart", "options": options}
        components = [chart_comp, {"id": "root", "component": "Card", "child": "chart"}]
        return self._envelope(components)

    def build_pie_chart(self, title: str, data: dict, icon: str = "") -> dict:
        """Build a pie/donut chart. data: {slices: [{name, value}], donut: bool}"""
        slices = data.get("slices", data.get("segments", []))
        if not slices:
            raise ValueError("pie_chart requires 'data.slices' as array of {name, value}")

        is_donut = data.get("donut", False)
        radius = ["40%", "70%"] if is_donut else [0, "70%"]

        colors = self.CHART_COLORS + ['#6366f1', '#34d399', '#f87171']

        options = {
            "title": {"text": title, "left": "center", "padding": [5, 0, 15, 0]},
            "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
            "legend": {"orient": "horizontal", "bottom": 10},
            "series": [{
                "type": "pie",
                "radius": radius,
                "data": [{"name": s.get("name", f"Slice {i+1}"), "value": s.get("value", 0)}
                         for i, s in enumerate(slices)],
                "emphasis": {"itemStyle": {"shadowBlur": 10, "shadowColor": "rgba(0,0,0,0.3)"}},
                "label": {"color": "rgba(255,255,255,0.7)"},
                "itemStyle": {"borderColor": "rgba(15,15,30,0.9)", "borderWidth": 2},
            }],
            "color": colors[:len(slices)],
        }

        chart_comp = {"id": "chart", "component": "EChart", "options": options}
        components = [chart_comp, {"id": "root", "component": "Card", "child": "chart"}]
        return self._envelope(components)

    def build_radar_chart(self, title: str, data: dict, icon: str = "") -> dict:
        """Build a radar chart. data: {indicators: [{name, max}], series: [{name, values: []}]}"""
        indicators = data.get("indicators", [])
        series_data = data.get("series", [])
        if not indicators:
            raise ValueError("radar_chart requires 'data.indicators' as array of {name, max}")

        colors = ['#6366f1', '#06b6d4', '#10b981', '#f59e0b']
        radar_series = []
        for i, s in enumerate(series_data):
            color = colors[i % len(colors)]
            radar_series.append({
                "name": s.get("name", f"Series {i+1}"),
                "value": s.get("values", []),
                "areaStyle": {"opacity": 0.1},
                "lineStyle": {"color": color, "width": 2},
                "itemStyle": {"color": color},
            })

        options = {
            "title": {"text": title, "left": "center", "padding": [5, 0, 15, 0]},
            "legend": {"bottom": 10},
            "radar": {
                "indicator": [{"name": ind.get("name", ""), "max": ind.get("max", 100)}
                              for ind in indicators],
                "shape": "polygon",
                "splitArea": {"areaStyle": {"color": ["rgba(99,102,241,0.02)", "rgba(99,102,241,0.05)"]}},
                "axisLine": {"lineStyle": {"color": "rgba(255,255,255,0.15)"}},
                "splitLine": {"lineStyle": {"color": "rgba(255,255,255,0.08)"}},
                "axisName": {"color": "rgba(255,255,255,0.5)"},
            },
            "series": [{"type": "radar", "data": radar_series}],
            "color": colors,
        }

        chart_comp = {"id": "chart", "component": "EChart", "options": options}
        components = [chart_comp, {"id": "root", "component": "Card", "child": "chart"}]
        return self._envelope(components)

    def build_gauge_chart(self, title: str, data: dict, icon: str = "") -> dict:
        """Build a gauge chart. data: {value, min?, max?, unit?}"""
        value = data.get("value", 0)
        min_val = data.get("min", 0)
        max_val = data.get("max", 100)
        unit = data.get("unit", "%")

        options = {
            "title": {"text": title, "left": "center"},
            "series": [{
                "type": "gauge",
                "min": min_val,
                "max": max_val,
                "progress": {"show": True, "width": 14},
                "axisLine": {"lineStyle": {"width": 14, "color": [
                    [0.3, "#ef4444"], [0.7, "#f59e0b"], [1, "#10b981"]
                ]}},
                "axisTick": {"show": False},
                "splitLine": {"length": 10, "lineStyle": {"width": 2, "color": "rgba(255,255,255,0.2)"}},
                "axisLabel": {"distance": 20, "color": "rgba(255,255,255,0.4)", "fontSize": 11},
                "pointer": {"itemStyle": {"color": "#6366f1"}},
                "detail": {
                    "valueAnimation": True,
                    "formatter": f"{{value}}{unit}",
                    "color": "rgba(255,255,255,0.8)",
                    "fontSize": 22,
                    "offsetCenter": [0, "70%"],
                },
                "data": [{"value": value}],
            }],
        }

        chart_comp = {"id": "chart", "component": "EChart", "options": options}
        components = [chart_comp, {"id": "root", "component": "Card", "child": "chart"}]
        return self._envelope(components)

    def build_funnel_chart(self, title: str, data: dict, icon: str = "") -> dict:
        """Build a funnel chart. data: {stages: [{name, value}]}"""
        stages = data.get("stages", data.get("items", []))
        if not stages:
            raise ValueError("funnel_chart requires 'data.stages' as array of {name, value}")

        colors = ['#6366f1', '#8b5cf6', '#06b6d4', '#10b981', '#f59e0b', '#ef4444']

        options = {
            "title": {"text": title, "left": "center"},
            "tooltip": {"trigger": "item", "formatter": "{b}: {c}"},
            "legend": {"bottom": "bottom"},
            "series": [{
                "type": "funnel",
                "left": "10%",
                "top": 50,
                "bottom": 50,
                "width": "80%",
                "min": 0,
                "max": max(s.get("value", 0) for s in stages) if stages else 100,
                "sort": "descending",
                "gap": 2,
                "label": {"show": True, "position": "inside", "color": "rgba(255,255,255,0.85)"},
                "itemStyle": {"borderColor": "rgba(15,15,30,0.9)", "borderWidth": 1},
                "data": [{"name": s.get("name", ""), "value": s.get("value", 0)} for s in stages],
            }],
            "color": colors[:len(stages)],
        }

        chart_comp = {"id": "chart", "component": "EChart", "options": options}
        components = [chart_comp, {"id": "root", "component": "Card", "child": "chart"}]
        return self._envelope(components)

    def build_treemap_chart(self, title: str, data: dict, icon: str = "") -> dict:
        """Build a treemap chart. data: {items: [{name, value, children?: [...]}]}"""
        items = data.get("items", data.get("children", []))
        if not items:
            raise ValueError("treemap_chart requires 'data.items' as array of {name, value}")

        colors = ['#6366f1', '#06b6d4', '#10b981', '#f59e0b', '#ef4444',
                  '#8b5cf6', '#22d3ee', '#34d399']

        options = {
            "title": {"text": title, "left": "center"},
            "tooltip": {"formatter": "{b}: {c}"},
            "series": [{
                "type": "treemap",
                "data": items,
                "roam": False,
                "breadcrumb": {"show": False},
                "label": {"show": True, "color": "rgba(255,255,255,0.85)", "fontSize": 12},
                "itemStyle": {"borderColor": "rgba(15,15,30,0.9)", "borderWidth": 2, "gapWidth": 1},
                "levels": [{
                    "itemStyle": {"borderWidth": 0, "gapWidth": 2},
                }],
            }],
            "color": colors,
        }

        chart_comp = {"id": "chart", "component": "EChart", "options": options}
        components = [chart_comp, {"id": "root", "component": "Card", "child": "chart"}]
        return self._envelope(components)

    def _sanitize_echart_options(self, obj):
        """Recursively fix common LLM formatting mistakes in ECharts options.
        
        LLMs often generate broken formatters like $(value), ${value}, $value
        instead of ECharts' actual placeholders like {c}, {b}, {a}.
        This also strips JS function strings that won't work in JSON.
        """
        import re
        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                if k == "formatter" and isinstance(v, str):
                    # Fix common broken template patterns
                    cleaned = v
                    # Replace $(value), ${value}, $value -> {c} (the data value)
                    cleaned = re.sub(r'\$\(value\)|\$\{value\}|\$value', '{c}', cleaned)
                    # Replace $(name), ${name}, $name -> {b} (category/name)
                    cleaned = re.sub(r'\$\(name\)|\$\{name\}|\$name', '{b}', cleaned)
                    # Replace $(seriesName), ${seriesName} -> {a}
                    cleaned = re.sub(r'\$\(seriesName\)|\$\{seriesName\}', '{a}', cleaned)
                    # Replace $(percent), ${percent} -> {d}
                    cleaned = re.sub(r'\$\(percent\)|\$\{percent\}', '{d}', cleaned)
                    # If it looks like a JS function string, remove it (can't serialize)
                    if cleaned.strip().startswith('function') or cleaned.strip().startswith('('):
                        # Skip function formatters — let ECharts use defaults
                        continue
                    result[k] = cleaned
                else:
                    result[k] = self._sanitize_echart_options(v)
            return result
        elif isinstance(obj, list):
            return [self._sanitize_echart_options(item) for item in obj]
        return obj

    def build_echart(self, title: str, data: dict, icon: str = "") -> dict:
        """Generic EChart passthrough — data.options is raw ECharts option object."""
        options = data.get("options", {})
        if not options:
            raise ValueError("echart type requires 'data.options' with raw ECharts option object")

        # Sanitize common LLM formatting mistakes
        options = self._sanitize_echart_options(options)

        if title and not options.get("title"):
            options["title"] = {"text": title}

        height = data.get("height", "380px")

        chart_comp = {"id": "chart", "component": "EChart", "options": options, "height": height}
        components = [chart_comp, {"id": "root", "component": "Card", "child": "chart"}]
        return self._envelope(components)


# Alias for backward compatibility and test imports
A2UIBuilder = UIGenerator
