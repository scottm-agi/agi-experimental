### a2ui_generate
Generate rich UI components (cards, tables, dashboards, charts) as interactive tiles in the chat.
Use this tool for structured visual output: status cards, data tables, dashboards, or **charts/graphs** (via ECharts).

> **MANDATORY**: For ANY data visualization or charting request, ALWAYS use `a2ui_generate`. NEVER fall back to `code_execution` with matplotlib/seaborn for charts. The A2UI line_chart supports logarithmic scales, multi-series with dashed/dotted styles, null gaps, per-series colors, and reference lines.

Charts are **interactive** — users can hover for tooltips and click to expand fullscreen with zoom/pan/save controls.

**Arguments:**
- `component_type` (required): One of:
  - **Layout:** `info_card`, `data_table`, `status_dashboard`, `action_form`
  - **Charts:** `scatter_chart`, `bar_chart`, `line_chart`, `area_chart`, `pie_chart`, `radar_chart`, `gauge_chart`, `funnel_chart`, `treemap_chart`
- `icon` (optional): Icon name (e.g. "info", "check", "warning", "settings")

> **IMPORTANT**: Always prefer dedicated chart types (`line_chart`, `bar_chart`, `scatter_chart`, etc.) over the generic `echart` type. Dedicated builders handle label formatting, tooltips, themes, and data structure automatically. Only use `echart` for chart types not covered by the dedicated builders. **NEVER use $(value) or ${value} in formatters** — ECharts uses `{c}` for values, `{b}` for names, `{a}` for series names.

---

**Chart Examples:**

1. **Scatter chart** (Eisenhower quadrant):
~~~json
{
    "thoughts": ["User wants prioritization scatter plot"],
    "tool_name": "a2ui_generate",
    "tool_args": {
        "component_type": "scatter_chart",
        "title": "Task Priority Matrix",
        "data": {
            "x_label": "Urgency", "y_label": "Importance",
            "xMax": 100, "yMax": 100,
            "quadrants": {"topRight": "Do First", "topLeft": "Schedule", "bottomRight": "Delegate", "bottomLeft": "Eliminate"},
            "points": [{"x": 85, "y": 90, "label": "Fix bug"}, {"x": 20, "y": 80, "label": "Plan roadmap"}]
        }
    }
}
~~~

2. **Bar chart** (vertical or horizontal):
~~~json
{
    "thoughts": ["Show monthly revenue as bar chart"],
    "tool_name": "a2ui_generate",
    "tool_args": {
        "component_type": "bar_chart",
        "title": "Monthly Revenue",
        "data": {
            "y_label": "Revenue ($K)",
            "bars": [{"label": "Jan", "value": 42}, {"label": "Feb", "value": 58}, {"label": "Mar", "value": 73}]
        }
    }
}
~~~
Set `"horizontal": true` in data for horizontal bars.

3. **Line chart** (single or multi-series, can mix line+bar, supports labels):
~~~json
{
    "thoughts": ["Show BTC price trajectory with EMA as bar overlay"],
    "tool_name": "a2ui_generate",
    "tool_args": {
        "component_type": "line_chart",
        "title": "Bitcoin 15yr Trajectory",
        "data": {
            "labels": ["2011", "2012", "2013", "2014", "2015", "2016", "2017"],
            "show_labels": true,
            "label_format": {"prefix": "$"},
            "y_label": "Price (USD)",
            "series": [
                {"name": "BTC Price", "values": [1, 13, 770, 320, 430, 950, 19000]},
                {"name": "EMA", "values": [1, 7, 200, 260, 345, 650, 5000], "type": "bar", "show_labels": false}
            ]
        }
    }
}
~~~
Series options: `"type": "bar"` to mix bar overlay, `"area": true` for area fill, `"show_labels": true/false` per series, `"mark_line": {"average": true}` for reference lines.

**Advanced line_chart features:**
- `"logarithmic": true` in data → log-scale Y-axis
- Per-series `"color": "#hexcolor"` override
- Per-series `"style": "dashed"` or `"dotted"` for projection/trend lines
- Per-series `"width": 4` for line thickness
- `null` values in series arrays create gaps (discontinuous lines)
- Data-level `"mark_line": {"label": "Baseline", "y_value": 160}` for reference lines

**Advanced example** (multi-series projection with log scale):
~~~json
{
    "thoughts": ["Complex financial projection with 3 scenarios"],
    "tool_name": "a2ui_generate",
    "tool_args": {
        "component_type": "line_chart",
        "title": "XMR 25-Year Price Projection",
        "data": {
            "labels": ["2014", "2017", "2020", "2023", "2026", "2029", "2032", "2035", "2039"],
            "y_label": "Price (USD)",
            "logarithmic": true,
            "label_format": {"prefix": "$"},
            "series": [
                {"name": "Historical", "values": [1.58, 160, 110, 155, 160, null, null, null, null], "color": "#ff6600", "width": 4},
                {"name": "Conservative (+8%)", "values": [null, null, null, null, 160, 201, 253, 319, 435], "color": "#4caf50", "style": "dashed"},
                {"name": "Bullish (+30%)", "values": [null, null, null, null, 160, 351, 772, 1696, 4845], "color": "#2196f3", "style": "dashed", "area": true},
                {"name": "Bearish (-5%)", "values": [null, null, null, null, 160, 137, 117, 100, 82], "color": "#f44336", "style": "dotted"}
            ],
            "mark_line": {"label": "V26 Baseline ($160)", "y_value": 160}
        }
    }
}
~~~

4. **Area chart** (line with filled area — shorthand for line_chart with area:true):
~~~json
{
    "thoughts": ["Show traffic trend with area fill"],
    "tool_name": "a2ui_generate",
    "tool_args": {
        "component_type": "area_chart",
        "title": "Daily Traffic",
        "data": {
            "labels": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "series": [{"name": "Visitors", "values": [820, 932, 901, 1200, 1100]}]
        }
    }
}
~~~

5. **Pie chart** (or donut):
~~~json
{
    "thoughts": ["Show market share breakdown"],
    "tool_name": "a2ui_generate",
    "tool_args": {
        "component_type": "pie_chart",
        "title": "Market Share",
        "data": {
            "donut": true,
            "slices": [{"name": "Chrome", "value": 65}, {"name": "Safari", "value": 18}, {"name": "Firefox", "value": 10}, {"name": "Other", "value": 7}]
        }
    }
}
~~~

6. **Radar chart** (multi-axis comparison):
~~~json
{
    "thoughts": ["Compare product features on radar"],
    "tool_name": "a2ui_generate",
    "tool_args": {
        "component_type": "radar_chart",
        "title": "Product Comparison",
        "data": {
            "indicators": [{"name": "Speed", "max": 100}, {"name": "Reliability", "max": 100}, {"name": "Cost", "max": 100}, {"name": "UX", "max": 100}, {"name": "Support", "max": 100}],
            "series": [
                {"name": "Product A", "values": [90, 80, 60, 85, 70]},
                {"name": "Product B", "values": [70, 90, 80, 60, 90]}
            ]
        }
    }
}
~~~

7. **Gauge chart** (single value indicator):
~~~json
{
    "thoughts": ["Show CPU usage as gauge"],
    "tool_name": "a2ui_generate",
    "tool_args": {
        "component_type": "gauge_chart",
        "title": "CPU Usage",
        "data": {"value": 72, "min": 0, "max": 100, "unit": "%"}
    }
}
~~~

8. **Funnel chart** (conversion pipeline):
~~~json
{
    "thoughts": ["Show sales funnel stages"],
    "tool_name": "a2ui_generate",
    "tool_args": {
        "component_type": "funnel_chart",
        "title": "Sales Pipeline",
        "data": {
            "stages": [{"name": "Leads", "value": 1200}, {"name": "Qualified", "value": 800}, {"name": "Proposal", "value": 400}, {"name": "Negotiation", "value": 200}, {"name": "Closed", "value": 80}]
        }
    }
}
~~~

9. **Treemap chart** (hierarchical proportions):
~~~json
{
    "thoughts": ["Show disk usage as treemap"],
    "tool_name": "a2ui_generate",
    "tool_args": {
        "component_type": "treemap_chart",
        "title": "Disk Usage",
        "data": {
            "items": [{"name": "Documents", "value": 4500}, {"name": "Photos", "value": 3200}, {"name": "Videos", "value": 8100}, {"name": "Apps", "value": 2700}, {"name": "System", "value": 1500}]
        }
    }
}
~~~

10. **Generic EChart** (any ECharts type — raw options passthrough):
~~~json
{
    "thoughts": ["Custom chart using raw ECharts options"],
    "tool_name": "a2ui_generate",
    "tool_args": {
        "component_type": "echart",
        "title": "Custom Visualization",
        "data": {
            "options": {
                "xAxis": {"type": "category", "data": ["A", "B", "C"]},
                "yAxis": {"type": "value"},
                "series": [{"type": "bar", "data": [10, 20, 30]}]
            }
        }
    }
}
~~~

---

**Layout Examples:**

Info card:
~~~json
{"tool_name": "a2ui_generate", "tool_args": {"component_type": "info_card", "title": "Status", "icon": "check", "data": {"body": "All systems operational."}}}
~~~

Data table:
~~~json
{"tool_name": "a2ui_generate", "tool_args": {"component_type": "data_table", "title": "Server Info", "data": {"rows": {"Hostname": "prod-01", "IP": "10.0.1.42", "Uptime": "14d"}}}}
~~~
