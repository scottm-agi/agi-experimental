from python.helpers.tool import Tool, Response
from python.helpers.print_style import PrintStyle
import os
import json
import shutil
import glob


class SystemDashboard(Tool):
    async def execute(self, action: str = "full_dashboard", **kwargs) -> Response:
        """
        Gathers system metrics and returns them as a ready-to-render A2UI payload.
        The agent should output this payload inside a fenced ```a2ui block.
        """
        try:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            projects_dir = os.path.join(base_dir, "usr", "projects")

            # ---------- Token Usage ----------
            token_usage_file = os.path.join(base_dir, "logs", "llm_metrics.jsonl")
            total_tokens_in = 0
            total_tokens_out = 0
            total_cost = 0.0
            model_tokens = {}  # model -> total tokens

            if os.path.exists(token_usage_file):
                with open(token_usage_file, "r") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            record = json.loads(line)
                            pin = record.get("prompt_tokens", 0)
                            pout = record.get("completion_tokens", 0)
                            total_tokens_in += pin
                            total_tokens_out += pout
                            total_cost += record.get("estimated_cost_usd", 0.0)
                            model = record.get("model", "unknown")
                            model_tokens[model] = model_tokens.get(model, 0) + pin + pout
                        except json.JSONDecodeError:
                            continue

            total_tokens = total_tokens_in + total_tokens_out

            # ---------- Projects & Chats ----------
            project_count = 0
            total_chats = 0
            project_chat_counts = {}

            if os.path.exists(projects_dir):
                for d in os.listdir(projects_dir):
                    full = os.path.join(projects_dir, d)
                    if os.path.isdir(full) and not d.startswith("."):
                        project_count += 1
                        chats_dir = os.path.join(full, "chats")
                        chat_count = 0
                        if os.path.isdir(chats_dir):
                            chat_count = len(
                                [f for f in os.listdir(chats_dir) if f.endswith(".json")]
                            )
                        total_chats += chat_count
                        project_chat_counts[d] = chat_count

            # ---------- Disk Usage ----------
            disk = shutil.disk_usage("/")
            disk_pct = round(disk.used / disk.total * 100)

            # ---------- Build A2UI components ----------
            components = []
            children_ids = []

            # Title
            components.append(
                {"id": "title", "component": "Text", "text": "AGIX System Overview", "variant": "h2"}
            )
            children_ids.append("title")
            components.append({"id": "div1", "component": "Divider"})
            children_ids.append("div1")

            # Stat cards row
            stats = [
                ("Total Projects", str(project_count), "PROJECTS"),
                ("Total Chats", str(total_chats), "CHATS"),
                ("Tokens Used", f"{total_tokens:,}", "TOKENS"),
                ("Estimated Cost", f"${total_cost:.2f}", "COST"),
                ("Disk Usage", f"{disk_pct}%", "DISK"),
            ]

            stat_row_children = []
            for i, (label, value, cap) in enumerate(stats):
                val_id = f"sv_{i}"
                cap_id = f"sc_{i}"
                col_id = f"scol_{i}"
                components.append({"id": val_id, "component": "Text", "text": value, "variant": "h1"})
                components.append({"id": cap_id, "component": "Text", "text": cap, "variant": "caption"})
                components.append(
                    {"id": col_id, "component": "Column", "children": [val_id, cap_id], "align": "center"}
                )
                stat_row_children.append(col_id)

            components.append(
                {"id": "stats_row", "component": "Row", "children": stat_row_children, "justify": "spaceAround"}
            )
            children_ids.append("stats_row")

            # Divider before details
            components.append({"id": "div2", "component": "Divider"})
            children_ids.append("div2")

            # Model breakdown rows
            if model_tokens:
                components.append(
                    {"id": "model_title", "component": "Text", "text": "Token Usage by Model", "variant": "h3"}
                )
                children_ids.append("model_title")

                sorted_models = sorted(model_tokens.items(), key=lambda x: x[1], reverse=True)[:5]
                for j, (model, tokens) in enumerate(sorted_models):
                    name_id = f"mn_{j}"
                    tok_id = f"mt_{j}"
                    row_id = f"mr_{j}"
                    components.append({"id": name_id, "component": "Text", "text": model, "variant": "body"})
                    components.append(
                        {"id": tok_id, "component": "Text", "text": f"{tokens:,} tokens", "variant": "body"}
                    )
                    components.append(
                        {"id": row_id, "component": "Row", "children": [name_id, tok_id], "justify": "spaceBetween"}
                    )
                    children_ids.append(row_id)

            # Project breakdown rows (top 5)
            if project_chat_counts:
                components.append({"id": "div3", "component": "Divider"})
                children_ids.append("div3")
                components.append(
                    {"id": "proj_title", "component": "Text", "text": "Chats by Project", "variant": "h3"}
                )
                children_ids.append("proj_title")

                sorted_projects = sorted(project_chat_counts.items(), key=lambda x: x[1], reverse=True)[:5]
                for k, (proj, count) in enumerate(sorted_projects):
                    pn_id = f"pn_{k}"
                    pc_id = f"pc_{k}"
                    pr_id = f"pr_{k}"
                    components.append({"id": pn_id, "component": "Text", "text": proj, "variant": "body"})
                    components.append(
                        {"id": pc_id, "component": "Text", "text": f"{count} chats", "variant": "body"}
                    )
                    components.append(
                        {"id": pr_id, "component": "Row", "children": [pn_id, pc_id], "justify": "spaceBetween"}
                    )
                    children_ids.append(pr_id)

            # ---------- Recent Agent Work Summary (Issue #795) ----------
            recent_work = []
            all_chat_files = sorted(
                glob.glob(os.path.join(projects_dir, "*/chats/*.json")),
                key=os.path.getmtime,
                reverse=True,
            )[:15]

            for cf in all_chat_files:
                try:
                    with open(cf) as f:
                        cdata = json.load(f)
                    chat_name = cdata.get("name", os.path.basename(cf).replace(".json", ""))
                    parts = cf.replace(projects_dir + "/", "").split("/")
                    project_name = parts[0] if parts else "unknown"

                    # Extract last assistant message as outcome summary
                    messages = cdata.get("messages", cdata.get("history", []))
                    outcome = ""
                    for msg in reversed(messages):
                        content = msg.get("content", "")
                        role = msg.get("role", "")
                        if isinstance(content, str) and role == "assistant" and len(content) > 20:
                            outcome = content[:120].replace("\n", " ")
                            break

                    recent_work.append({
                        "chat": chat_name[:35],
                        "project": project_name[:20],
                        "outcome": outcome[:100] if outcome else "—",
                    })
                except Exception:
                    continue

            if recent_work:
                components.append({"id": "div_rw", "component": "Divider"})
                children_ids.append("div_rw")
                components.append({
                    "id": "rw_table",
                    "component": "DataTable",
                    "title": "Recent Agent Work",
                    "columns": [
                        {"name": "chat", "header": "Chat"},
                        {"name": "project", "header": "Project"},
                        {"name": "outcome", "header": "Outcome"},
                    ],
                    "rows": recent_work[:10],
                })
                children_ids.append("rw_table")

            # ---------- Deliverables (Issue #795) ----------
            deliverables = []
            for md_file in sorted(
                glob.glob(os.path.join(projects_dir, "*/deliverables/*.md")),
                key=os.path.getmtime,
                reverse=True,
            )[:10]:
                try:
                    with open(md_file) as f:
                        content = f.read(500)
                    parts = md_file.replace(projects_dir + "/", "").split("/")
                    proj = parts[0] if parts else "unknown"

                    title = os.path.basename(md_file).replace(".md", "")
                    agent = "—"
                    if content.startswith("---"):
                        yaml_parts = content.split("---", 2)
                        if len(yaml_parts) >= 3:
                            try:
                                import yaml
                                meta = yaml.safe_load(yaml_parts[1])
                                if meta:
                                    title = meta.get("title", title)
                                    agent = meta.get("agent", agent)
                            except Exception:
                                pass

                    deliverables.append({
                        "file": os.path.basename(md_file)[:30],
                        "project": proj[:20],
                        "title": title[:40],
                        "agent": agent[:15],
                    })
                except Exception:
                    continue

            if deliverables:
                components.append({"id": "div_del", "component": "Divider"})
                children_ids.append("div_del")
                components.append({
                    "id": "del_table",
                    "component": "DataTable",
                    "title": "Recent Deliverables",
                    "columns": [
                        {"name": "title", "header": "Title"},
                        {"name": "project", "header": "Project"},
                        {"name": "agent", "header": "Agent"},
                        {"name": "file", "header": "File"},
                    ],
                    "rows": deliverables,
                })
                children_ids.append("del_table")

            # ---------- Activity Timeline (Issue #795) ----------
            from collections import Counter
            from datetime import datetime
            day_counts = Counter()
            for cf in all_chat_files:
                try:
                    mtime = os.path.getmtime(cf)
                    day = datetime.fromtimestamp(mtime).strftime("%m/%d")
                    day_counts[day] += 1
                except Exception:
                    pass

            if day_counts:
                timeline_data = [
                    {"name": day, "value": count}
                    for day, count in sorted(day_counts.items())[-14:]  # Last 14 days
                ]
                if len(timeline_data) > 1:
                    components.append({"id": "div_tl", "component": "Divider"})
                    children_ids.append("div_tl")
                    components.append({
                        "id": "timeline",
                        "component": "LineChart",
                        "title": "Chat Activity (last 14 days)",
                        "data": timeline_data,
                    })
                    children_ids.append("timeline")

            # Main column + root card
            components.append({"id": "main_col", "component": "Column", "children": children_ids})
            components.append({"id": "root", "component": "Card", "child": "main_col"})

            # Build the final A2UI payload
            a2ui_payload = {
                "messages": [
                    {"updateComponents": {"components": components}}
                ]
            }

            payload_json = json.dumps(a2ui_payload, indent=2)

            result = f"""Here is the A2UI payload. Output it exactly inside a fenced a2ui block:

```a2ui
{payload_json}
```"""
            return Response(message=result, break_loop=False)

        except Exception as e:
            return Response(message=f"Error generating dashboard: {str(e)}", break_loop=False)
