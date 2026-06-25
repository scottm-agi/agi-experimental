/**
 * A2UI Task/Project Plan Components (Issue #817)
 *
 * Separate module containing TaskPlan, Gantt, DependencyGraph, and Kanban
 * component renderers. Kept modular to avoid the 1500-line limit on the
 * main renderer file.
 *
 * Exports a map of component-type → renderFn(comp, componentMap, dataModel).
 */

// ─── Status colors & icons ──────────────────────────────────────────

const STATUS_CONFIG = {
    done:        { color: '#22c55e', icon: '✅', bg: 'rgba(34,197,94,0.12)' },
    complete:    { color: '#22c55e', icon: '✅', bg: 'rgba(34,197,94,0.12)' },
    in_progress: { color: '#f59e0b', icon: '🔨', bg: 'rgba(245,158,11,0.12)' },
    active:      { color: '#f59e0b', icon: '🔨', bg: 'rgba(245,158,11,0.12)' },
    pending:     { color: '#6b7280', icon: '⏳', bg: 'rgba(107,114,128,0.10)' },
    blocked:     { color: '#ef4444', icon: '🚫', bg: 'rgba(239,68,68,0.12)' },
    cancelled:   { color: '#9ca3af', icon: '❌', bg: 'rgba(156,163,175,0.10)' },
};

function statusOf(s) {
    return STATUS_CONFIG[String(s).toLowerCase()] || STATUS_CONFIG.pending;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ─── TaskPlan ───────────────────────────────────────────────────────

function renderTaskPlan(comp) {
    const tasks = comp.tasks || [];
    const title = comp.title || 'Task Plan';

    const container = document.createElement('div');
    container.className = 'a2ui-task-plan';

    // Header with progress
    const header = document.createElement('div');
    header.className = 'a2ui-task-plan__header';

    const h3 = document.createElement('h3');
    h3.textContent = title;
    header.appendChild(h3);

    const doneCount = tasks.filter(t => ['done', 'complete'].includes(String(t.status).toLowerCase())).length;
    const progressText = document.createElement('span');
    progressText.className = 'a2ui-task-plan__progress';
    progressText.textContent = `${doneCount}/${tasks.length} complete`;
    header.appendChild(progressText);
    container.appendChild(header);

    // Progress bar
    const progressBar = document.createElement('div');
    progressBar.className = 'a2ui-task-plan__bar';
    const fill = document.createElement('div');
    fill.className = 'a2ui-task-plan__bar-fill';
    fill.style.width = `${tasks.length > 0 ? (doneCount / tasks.length) * 100 : 0}%`;
    progressBar.appendChild(fill);
    container.appendChild(progressBar);

    // Task list
    const list = document.createElement('div');
    list.className = 'a2ui-task-plan__list';

    tasks.forEach(task => {
        const st = statusOf(task.status);
        const row = document.createElement('div');
        row.className = 'a2ui-task-plan__item';
        row.style.borderLeftColor = st.color;

        const icon = document.createElement('span');
        icon.className = 'a2ui-task-plan__icon';
        icon.textContent = st.icon;
        row.appendChild(icon);

        const info = document.createElement('div');
        info.className = 'a2ui-task-plan__info';

        const name = document.createElement('div');
        name.className = 'a2ui-task-plan__name';
        name.textContent = task.title || task.name || `Task ${task.id}`;
        info.appendChild(name);

        if (task.assignee) {
            const assignee = document.createElement('span');
            assignee.className = 'a2ui-task-plan__assignee';
            assignee.textContent = task.assignee;
            info.appendChild(assignee);
        }

        if (task.depends_on && task.depends_on.length) {
            const deps = document.createElement('span');
            deps.className = 'a2ui-task-plan__deps';
            deps.textContent = `→ ${task.depends_on.join(', ')}`;
            info.appendChild(deps);
        }

        row.appendChild(info);

        const badge = document.createElement('span');
        badge.className = 'a2ui-task-plan__badge';
        badge.style.background = st.bg;
        badge.style.color = st.color;
        badge.textContent = task.status;
        row.appendChild(badge);

        list.appendChild(row);
    });

    container.appendChild(list);
    return container;
}

// ─── Gantt Chart ────────────────────────────────────────────────────

function renderGantt(comp) {
    const items = comp.items || [];
    const title = comp.title || 'Timeline';

    const container = document.createElement('div');
    container.className = 'a2ui-gantt';

    const h3 = document.createElement('h3');
    h3.textContent = title;
    container.appendChild(h3);

    if (items.length === 0) {
        container.innerHTML += '<p class="a2ui-empty">No timeline data.</p>';
        return container;
    }

    // Parse dates to find range
    const dates = items.flatMap(i => {
        const s = new Date(i.start);
        const e = new Date(i.end);
        return [s, e];
    }).filter(d => !isNaN(d));

    const minDate = new Date(Math.min(...dates));
    const maxDate = new Date(Math.max(...dates));
    const rangeDays = Math.max(1, (maxDate - minDate) / (1000 * 60 * 60 * 24));

    const chart = document.createElement('div');
    chart.className = 'a2ui-gantt__chart';

    items.forEach(item => {
        const st = statusOf(item.status);
        const row = document.createElement('div');
        row.className = 'a2ui-gantt__row';

        const label = document.createElement('div');
        label.className = 'a2ui-gantt__label';
        label.textContent = item.task || item.name;
        row.appendChild(label);

        const track = document.createElement('div');
        track.className = 'a2ui-gantt__track';

        const bar = document.createElement('div');
        bar.className = 'a2ui-gantt__bar';

        const start = new Date(item.start);
        const end = new Date(item.end);
        const offsetPct = ((start - minDate) / (1000 * 60 * 60 * 24)) / rangeDays * 100;
        const widthPct = Math.max(2, ((end - start) / (1000 * 60 * 60 * 24)) / rangeDays * 100);

        bar.style.left = `${offsetPct}%`;
        bar.style.width = `${widthPct}%`;
        bar.style.background = st.color;
        bar.title = `${item.task || item.name}: ${item.start} → ${item.end}`;

        track.appendChild(bar);
        row.appendChild(track);
        chart.appendChild(row);
    });

    container.appendChild(chart);
    return container;
}

// ─── Dependency Graph ───────────────────────────────────────────────

function renderDependencyGraph(comp) {
    const nodes = comp.nodes || [];
    const edges = comp.edges || [];
    const title = comp.title || 'Dependencies';

    const container = document.createElement('div');
    container.className = 'a2ui-dep-graph';

    const h3 = document.createElement('h3');
    h3.textContent = title;
    container.appendChild(h3);

    if (nodes.length === 0) {
        container.innerHTML += '<p class="a2ui-empty">No dependency data.</p>';
        return container;
    }

    // Build adjacency info for layout
    const inDegree = {};
    const outEdges = {};
    nodes.forEach(n => { inDegree[n.id] = 0; outEdges[n.id] = []; });
    edges.forEach(e => {
        inDegree[e.to] = (inDegree[e.to] || 0) + 1;
        if (outEdges[e.from]) outEdges[e.from].push(e.to);
    });

    // Topological sort for levels
    const levels = [];
    const visited = new Set();
    const queue = nodes.filter(n => inDegree[n.id] === 0).map(n => n.id);

    while (queue.length > 0) {
        const level = [...queue];
        levels.push(level);
        const next = [];
        for (const id of level) {
            visited.add(id);
            for (const out of (outEdges[id] || [])) {
                inDegree[out]--;
                if (inDegree[out] === 0 && !visited.has(out)) next.push(out);
            }
        }
        queue.length = 0;
        queue.push(...next);
    }

    // Add any unvisited nodes
    const remaining = nodes.filter(n => !visited.has(n.id)).map(n => n.id);
    if (remaining.length) levels.push(remaining);

    const nodeMap = {};
    nodes.forEach(n => { nodeMap[n.id] = n; });

    // Render as layered flow
    const flow = document.createElement('div');
    flow.className = 'a2ui-dep-graph__flow';

    levels.forEach((level, li) => {
        const col = document.createElement('div');
        col.className = 'a2ui-dep-graph__level';

        level.forEach(id => {
            const n = nodeMap[id];
            if (!n) return;
            const st = statusOf(n.status);

            const node = document.createElement('div');
            node.className = 'a2ui-dep-graph__node';
            node.style.borderColor = st.color;
            node.style.background = st.bg;

            const label = document.createElement('div');
            label.className = 'a2ui-dep-graph__node-label';
            label.textContent = n.label || n.id;
            node.appendChild(label);

            const badge = document.createElement('span');
            badge.className = 'a2ui-dep-graph__node-status';
            badge.textContent = st.icon;
            node.appendChild(badge);

            col.appendChild(node);
        });

        flow.appendChild(col);

        // Arrow between levels
        if (li < levels.length - 1) {
            const arrow = document.createElement('div');
            arrow.className = 'a2ui-dep-graph__arrow';
            arrow.textContent = '→';
            flow.appendChild(arrow);
        }
    });

    container.appendChild(flow);

    // Legend
    const legend = document.createElement('div');
    legend.className = 'a2ui-dep-graph__legend';
    edges.forEach(e => {
        const item = document.createElement('span');
        item.className = 'a2ui-dep-graph__edge-label';
        item.textContent = `${e.from} → ${e.to}`;
        legend.appendChild(item);
    });
    container.appendChild(legend);

    return container;
}

// ─── Kanban Board ───────────────────────────────────────────────────

function renderKanban(comp) {
    const columns = comp.columns || [];
    const title = comp.title || 'Board';

    const container = document.createElement('div');
    container.className = 'a2ui-kanban';

    const h3 = document.createElement('h3');
    h3.textContent = title;
    container.appendChild(h3);

    const board = document.createElement('div');
    board.className = 'a2ui-kanban__board';

    columns.forEach(col => {
        const column = document.createElement('div');
        column.className = 'a2ui-kanban__column';

        const colHeader = document.createElement('div');
        colHeader.className = 'a2ui-kanban__col-header';

        const colName = document.createElement('span');
        colName.textContent = col.name;
        colHeader.appendChild(colName);

        const count = document.createElement('span');
        count.className = 'a2ui-kanban__count';
        count.textContent = (col.items || []).length;
        colHeader.appendChild(count);

        column.appendChild(colHeader);

        const colBody = document.createElement('div');
        colBody.className = 'a2ui-kanban__col-body';

        (col.items || []).forEach(item => {
            const card = document.createElement('div');
            card.className = 'a2ui-kanban__card';

            const cardTitle = document.createElement('div');
            cardTitle.className = 'a2ui-kanban__card-title';
            cardTitle.textContent = typeof item === 'string' ? item : (item.title || item.name || item.id);
            card.appendChild(cardTitle);

            if (typeof item === 'object') {
                if (item.assignee) {
                    const assignee = document.createElement('span');
                    assignee.className = 'a2ui-kanban__card-assignee';
                    assignee.textContent = item.assignee;
                    card.appendChild(assignee);
                }
                if (item.priority) {
                    const prio = document.createElement('span');
                    prio.className = `a2ui-kanban__card-priority priority-${String(item.priority).toLowerCase()}`;
                    prio.textContent = item.priority;
                    card.appendChild(prio);
                }
            }

            colBody.appendChild(card);
        });

        column.appendChild(colBody);
        board.appendChild(column);
    });

    container.appendChild(board);
    return container;
}

// ─── Export ──────────────────────────────────────────────────────────

export const taskComponentRenderers = {
    TaskPlan: renderTaskPlan,
    Gantt: renderGantt,
    DependencyGraph: renderDependencyGraph,
    Kanban: renderKanban,
};
