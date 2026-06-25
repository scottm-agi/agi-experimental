/**
 * Tile Renderer
 *
 * Renders JSON payloads into DOM elements.
 * Maps component types from the basic catalog to native HTML elements.
 *
 * This renderer processes the adjacency-list component model: components are
 * a flat list with ID references, and the renderer resolves them recursively
 * from the 'root' component.
 *
 * Spec: https://a2ui.org/specification/v0.9-a2ui/
 */

import { taskComponentRenderers } from './a2ui-task-components.js';

// Material Symbols icon name mapping (A2UI camelCase → Material Symbols snake_case)
const ICON_NAME_MAP = {
    accountCircle: 'account_circle',
    add: 'add',
    arrowBack: 'arrow_back',
    arrowForward: 'arrow_forward',
    attachFile: 'attach_file',
    calendarToday: 'calendar_today',
    call: 'call',
    camera: 'camera',
    check: 'check',
    close: 'close',
    delete: 'delete',
    download: 'download',
    edit: 'edit',
    event: 'event',
    error: 'error',
    fastForward: 'fast_forward',
    favorite: 'favorite',
    favoriteOff: 'heart_broken',
    folder: 'folder',
    help: 'help',
    home: 'home',
    info: 'info',
    locationOn: 'location_on',
    lock: 'lock',
    lockOpen: 'lock_open',
    mail: 'mail',
    menu: 'menu',
    moreVert: 'more_vert',
    moreHoriz: 'more_horiz',
    notificationsOff: 'notifications_off',
    notifications: 'notifications',
    pause: 'pause',
    payment: 'payment',
    person: 'person',
    phone: 'phone',
    photo: 'photo',
    play: 'play_arrow',
    print: 'print',
    refresh: 'refresh',
    rewind: 'fast_rewind',
    search: 'search',
    send: 'send',
    settings: 'settings',
    share: 'share',
    shoppingCart: 'shopping_cart',
    skipNext: 'skip_next',
    skipPrevious: 'skip_previous',
    star: 'star',
    starHalf: 'star_half',
    starOff: 'star_outline',
    stop: 'stop',
    upload: 'upload',
    visibility: 'visibility',
    visibilityOff: 'visibility_off',
    volumeDown: 'volume_down',
    volumeMute: 'volume_mute',
    volumeOff: 'volume_off',
    volumeUp: 'volume_up',
    warning: 'warning',
};

/**
 * Render an A2UI payload into a DOM element.
 *
 * @param {Object} payload - The A2UI payload from the tool response.
 *   Expected format: { messages: [ {createSurface: ...}, {updateComponents: ...}, ...] }
 * @returns {HTMLElement} The rendered tile element.
 */
export function renderA2UITile(payload) {
    const tile = document.createElement('div');
    tile.className = 'a2ui-tile';

    try {
        const messages = payload.messages || [];
        let components = [];
        let dataModel = {};
        let surfaceId = '';

        // Process standard envelope messages
        for (const msg of messages) {
            if (msg.createSurface) {
                surfaceId = msg.createSurface.surfaceId || '';
            }
            if (msg.updateComponents) {
                components = components.concat(msg.updateComponents.components || []);
            }
            if (msg.updateDataModel) {
                dataModel = { ...dataModel, ...(msg.updateDataModel.value || {}) };
            }
        }

        // Handle a2ui_v09 format - can be Array of components or Object with component_type
        if (payload.a2ui_v09) {
            if (Array.isArray(payload.a2ui_v09)) {
                // Array of raw components
                const v09Components = payload.a2ui_v09.map((c, i) => ({
                    ...c,
                    id: c.id || `v09_${i}`,
                    component: c.component || c.component_type || 'Text'
                }));

                v09Components.forEach(c => {
                    // Map common legacy names if not present
                    if (c.component === 'radar_chart') c.component = 'RadarChart';
                    if (c.component === 'pie_chart') c.component = 'PieChart';
                    if (c.component === 'bar_chart') c.component = 'BarChart';
                    if (c.component === 'scatter_chart') c.component = 'ScatterChart';
                });

                components = components.concat(v09Components);
            } else if (typeof payload.a2ui_v09 === 'object') {
                // Object with component_type, title, data — treat as direct component definition
                const v09Obj = payload.a2ui_v09;
                const compType = v09Obj.component_type || v09Obj.component || 'Card';
                const comp = {
                    id: v09Obj.id || 'root',
                    component: compType,
                    ...v09Obj
                };
                // Map legacy chart type names to renderer names
                const typeMap = {
                    'radar_chart': 'RadarChart', 'pie_chart': 'PieChart',
                    'bar_chart': 'BarChart', 'scatter_chart': 'ScatterChart',
                    'line_chart': 'EChart', 'area_chart': 'EChart',
                    'gauge_chart': 'EChart', 'funnel_chart': 'EChart',
                    'treemap_chart': 'EChart', 'echart': 'EChart',
                    'info_card': 'Card', 'data_table': 'Card',
                    'status_dashboard': 'Card', 'action_form': 'Card'
                };
                if (typeMap[comp.component]) comp.component = typeMap[comp.component];
                components = [comp];
            }
        }

        // Final Robustness Check
        if (components.length > 0 && !components.find(c => c.id === 'root')) {
            const first = components[0];
            if (first) first.id = 'root';
        }

        // Robustness: if no messages but payload itself looks like a direct component
        if (components.length === 0 && (payload.component || payload.component_type)) {
            const comp = { ...payload, id: payload.id || 'root', component: payload.component || payload.component_type };
            // Map legacy chart types if needed
            if (comp.component === 'radar_chart') comp.component = 'RadarChart';
            if (comp.component === 'pie_chart') comp.component = 'PieChart';
            if (comp.component === 'bar_chart') comp.component = 'BarChart';
            if (comp.component === 'scatter_chart') comp.component = 'ScatterChart';

            components = [comp];
        }

        if (components.length === 0) {
            tile.innerHTML = '<p class="a2ui-error">No components to render.</p>';
            return tile;
        }

        // Build component map (adjacency list)
        const componentMap = new Map();
        for (const comp of components) {
            if (comp.id) {
                componentMap.set(comp.id, comp);
            }
        }

        // Find root and render
        const rootComp = componentMap.get('root');
        if (!rootComp) {
            tile.innerHTML = '<p class="a2ui-error">No root component found.</p>';
            return tile;
        }

        tile.dataset.surfaceId = surfaceId;
        const rendered = renderComponent(rootComp, componentMap, dataModel);
        if (rendered) {
            tile.appendChild(rendered);
        }

    } catch (err) {
        console.error('[UI] Render error:', err);
        tile.innerHTML = `<p class="a2ui-error">Render error: ${escapeHtml(err.message)}</p>`;
    }

    return tile;
}

/**
 * Render a single component by type.
 */
function renderComponent(comp, componentMap, dataModel) {
    const type = comp.component;
    if (!type) return null;

    const renderers = {
        Card: renderCard,
        Text: renderText,
        Image: renderImage,
        Icon: renderIcon,
        Row: renderRow,
        Column: renderColumn,
        List: renderList,
        Button: renderButton,
        TextField: renderTextField,
        CheckBox: renderCheckBox,
        Divider: renderDivider,
        Slider: renderSlider,
        ChoicePicker: renderChoicePicker,
        Tabs: renderTabs,
        Video: renderVideo,
        AudioPlayer: renderAudioPlayer,
        DateTimeInput: renderDateTimeInput,
        Modal: renderModal,
        ScatterChart: renderScatterChart,
        BarChart: renderBarChart,
        RadarChart: renderRadarChart,
        PieChart: renderPieChart,
        LineChart: renderLineChart,
        EChart: renderEChart,
        DataTable: renderDataTable,
        StatusDashboard: renderStatusDashboard,
        InfoCard: renderInfoCard,
        ...taskComponentRenderers,
    };

    const renderer = renderers[type];
    if (!renderer) {
        console.warn(`[UI] Unknown component type: ${type}`);
        const el = document.createElement('div');
        el.className = 'a2ui-unknown';
        el.textContent = `[${type}]`;
        return el;
    }

    const el = renderer(comp, componentMap, dataModel);
    if (el && comp.id) {
        el.dataset.a2uiId = comp.id;
    }
    return el;
}

/**
 * Resolve a dynamic value (string or data-bound path).
 */
function resolveValue(val, dataModel) {
    if (val === null || val === undefined) return '';
    if (typeof val === 'string') {
        if (val.startsWith('{{') && val.endsWith('}}')) {
            const path = val.slice(2, -2).trim();
            return getByPath(dataModel, path) ?? val;
        }
        return val;
    }
    if (typeof val === 'number' || typeof val === 'boolean') return val;
    if (typeof val === 'object' && val.path) {
        return getByPath(dataModel, val.path) ?? '';
    }
    return String(val);
}

/**
 * Get a value from a nested object by JSON Pointer path.
 */
function getByPath(obj, path) {
    if (!path || path === '/') return obj;
    const parts = path.replace(/^\//, '').split('/');
    let current = obj;
    for (const part of parts) {
        if (current == null) return '';
        current = current[part];
    }
    return current ?? '';
}

/**
 * Resolve children IDs to rendered DOM elements.
 */
function renderChildren(childrenSpec, componentMap, dataModel) {
    if (!childrenSpec) return [];

    // Direct array of IDs
    if (Array.isArray(childrenSpec)) {
        return childrenSpec
            .map(id => {
                const child = componentMap.get(id);
                return child ? renderComponent(child, componentMap, dataModel) : null;
            })
            .filter(Boolean);
    }

    return [];
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}


// ─── Component Renderers ──────────────────────────────────────────────

function renderCard(comp, componentMap, dataModel) {
    const el = document.createElement('div');
    el.className = 'a2ui-card';

    if (comp.child) {
        const childComp = componentMap.get(comp.child);
        if (childComp) {
            const childEl = renderComponent(childComp, componentMap, dataModel);
            if (childEl) el.appendChild(childEl);
        }
    }
    return el;
}

function renderText(comp, _componentMap, dataModel) {
    const text = resolveValue(comp.text, dataModel);
    const variant = comp.variant || 'body';

    const tagMap = {
        h1: 'h1', h2: 'h2', h3: 'h3', h4: 'h4', h5: 'h5',
        caption: 'span', body: 'p',
    };

    const tag = tagMap[variant] || 'p';
    const el = document.createElement(tag);
    el.className = `a2ui-text a2ui-text--${variant}`;
    el.textContent = String(text);
    return el;
}

function renderImage(comp, _componentMap, dataModel) {
    const url = resolveValue(comp.url, dataModel);
    const el = document.createElement('img');
    el.className = 'a2ui-image';
    el.src = url;
    el.alt = comp.alt || '';
    if (comp.fit) {
        el.style.objectFit = comp.fit === 'scaleDown' ? 'scale-down' : comp.fit;
    }
    if (comp.variant) {
        el.classList.add(`a2ui-image--${comp.variant}`);
    }
    return el;
}

function renderIcon(comp, _componentMap, dataModel) {
    const name = typeof comp.name === 'string' ? comp.name : resolveValue(comp.name, dataModel);
    const mappedName = ICON_NAME_MAP[name] || name;
    const el = document.createElement('span');
    el.className = 'a2ui-icon material-symbols-outlined';
    el.textContent = mappedName;
    return el;
}

function renderRow(comp, componentMap, dataModel) {
    const el = document.createElement('div');
    el.className = 'a2ui-row';

    if (comp.justify) el.dataset.justify = comp.justify;
    if (comp.align) el.dataset.align = comp.align;

    const children = renderChildren(comp.children, componentMap, dataModel);
    for (const child of children) {
        el.appendChild(child);
    }
    return el;
}

function renderColumn(comp, componentMap, dataModel) {
    const el = document.createElement('div');
    el.className = 'a2ui-col';

    if (comp.justify) el.dataset.justify = comp.justify;
    if (comp.align) el.dataset.align = comp.align;

    const children = renderChildren(comp.children, componentMap, dataModel);
    for (const child of children) {
        el.appendChild(child);
    }
    return el;
}

function renderList(comp, componentMap, dataModel) {
    const el = document.createElement('div');
    el.className = 'a2ui-list';
    if (comp.direction === 'horizontal') {
        el.classList.add('a2ui-list--horizontal');
    }

    const children = renderChildren(comp.children, componentMap, dataModel);
    for (const child of children) {
        el.appendChild(child);
    }
    return el;
}

function renderButton(comp, componentMap, dataModel) {
    const el = document.createElement('button');
    el.className = 'a2ui-btn';
    if (comp.variant) {
        el.classList.add(`a2ui-btn--${comp.variant}`);
    }

    if (comp.child) {
        const childComp = componentMap.get(comp.child);
        if (childComp) {
            const childEl = renderComponent(childComp, componentMap, dataModel);
            if (childEl) el.appendChild(childEl);
        }
    }

    // Button click handler (emit custom event)
    if (comp.action && comp.action.event) {
        el.addEventListener('click', () => {
            const event = new CustomEvent('a2ui-action', {
                bubbles: true,
                detail: {
                    eventName: comp.action.event.name,
                    context: comp.action.event.context || {},
                },
            });
            el.dispatchEvent(event);
        });
    }

    return el;
}

function renderTextField(comp, _componentMap, dataModel) {
    const wrapper = document.createElement('div');
    wrapper.className = 'a2ui-textfield';

    const label = resolveValue(comp.label, dataModel);
    const value = resolveValue(comp.value, dataModel);
    const variant = comp.variant || 'shortText';

    const labelEl = document.createElement('label');
    labelEl.className = 'a2ui-textfield__label';
    labelEl.textContent = label;
    wrapper.appendChild(labelEl);

    if (variant === 'longText') {
        const textarea = document.createElement('textarea');
        textarea.className = 'a2ui-textfield__input a2ui-textfield__textarea';
        textarea.value = value;
        textarea.placeholder = label;
        textarea.rows = 3;
        wrapper.appendChild(textarea);
    } else {
        const input = document.createElement('input');
        input.className = 'a2ui-textfield__input';
        input.value = value;
        input.placeholder = label;
        if (variant === 'number') input.type = 'number';
        else if (variant === 'obscured') input.type = 'password';
        else input.type = 'text';
        wrapper.appendChild(input);
    }

    return wrapper;
}

function renderCheckBox(comp, _componentMap, dataModel) {
    const wrapper = document.createElement('label');
    wrapper.className = 'a2ui-checkbox';

    const input = document.createElement('input');
    input.type = 'checkbox';
    input.className = 'a2ui-checkbox__input';
    const val = resolveValue(comp.value, dataModel);
    input.checked = val === true || val === 'true';
    wrapper.appendChild(input);

    const labelText = resolveValue(comp.label, dataModel);
    const span = document.createElement('span');
    span.className = 'a2ui-checkbox__label';
    span.textContent = labelText;
    wrapper.appendChild(span);

    return wrapper;
}

function renderDivider(comp) {
    const el = document.createElement('hr');
    el.className = 'a2ui-divider';
    if (comp.axis === 'vertical') {
        el.classList.add('a2ui-divider--vertical');
    }
    return el;
}

function renderSlider(comp, _componentMap, dataModel) {
    const wrapper = document.createElement('div');
    wrapper.className = 'a2ui-slider';

    const label = resolveValue(comp.label, dataModel);
    if (label) {
        const labelEl = document.createElement('label');
        labelEl.className = 'a2ui-slider__label';
        labelEl.textContent = label;
        wrapper.appendChild(labelEl);
    }

    const input = document.createElement('input');
    input.type = 'range';
    input.className = 'a2ui-slider__input';
    input.min = comp.min ?? 0;
    input.max = comp.max ?? 100;
    input.value = resolveValue(comp.value, dataModel);
    wrapper.appendChild(input);

    return wrapper;
}

function renderChoicePicker(comp, _componentMap, dataModel) {
    const wrapper = document.createElement('div');
    wrapper.className = 'a2ui-choice-picker';

    const label = resolveValue(comp.label, dataModel);
    if (label) {
        const labelEl = document.createElement('div');
        labelEl.className = 'a2ui-choice-picker__label';
        labelEl.textContent = label;
        wrapper.appendChild(labelEl);
    }

    const options = comp.options || [];
    const selectedValues = resolveValue(comp.value, dataModel) || [];

    for (const opt of options) {
        const optLabel = resolveValue(opt.label, dataModel);
        const optValue = opt.value || '';
        const isSelected = Array.isArray(selectedValues)
            ? selectedValues.includes(optValue)
            : selectedValues === optValue;

        const chip = document.createElement('span');
        chip.className = `a2ui-choice-picker__chip${isSelected ? ' a2ui-choice-picker__chip--selected' : ''}`;
        chip.textContent = optLabel;
        chip.dataset.value = optValue;
        wrapper.appendChild(chip);
    }

    return wrapper;
}

function renderTabs(comp, componentMap, dataModel) {
    const wrapper = document.createElement('div');
    wrapper.className = 'a2ui-tabs';

    const tabs = comp.tabs || [];
    const tabBar = document.createElement('div');
    tabBar.className = 'a2ui-tabs__bar';

    const tabPanels = document.createElement('div');
    tabPanels.className = 'a2ui-tabs__panels';

    tabs.forEach((tab, i) => {
        const tabBtn = document.createElement('button');
        tabBtn.className = `a2ui-tabs__tab${i === 0 ? ' a2ui-tabs__tab--active' : ''}`;
        tabBtn.textContent = resolveValue(tab.title, dataModel);
        tabBtn.dataset.tabIndex = i;
        tabBar.appendChild(tabBtn);

        const panel = document.createElement('div');
        panel.className = `a2ui-tabs__panel${i === 0 ? ' a2ui-tabs__panel--active' : ''}`;
        panel.dataset.tabIndex = i;

        if (tab.child) {
            const childComp = componentMap.get(tab.child);
            if (childComp) {
                const childEl = renderComponent(childComp, componentMap, dataModel);
                if (childEl) panel.appendChild(childEl);
            }
        }
        tabPanels.appendChild(panel);
    });

    // Tab switching
    tabBar.addEventListener('click', (e) => {
        const btn = e.target.closest('.a2ui-tabs__tab');
        if (!btn) return;
        const idx = btn.dataset.tabIndex;

        tabBar.querySelectorAll('.a2ui-tabs__tab').forEach(t => t.classList.remove('a2ui-tabs__tab--active'));
        tabPanels.querySelectorAll('.a2ui-tabs__panel').forEach(p => p.classList.remove('a2ui-tabs__panel--active'));

        btn.classList.add('a2ui-tabs__tab--active');
        tabPanels.querySelector(`[data-tab-index="${idx}"]`)?.classList.add('a2ui-tabs__panel--active');
    });

    wrapper.appendChild(tabBar);
    wrapper.appendChild(tabPanels);
    return wrapper;
}

function renderVideo(comp, _componentMap, dataModel) {
    const url = resolveValue(comp.url, dataModel);
    const el = document.createElement('video');
    el.className = 'a2ui-video';
    el.src = url;
    el.controls = true;
    return el;
}

function renderAudioPlayer(comp, _componentMap, dataModel) {
    const url = resolveValue(comp.url, dataModel);
    const el = document.createElement('audio');
    el.className = 'a2ui-audio';
    el.src = url;
    el.controls = true;
    return el;
}

function renderDateTimeInput(comp, _componentMap, dataModel) {
    const wrapper = document.createElement('div');
    wrapper.className = 'a2ui-datetime';

    const label = resolveValue(comp.label, dataModel);
    if (label) {
        const labelEl = document.createElement('label');
        labelEl.className = 'a2ui-datetime__label';
        labelEl.textContent = label;
        wrapper.appendChild(labelEl);
    }

    const input = document.createElement('input');
    input.className = 'a2ui-datetime__input';
    const value = resolveValue(comp.value, dataModel);
    input.value = value;

    if (comp.enableDate && comp.enableTime) input.type = 'datetime-local';
    else if (comp.enableTime) input.type = 'time';
    else input.type = 'date';

    wrapper.appendChild(input);
    return wrapper;
}

function renderModal(comp, componentMap, dataModel) {
    const wrapper = document.createElement('div');
    wrapper.className = 'a2ui-modal-wrapper';

    // Trigger
    if (comp.trigger) {
        const triggerComp = componentMap.get(comp.trigger);
        if (triggerComp) {
            const triggerEl = renderComponent(triggerComp, componentMap, dataModel);
            if (triggerEl) {
                triggerEl.classList.add('a2ui-modal__trigger');
                triggerEl.addEventListener('click', () => {
                    const overlay = wrapper.querySelector('.a2ui-modal__overlay');
                    if (overlay) overlay.classList.toggle('a2ui-modal__overlay--open');
                });
                wrapper.appendChild(triggerEl);
            }
        }
    }

    // Modal content
    const overlay = document.createElement('div');
    overlay.className = 'a2ui-modal__overlay';

    const modal = document.createElement('div');
    modal.className = 'a2ui-modal';

    if (comp.content) {
        const contentComp = componentMap.get(comp.content);
        if (contentComp) {
            const contentEl = renderComponent(contentComp, componentMap, dataModel);
            if (contentEl) modal.appendChild(contentEl);
        }
    }

    // Close button
    const closeBtn = document.createElement('button');
    closeBtn.className = 'a2ui-modal__close';
    closeBtn.textContent = '✕';
    closeBtn.addEventListener('click', () => {
        overlay.classList.remove('a2ui-modal__overlay--open');
    });
    modal.insertBefore(closeBtn, modal.firstChild);

    overlay.appendChild(modal);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) overlay.classList.remove('a2ui-modal__overlay--open');
    });
    wrapper.appendChild(overlay);

    return wrapper;
}

// ─── ECharts-Based Chart Renderers ────────────────────────────────────

const CHART_COLORS = [
    '#cb7a3a', '#9b4e1d', '#e5a373', '#f2d4bd',
    '#57534e', '#78716c', '#a8a29e',
    '#d6d3d1', '#e7e5e4', '#f5f5f4',
];

/** Detect if current theme is light mode */
function isLightMode() {
    const body = document.body;
    // Check common light-mode indicators
    if (body.classList.contains('light-mode')) return true;
    if (document.documentElement.getAttribute('data-theme') === 'light') return true;
    // Check computed background color — light backgrounds have high luminance
    const bg = getComputedStyle(body).backgroundColor;
    if (bg) {
        const match = bg.match(/\d+/g);
        if (match && match.length >= 3) {
            const [r, g, b] = match.map(Number);
            const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
            if (luminance > 0.5) return true;
        }
    }
    return false;
}

/** Get ECharts theme matching the current app theme */
function getEChartsTheme() {
    const light = isLightMode();
    const textColor = light ? 'rgba(0,0,0,0.7)' : 'rgba(255,255,255,0.7)';
    const titleColor = light ? 'rgba(0,0,0,0.85)' : 'rgba(255,255,255,0.9)';
    const legendColor = light ? 'rgba(0,0,0,0.6)' : 'rgba(255,255,255,0.6)';
    const axisLineColor = light ? 'rgba(0,0,0,0.15)' : 'rgba(255,255,255,0.15)';
    const axisLabelColor = light ? 'rgba(0,0,0,0.5)' : 'rgba(255,255,255,0.5)';
    const splitLineColor = light ? 'rgba(0,0,0,0.08)' : 'rgba(255,255,255,0.06)';
    const tooltipBg = light ? 'rgba(255,255,255,0.95)' : 'rgba(20,20,40,0.92)';
    const tooltipText = light ? 'rgba(0,0,0,0.85)' : 'rgba(255,255,255,0.85)';

    const baseTheme = {
        backgroundColor: 'transparent',
        textStyle: { color: textColor, fontFamily: "'Inter', 'Roboto', system-ui, sans-serif" },
        title: { textStyle: { color: titleColor, fontSize: 14, fontWeight: 600 } },
        legend: { textStyle: { color: legendColor } },
        tooltip: {
            backgroundColor: tooltipBg,
            borderColor: 'var(--color-border)',
            textStyle: { color: tooltipText, fontSize: 12 },
            borderWidth: 1,
        },
        xAxis: {
            axisLine: { lineStyle: { color: axisLineColor } },
            axisLabel: { color: axisLabelColor },
            splitLine: { lineStyle: { color: splitLineColor } },
        },
        yAxis: {
            axisLine: { lineStyle: { color: axisLineColor } },
            axisLabel: { color: axisLabelColor },
            splitLine: { lineStyle: { color: splitLineColor } },
        },
    };

    return baseTheme;
}

/** Track ECharts instances for proper disposal */
const echartsInstances = new WeakMap();

/**
 * Create an ECharts instance inside a container div.
 * Returns {container, chart} where chart is the ECharts instance.
 */
function createEChartsContainer(options, width = '100%', height = '380px') {
    const container = document.createElement('div');
    container.className = 'a2ui-chart-container';

    const chartDiv = document.createElement('div');
    chartDiv.style.width = width;
    chartDiv.style.height = height;
    container.appendChild(chartDiv);

    // Defer ECharts init — poll until element is in DOM and has nonzero width
    // (handles case where parent is initially hidden by simple-chat-mode CSS)
    let initAttempts = 0;
    const tryInit = () => {
        initAttempts++;
        if (!chartDiv.isConnected) {
            if (initAttempts < 20) setTimeout(tryInit, 100);
            return;
        }
        if (chartDiv.offsetWidth === 0 && initAttempts < 20) {
            setTimeout(tryInit, 100);
            return;
        }
        try {
            const chart = globalThis.echarts?.init(chartDiv, null, { renderer: 'canvas' });
            if (!chart) {
                chartDiv.textContent = '[ECharts not loaded]';
                return;
            }
            chart.setOption(options);
            echartsInstances.set(chartDiv, chart);

            // Resize on window resize
            const resizeHandler = () => chart.resize();
            globalThis.addEventListener('resize', resizeHandler);

            // If chart was initialized at 0 width, schedule a resize when it becomes visible
            if (chartDiv.offsetWidth === 0) {
                const visibilityCheck = setInterval(() => {
                    if (chartDiv.offsetWidth > 0) {
                        chart.resize();
                        clearInterval(visibilityCheck);
                    }
                }, 200);
                // Safety: stop checking after 10 seconds
                setTimeout(() => clearInterval(visibilityCheck), 10000);
            }

            // Cleanup on disconnect (via MutationObserver)
            const observer = new MutationObserver(() => {
                if (!chartDiv.isConnected) {
                    chart.dispose();
                    observer.disconnect();
                    globalThis.removeEventListener('resize', resizeHandler);
                }
            });
            observer.observe(chartDiv.parentElement || document.body, { childList: true, subtree: true });
        } catch (err) {
            console.error('[A2UI] ECharts init error:', err);
            chartDiv.textContent = '[Chart error]';
        }
    };
    requestAnimationFrame(tryInit);

    // Click-to-expand handler
    container.addEventListener('click', () => openChartModal(options));

    return container;
}

/**
 * Open a fullscreen modal with an interactive ECharts chart.
 */
function openChartModal(options) {
    // Create overlay
    const overlay = document.createElement('div');
    overlay.className = 'a2ui-chart-modal-overlay';

    const content = document.createElement('div');
    content.className = 'a2ui-chart-modal-content';
    content.addEventListener('click', (e) => e.stopPropagation());

    // Close button
    const closeBtn = document.createElement('button');
    closeBtn.className = 'a2ui-chart-modal-close';
    closeBtn.textContent = 'ESC to close';
    closeBtn.addEventListener('click', () => overlay.remove());
    content.appendChild(closeBtn);

    // Chart container (fills modal)
    const chartDiv = document.createElement('div');
    chartDiv.style.width = '100%';
    chartDiv.style.height = 'calc(100% - 20px)';
    chartDiv.style.marginTop = '20px';
    content.appendChild(chartDiv);

    overlay.appendChild(content);
    document.body.appendChild(overlay);

    // Close on overlay click or ESC
    overlay.addEventListener('click', () => overlay.remove());
    const escHandler = (e) => {
        if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', escHandler); }
    };
    document.addEventListener('keydown', escHandler);

    // Init expanded chart with toolbox for download/zoom
    requestAnimationFrame(() => {
        try {
            const chart = globalThis.echarts?.init(chartDiv, null, { renderer: 'canvas' });
            if (!chart) return;

            // Expanded options: add toolbox + dataZoom
            const expandedOptions = {
                ...options,
                toolbox: {
                    show: true,
                    right: 20,
                    top: 5,
                    feature: {
                        dataZoom: { yAxisIndex: 'none', title: { zoom: 'Zoom', back: 'Reset' } },
                        restore: { title: 'Reset' },
                        saveAsImage: { title: 'Save', pixelRatio: 2 },
                    },
                    iconStyle: { borderColor: isLightMode() ? 'rgba(0,0,0,0.5)' : 'rgba(255,255,255,0.5)' },
                },
                dataZoom: [
                    { type: 'inside', filterMode: 'none' },
                ],
            };
            chart.setOption(expandedOptions);

            // Cleanup on overlay close
            const checkCleanup = () => {
                if (!overlay.isConnected) {
                    chart.dispose();
                    return;
                }
                requestAnimationFrame(checkCleanup);
            };
            requestAnimationFrame(checkCleanup);
        } catch (err) {
            console.error('[UI] Modal chart error:', err);
        }
    });
}

/**
 * Render a ScatterChart component using ECharts.
 * Supports quadrant mode (Eisenhower matrix) with labeled quadrants.
 */
function renderScatterChart(comp, _componentMap, dataModel) {
    const points = comp.points || [];
    const xLabel = comp.xLabel || 'X';
    const yLabel = comp.yLabel || 'Y';
    const quadrants = comp.quadrants || null;
    const title = comp.title || '';

    let xMin = comp.xMin ?? 0;
    let xMax = comp.xMax ?? Math.max(10, ...points.map(p => p.x || 0)) * 1.1;
    let yMin = comp.yMin ?? 0;
    let yMax = comp.yMax ?? Math.max(10, ...points.map(p => p.y || 0)) * 1.1;
    const midX = (xMin + xMax) / 2;
    const midY = (yMin + yMax) / 2;

    // Build series data with colors
    const seriesData = points.map((pt, i) => ({
        value: [pt.x || 0, pt.y || 0],
        name: pt.label || `Point ${i + 1}`,
        itemStyle: { color: pt.color || CHART_COLORS[i % CHART_COLORS.length] },
        symbolSize: (pt.size || 5) * 2,
    }));

    // Quadrant markAreas
    const markArea = quadrants ? {
        silent: true,
        data: [
            [{ name: quadrants.topRight || '', xAxis: midX, yAxis: midY, itemStyle: { color: 'rgba(203, 122, 58, 0.08)' } },
            { xAxis: xMax, yAxis: yMax }],
            [{ name: quadrants.topLeft || '', xAxis: xMin, yAxis: midY, itemStyle: { color: 'rgba(155, 78, 29, 0.08)' } },
            { xAxis: midX, yAxis: yMax }],
            [{ name: quadrants.bottomRight || '', xAxis: midX, yAxis: midY, itemStyle: { color: 'rgba(203, 122, 58, 0.05)' } },
            { xAxis: xMax, yAxis: midY }],
            [{ name: quadrants.bottomLeft || '', xAxis: xMin, yAxis: midY, itemStyle: { color: 'rgba(87, 83, 78, 0.08)' } },
            { xAxis: midX, yAxis: midY }],
        ],
        label: { fontSize: 11, color: 'rgba(255,255,255,0.35)', position: 'insideTop' },
    } : undefined;

    // Quadrant markLines
    const markLine = quadrants ? {
        silent: true,
        lineStyle: { color: 'rgba(255,255,255,0.2)', type: 'dashed' },
        data: [
            { xAxis: midX },
            { yAxis: midY },
        ],
        label: { show: false },
        symbol: 'none',
    } : undefined;

    const options = {
        ...getEChartsTheme(),
        title: { text: title, left: 'center', ...getEChartsTheme().title },
        tooltip: {
            ...getEChartsTheme().tooltip,
            trigger: 'item',
            formatter: (params) => `<strong>${params.name}</strong><br/>X: ${params.value[0]}<br/>Y: ${params.value[1]}`,
        },
        xAxis: {
            ...getEChartsTheme().xAxis,
            type: 'value',
            name: xLabel,
            nameLocation: 'center',
            nameGap: 30,
            nameTextStyle: { color: 'rgba(255,255,255,0.5)', fontSize: 12 },
            min: xMin,
            max: xMax,
        },
        yAxis: {
            ...getEChartsTheme().yAxis,
            type: 'value',
            name: yLabel,
            nameLocation: 'center',
            nameGap: 40,
            nameTextStyle: { color: 'rgba(255,255,255,0.5)', fontSize: 12 },
            min: yMin,
            max: yMax,
        },
        series: [{
            type: 'scatter',
            data: seriesData,
            markArea: markArea,
            markLine: markLine,
            emphasis: {
                itemStyle: { shadowBlur: 10, shadowColor: 'rgba(203, 122, 58, 0.5)' },
            },
        }],
        grid: { left: 60, right: 20, top: title ? 50 : 20, bottom: 50 },
        animation: true,
        animationDuration: 600,
    };

    return createEChartsContainer(options);
}

/**
 * Render a BarChart component using ECharts.
 */
function renderBarChart(comp, _componentMap, dataModel) {
    let bars = comp.bars || [];
    const title = comp.title || '';
    const yAxisLabel = comp.yLabel || 'Value';
    const horizontal = comp.horizontal || false;

    // Normalize: accept {label: value} dict
    if (!Array.isArray(bars)) {
        bars = Object.entries(bars).map(([label, value]) => ({ label, value: Number(value) || 0 }));
    }

    if (!bars.length) {
        const el = document.createElement('div');
        el.className = 'a2ui-chart-container';
        el.textContent = 'No data';
        return el;
    }

    const categories = bars.map(b => b.label);
    const values = bars.map(b => b.value);
    const colors = bars.map((b, i) => b.color || CHART_COLORS[i % CHART_COLORS.length]);

    const options = {
        ...getEChartsTheme(),
        title: { text: title, left: 'center', ...getEChartsTheme().title },
        tooltip: {
            ...getEChartsTheme().tooltip,
            trigger: 'axis',
            axisPointer: { type: 'shadow' },
        },
        xAxis: {
            ...getEChartsTheme().xAxis,
            type: horizontal ? 'value' : 'category',
            data: horizontal ? undefined : categories,
            name: horizontal ? yAxisLabel : undefined,
            axisLabel: horizontal ? {} : { rotate: bars.length > 8 ? 35 : 0, interval: 0, color: 'rgba(255,255,255,0.5)' },
        },
        yAxis: {
            ...getEChartsTheme().yAxis,
            type: horizontal ? 'category' : 'value',
            data: horizontal ? categories : undefined,
            name: horizontal ? undefined : yAxisLabel,
            nameLocation: 'center',
            nameGap: 45,
            nameTextStyle: { color: 'rgba(255,255,255,0.5)', fontSize: 12 },
        },
        series: [{
            type: 'bar',
            data: values.map((v, i) => ({
                value: v,
                itemStyle: {
                    color: {
                        type: 'linear',
                        x: 0, y: 0,
                        x2: horizontal ? 1 : 0,
                        y2: horizontal ? 0 : 1,
                        colorStops: [
                            { offset: 0, color: colors[i] },
                            { offset: 1, color: colors[i] + '80' },
                        ],
                    },
                    borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
                },
            })),
            label: {
                show: true,
                position: horizontal ? 'right' : 'top',
                color: 'rgba(255,255,255,0.6)',
                fontSize: 11,
            },
            emphasis: {
                itemStyle: { shadowBlur: 8, shadowColor: 'rgba(203, 122, 58, 0.3)' },
            },
        }],
        grid: { left: horizontal ? Math.max(120, Math.max(...categories.map(c => c.length)) * 7 + 20) : 60, right: horizontal ? 60 : 20, top: title ? 50 : 20, bottom: bars.length > 8 ? 70 : 50 },
        animation: true,
        animationDuration: 600,
    };

    return createEChartsContainer(options, '100%', horizontal ? `${Math.max(300, bars.length * 36 + 80)}px` : '380px');
}

/**
 * Generic EChart component — pass raw ECharts options directly.
 * This allows the agent to generate any chart type ECharts supports.
 */
function renderEChart(comp, _componentMap, dataModel) {
    const options = comp.options || {};
    const theme = getEChartsTheme();

    // Force CHART_COLORS and theme enforcement
    if (options.series) {
        options.series.forEach((s, idx) => {
            if (!s.itemStyle) s.itemStyle = {};

            // If color is missing or matches default blue, override with theme bronze
            let color = s.itemStyle.color;
            if (!color || color === '#5470c6' || color === 'blue') {
                color = CHART_COLORS[idx % CHART_COLORS.length];
            }
            s.itemStyle.color = color;

            if (s.areaStyle) {
                if (!s.areaStyle.color) {
                    s.areaStyle.color = {
                        type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [
                            { offset: 0, color: color },
                            { offset: 1, color: 'rgba(203, 122, 58, 0.05)' }
                        ]
                    };
                }
            }

            // Force line/bar styles to be consistent
            if (s.type === 'line' || s.type === 'bar') {
                if (!s.emphasis) s.emphasis = {};
                if (!s.emphasis.itemStyle) s.emphasis.itemStyle = {};
                s.emphasis.itemStyle.shadowBlur = 10;
                s.emphasis.itemStyle.shadowColor = 'rgba(203, 122, 58, 0.4)';
            }
        });
    }

    // Ensure visualMap gradients also use theme colors if present
    if (options.visualMap) {
        if (Array.isArray(options.visualMap)) {
            options.visualMap.forEach(vm => {
                if (vm.inRange && !vm.inRange.color) vm.inRange.color = [CHART_COLORS[0], CHART_COLORS[1]];
            });
        } else if (options.visualMap.inRange && !options.visualMap.inRange.color) {
            options.visualMap.inRange.color = [CHART_COLORS[0], CHART_COLORS[1]];
        }
    }

    // Merge dark theme defaults
    const mergedOptions = {
        ...theme,
        ...options,
        color: options.color || CHART_COLORS, // Force our palette as the base
        title: { ...theme.title, ...(options.title || {}) },
        tooltip: { ...theme.tooltip, ...(options.tooltip || {}) },
    };
    const height = comp.height || '380px';
    return createEChartsContainer(mergedOptions, '100%', height);
}

/**
 * Render a RadarChart component using ECharts.
 */
function renderRadarChart(comp, _componentMap, dataModel) {
    const indicators = comp.indicators || [];
    const series = comp.series || [];
    const title = comp.title || '';

    const options = {
        title: { text: title, left: 'center' },
        tooltip: { trigger: 'item' },
        legend: { bottom: 5, data: series.map(s => s.name), textStyle: { color: 'rgba(255,255,255,0.6)' } },
        radar: {
            indicator: indicators.map(ind => ({ name: ind.name, max: ind.max || 100 })),
            splitArea: { show: false },
            axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
            splitLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
        },
        series: [{
            type: 'radar',
            data: series.map((s, i) => ({
                value: s.value,
                name: s.name,
                itemStyle: { color: CHART_COLORS[i % CHART_COLORS.length] },
                areaStyle: { color: CHART_COLORS[i % CHART_COLORS.length] + '33' }
            }))
        }]
    };

    return createEChartsContainer(options);
}

/**
 * Render a PieChart component using ECharts.
 */
function renderPieChart(comp, _componentMap, dataModel) {
    const slices = comp.slices || comp.segments || [];
    const title = comp.title || '';
    const donut = comp.donut || false;

    const options = {
        title: { text: title, left: 'center' },
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
        legend: { bottom: 0, show: slices.length < 10, textStyle: { color: 'rgba(255,255,255,0.5)' } },
        series: [{
            type: 'pie',
            radius: donut ? ['40%', '75%'] : '75%',
            avoidLabelOverlap: true,
            itemStyle: { borderRadius: 6, borderColor: 'rgba(0,0,0,0)', borderWidth: 2 },
            label: { show: slices.length < 6, color: 'rgba(255,255,255,0.7)' },
            data: slices.map((s, i) => ({
                value: s.value,
                name: s.name || s.label,
                itemStyle: { color: CHART_COLORS[i % CHART_COLORS.length] }
            }))
        }]
    };

    return createEChartsContainer(options);
}

/**
 * Render a LineChart component using ECharts.
 */
function renderLineChart(comp, _componentMap, dataModel) {
    let series = comp.series || [];
    let labels = comp.labels || [];
    const title = comp.title || '';

    // Handle simple data format: [{name, value}, ...] (used by dashboard_a2ui API)
    // Convert to ECharts series + categories format
    const simpleData = comp.data || [];
    if (simpleData.length > 0 && series.length === 0) {
        labels = simpleData.map(d => d.name || d.label || '');
        series = [{
            name: title || 'Value',
            data: simpleData.map(d => d.value || 0),
            smooth: true,
            area: true,
        }];
    }

    const options = {
        ...getEChartsTheme(),
        title: { text: title, left: 'center', ...getEChartsTheme().title },
        legend: { show: series.length > 1 },
        tooltip: {
            ...getEChartsTheme().tooltip,
            trigger: 'axis',
            formatter: (params) => {
                if (!Array.isArray(params)) return '';
                let result = `<strong>${params[0]?.axisValue || ''}</strong><br/>`;
                params.forEach(p => {
                    const val = typeof p.value === 'number' ? p.value.toLocaleString() : p.value;
                    result += `${p.marker} ${p.seriesName}: ${val}<br/>`;
                });
                return result;
            },
        },
        xAxis: {
            ...getEChartsTheme().xAxis,
            type: 'category',
            data: labels,
            axisLabel: { rotate: labels.length > 10 ? 35 : 0, interval: 0 },
        },
        yAxis: {
            ...getEChartsTheme().yAxis,
            type: 'value',
            axisLabel: {
                formatter: (val) => {
                    if (val >= 1_000_000) return (val / 1_000_000).toFixed(1) + 'M';
                    if (val >= 1_000) return (val / 1_000).toFixed(0) + 'K';
                    return val;
                },
            },
        },
        series: series.map((s, i) => ({
            name: s.name || '',
            type: 'line',
            data: s.data,
            smooth: s.smooth !== false,
            symbol: 'circle',
            symbolSize: 6,
            lineStyle: { width: 3, color: CHART_COLORS[i % CHART_COLORS.length] },
            itemStyle: { color: CHART_COLORS[i % CHART_COLORS.length] },
            areaStyle: s.area !== false ? {
                color: {
                    type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                    colorStops: [
                        { offset: 0, color: CHART_COLORS[i % CHART_COLORS.length] + '44' },
                        { offset: 1, color: CHART_COLORS[i % CHART_COLORS.length] + '05' },
                    ],
                },
            } : null,
            emphasis: {
                itemStyle: { shadowBlur: 10, shadowColor: 'rgba(203, 122, 58, 0.4)' },
            },
        })),
        grid: { left: 60, right: 20, top: title ? 50 : 20, bottom: labels.length > 10 ? 70 : 40 },
        animation: true,
        animationDuration: 800,
    };

    return createEChartsContainer(options);
}

/**
 * Render a DataTable component.
 */
function renderDataTable(comp, _componentMap, dataModel) {
    const columns = comp.columns || [];
    const rows = comp.rows || [];
    const title = comp.title || '';

    const container = document.createElement('div');
    container.className = 'a2ui-data-table-container';

    if (title) {
        const header = document.createElement('h4');
        header.className = 'a2ui-table-title';
        header.textContent = title;
        container.appendChild(header);
    }

    const table = document.createElement('table');
    table.className = 'a2ui-data-table';

    // Header
    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    columns.forEach(col => {
        const th = document.createElement('th');
        th.textContent = col.header || col.name;
        headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    // Body
    const tbody = document.createElement('tbody');
    rows.forEach(row => {
        const tr = document.createElement('tr');
        columns.forEach(col => {
            const td = document.createElement('td');
            const value = row[col.name] || '';
            td.textContent = value;
            if (col.type === 'status') {
                const statusIcon = document.createElement('span');
                statusIcon.className = `a2ui-status-icon status-${String(value).toLowerCase()}`;
                td.prepend(statusIcon);
            }
            tr.appendChild(td);
        });
        tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    container.appendChild(table);

    return container;
}

/**
 * Render a StatusDashboard component.
 */
function renderStatusDashboard(comp, _componentMap, dataModel) {
    const items = comp.items || [];
    const title = comp.title || 'Status Dashboard';

    const container = document.createElement('div');
    container.className = 'a2ui-status-dashboard';

    const header = document.createElement('h3');
    header.textContent = title;
    container.appendChild(header);

    const grid = document.createElement('div');
    grid.className = 'a2ui-status-grid';

    items.forEach(item => {
        const card = document.createElement('div');
        card.className = `a2ui-status-card status-${String(item.status).toLowerCase()}`;

        const label = document.createElement('div');
        label.className = 'a2ui-status-label';
        label.textContent = item.label || item.name;

        const value = document.createElement('div');
        value.className = 'a2ui-status-value';
        value.textContent = item.value;

        card.appendChild(label);
        card.appendChild(value);
        grid.appendChild(card);
    });

    container.appendChild(grid);
    return container;
}

/**
 * Render an InfoCard component.
 */
function renderInfoCard(comp, _componentMap, dataModel) {
    const container = document.createElement('div');
    container.className = 'a2ui-info-card';

    if (comp.title) {
        const title = document.createElement('h4');
        title.textContent = comp.title;
        container.appendChild(title);
    }

    const content = document.createElement('div');
    content.className = 'a2ui-info-content';
    // Handle data binding in content
    let finalContent = comp.content || '';
    if (dataModel) {
        Object.keys(dataModel).forEach(key => {
            const regex = new RegExp(`\\{\\{${key}\\}\\}`, 'g');
            finalContent = finalContent.replace(regex, dataModel[key]);
        });
    }
    content.innerHTML = finalContent;
    container.appendChild(content);

    if (comp.footer) {
        const footer = document.createElement('div');
        footer.className = 'a2ui-info-footer';
        footer.textContent = comp.footer;
        container.appendChild(footer);
    }

    return container;
}

