/**
 * Scheduler Forms Module
 * Handles task create/edit forms, Flatpickr initialization, project dropdown,
 * and frequency management for the task scheduler component.
 * Extracted from scheduler.js for modularization (P1.3).
 */

import { getUserTimezone } from './time-utils.js';

// projectsStore is resolved at runtime via Alpine.store('projects') or window reference
let projectsStore = null;

/**
 * Resolve the projectsStore reference. Called lazily to avoid import-time issues.
 */
function resolveProjectsStore() {
    if (projectsStore) return projectsStore;
    if (window.Alpine && window.Alpine.store('projects')) {
        projectsStore = window.Alpine.store('projects');
    }
    return projectsStore;
}

/**
 * Derive the active project from the current chat context.
 * @param {object} chatsStore - The chats store
 * @returns {object|null} Active project or null
 */
export function deriveActiveProject(chatsStore) {
    const selected = chatsStore?.selectedContext || null;
    if (!selected || !selected.project) {
        return null;
    }

    const project = selected.project;
    return {
        name: project.name || null,
        title: project.title || project.name || null,
        color: project.color || '',
    };
}

/**
 * Format a project name for display.
 * @param {object} project - Project object
 * @returns {string} Formatted name
 */
export function formatProjectName(project) {
    if (!project) {
        return 'No Project';
    }
    const title = project.title || project.name;
    return title || 'No Project';
}

/**
 * Format a project label for display.
 * @param {object} project - Project object
 * @returns {string} Formatted label
 */
export function formatProjectLabel(project) {
    return `Project: ${formatProjectName(project)}`;
}

/**
 * Refresh project options from the projects store.
 * @param {object} component - The Alpine component instance (this)
 */
export async function refreshProjectOptions(component) {
    const store = resolveProjectsStore();
    try {
        if (store) {
            if (!Array.isArray(store.projectList) || !store.projectList.length) {
                if (typeof store.loadProjectsList === 'function') {
                    await store.loadProjectsList();
                }
            }
        }
    } catch (error) {
        console.warn('schedulerSettings: failed to load project list', error);
    }

    const list = store && Array.isArray(store.projectList) ? store.projectList : [];
    component.projectOptions = list.map((proj) => ({
        name: proj.name,
        title: proj.title || proj.name,
        color: proj.color || '',
    }));
}

/**
 * Handle project selection from dropdown.
 * @param {object} component - The Alpine component instance (this)
 * @param {string} slug - Project slug
 */
export function onProjectSelect(component, slug) {
    component.selectedProjectSlug = slug || '';
    if (!slug) {
        component.editingTask.project = null;
        return;
    }

    const option = component.projectOptions.find((item) => item.name === slug);
    if (option) {
        component.editingTask.project = { ...option };
    } else {
        component.editingTask.project = {
            name: slug,
            title: slug,
            color: '',
        };
    }
}

/**
 * Get filtered project options based on search query.
 * @param {object} component - The Alpine component instance (this)
 * @returns {Array} Filtered project options
 */
export function getFilteredProjectOptions(component) {
    if (!component.projectSearchQuery || component.projectSearchQuery.trim() === '') {
        return component.projectOptions;
    }
    const query = component.projectSearchQuery.toLowerCase().trim();
    return component.projectOptions.filter(proj => {
        const title = (proj.title || proj.name || '').toLowerCase();
        const name = (proj.name || '').toLowerCase();
        return title.includes(query) || name.includes(query);
    });
}

/**
 * Toggle project dropdown open/close.
 * @param {object} component - The Alpine component instance (this)
 */
export function toggleProjectDropdown(component) {
    component.isProjectDropdownOpen = !component.isProjectDropdownOpen;
    if (component.isProjectDropdownOpen) {
        component.projectSearchQuery = '';
        component.$nextTick(() => {
            const searchInput = component.$refs.projectSearchInput;
            if (searchInput) {
                searchInput.focus();
            }
        });
    }
}

/**
 * Close project dropdown.
 * @param {object} component - The Alpine component instance (this)
 */
export function closeProjectDropdown(component) {
    component.isProjectDropdownOpen = false;
    component.projectSearchQuery = '';
}

/**
 * Select a project from the dropdown.
 * @param {object} component - The Alpine component instance (this)
 * @param {object} project - Project object
 */
export function selectProjectFromDropdown(component, project) {
    if (project) {
        component.selectedProjectSlug = project.name;
        component.editingTask.project = { ...project };
    } else {
        component.selectedProjectSlug = '';
        component.editingTask.project = null;
    }
    closeProjectDropdown(component);
}

/**
 * Open create project modal.
 * @param {object} component - The Alpine component instance (this)
 */
export function openCreateProjectModal(component) {
    closeProjectDropdown(component);
    const store = resolveProjectsStore();
    if (store && typeof store.openCreateModal === 'function') {
        store.openCreateModal();
    } else if (window.Alpine && window.Alpine.store('projects')) {
        window.Alpine.store('projects').openCreateModal();
    }
}

/**
 * Extract project info from a task object.
 * @param {object} task - Task object
 * @returns {object|null} Project info or null
 */
export function extractTaskProject(task) {
    if (!task) {
        return null;
    }

    const slug = task.project_name || null;
    const project = task.project || {};
    const title = project.name || slug;
    const color = task.project_color || project.color || '';

    if (!slug && !title) {
        return null;
    }

    return {
        name: slug,
        title: title || slug,
        color: color,
    };
}

/**
 * Format a task's project for display.
 * @param {object} task - Task object
 * @returns {string} Formatted project name
 */
export function formatTaskProject(task) {
    return formatProjectName(extractTaskProject(task));
}

/**
 * Start creating a new task.
 * @param {object} component - The Alpine component instance (this)
 * @param {object} chatsStore - The chats store
 */
export async function startCreateTask(component, chatsStore) {
    component.isCreating = true;
    component.isEditing = false;
    document.querySelector('[x-data="schedulerSettings"]')?.setAttribute('data-editing-state', 'creating');
    await refreshProjectOptions(component);
    const activeProject = deriveActiveProject(chatsStore);
    let initialProject = activeProject ? { ...activeProject } : null;
    if (!initialProject && component.projectOptions.length > 0) {
        initialProject = { ...component.projectOptions[0] };
    }

    component.editingTask = {
        name: '',
        type: 'scheduled',
        state: 'idle',
        schedule: {
            minute: '*',
            hour: '*',
            day: '*',
            month: '*',
            weekday: '*',
            timezone: getUserTimezone()
        },
        token: component.generateRandomToken(),
        plan: {
            todo: [],
            in_progress: null,
            done: []
        },
        system_prompt: '',
        prompt: '',
        attachments: [],
        project: initialProject,
        dedicated_context: true,
    };
    component.selectedProjectSlug = initialProject && initialProject.name ? initialProject.name : '';

    component.$nextTick(() => {
        component.initFlatpickr('create');
    });
}

/**
 * Start editing an existing task.
 * @param {object} component - The Alpine component instance (this)
 * @param {string} taskId - Task UUID
 * @param {Function} showToast - Toast notification function
 */
export async function startEditTask(component, taskId, showToast) {
    const task = component.tasks.find(t => t.uuid === taskId);
    if (!task) {
        showToast('Task not found', 'error');
        return;
    }

    component.isCreating = false;
    component.isEditing = true;
    document.querySelector('[x-data="schedulerSettings"]')?.setAttribute('data-editing-state', 'editing');

    // Create a deep copy to avoid modifying the original
    component.editingTask = JSON.parse(JSON.stringify(task));
    const projectSlug = task.project_name || null;
    const projectDisplay = (task.project && task.project.name) || projectSlug;
    const projectColor = task.project_color || (task.project ? task.project.color : '') || '';
    component.editingTask.project = projectSlug || projectDisplay ? {
        name: projectSlug,
        title: projectDisplay,
        color: projectColor,
    } : null;
    component.editingTask.dedicated_context = !!task.dedicated_context;
    component.selectedProjectSlug = component.editingTask.project && component.editingTask.project.name ? component.editingTask.project.name : '';

    console.log('Task data for editing:', task);
    console.log('Attachments from task:', task.attachments);

    // Ensure state is set with a default if missing
    if (!component.editingTask.state) component.editingTask.state = 'idle';

    // Always initialize schedule to prevent UI errors
    if (!component.editingTask.schedule || typeof component.editingTask.schedule === 'string') {
        let scheduleObj = {
            minute: '*',
            hour: '*',
            day: '*',
            month: '*',
            weekday: '*',
            timezone: getUserTimezone()
        };

        if (typeof component.editingTask.schedule === 'string') {
            const parts = component.editingTask.schedule.split(' ');
            if (parts.length >= 5) {
                scheduleObj.minute = parts[0] || '*';
                scheduleObj.hour = parts[1] || '*';
                scheduleObj.day = parts[2] || '*';
                scheduleObj.month = parts[3] || '*';
                scheduleObj.weekday = parts[4] || '*';
            }
        }

        component.editingTask.schedule = scheduleObj;
    } else {
        if (!component.editingTask.schedule.timezone) {
            component.editingTask.schedule.timezone = getUserTimezone();
        }
    }

    // Ensure attachments is always an array
    if (!component.editingTask.attachments) {
        component.editingTask.attachments = [];
    } else if (typeof component.editingTask.attachments === 'string') {
        component.editingTask.attachments = component.editingTask.attachments
            .split('\n')
            .map(line => line.trim())
            .filter(line => line.length > 0);
    } else if (!Array.isArray(component.editingTask.attachments)) {
        component.editingTask.attachments = [];
    }

    // Ensure appropriate properties are initialized based on task type
    if (component.editingTask.type === 'scheduled') {
        if (!component.editingTask.token) {
            component.editingTask.token = '';
        }
        if (!component.editingTask.plan) {
            component.editingTask.plan = { todo: [], in_progress: null, done: [] };
        }
        if (component.editingTask.schedule) {
            const cron = `${component.editingTask.schedule.minute} ${component.editingTask.schedule.hour} ${component.editingTask.schedule.day} ${component.editingTask.schedule.month} ${component.editingTask.schedule.weekday}`;
            const options = [
                '* * * * *', '*/5 * * * *', '*/15 * * * *', '*/30 * * * *',
                '0 * * * *', '0 */12 * * *', '0 0 * * *', '0 0 * * 0'
            ];
            component.selectedFrequency = options.includes(cron) ? cron : 'custom';
        }
    } else if (component.editingTask.type === 'adhoc') {
        if (!component.editingTask.token) {
            component.editingTask.token = component.generateRandomToken();
            console.log('Generated new token for adhoc task:', component.editingTask.token);
        }
        console.log('Setting token for adhoc task:', component.editingTask.token);
        if (!component.editingTask.plan) {
            component.editingTask.plan = { todo: [], in_progress: null, done: [] };
        }
    } else if (component.editingTask.type === 'planned') {
        if (!component.editingTask.plan) {
            component.editingTask.plan = { todo: [], in_progress: null, done: [] };
        }
        if (!Array.isArray(component.editingTask.plan.todo)) {
            component.editingTask.plan.todo = [];
        }
        if (!component.editingTask.token) {
            component.editingTask.token = '';
        }
    }

    component.$nextTick(() => {
        component.initFlatpickr('edit');
    });
}

/**
 * Cancel editing and reset form state.
 * @param {object} component - The Alpine component instance (this)
 */
export function cancelEdit(component) {
    // Clean up Flatpickr instances
    const destroyFlatpickr = (inputId) => {
        const input = document.getElementById(inputId);
        if (input && input._flatpickr) {
            console.log(`Destroying Flatpickr instance for ${inputId}`);
            input._flatpickr.destroy();

            const wrapper = input.closest('.scheduler-flatpickr-wrapper');
            if (wrapper && wrapper.parentNode) {
                wrapper.parentNode.insertBefore(input, wrapper);
                wrapper.parentNode.removeChild(wrapper);
            }

            input.classList.remove('scheduler-flatpickr-input');
        }
    };

    if (component.isCreating) {
        destroyFlatpickr('newPlannedTime-create');
    } else if (component.isEditing) {
        destroyFlatpickr('newPlannedTime-edit');
    }

    component.editingTask = {
        name: '',
        type: 'scheduled',
        state: 'idle',
        schedule: {
            minute: '*',
            hour: '*',
            day: '*',
            month: '*',
            weekday: '*',
            timezone: getUserTimezone()
        },
        token: '',
        plan: {
            todo: [],
            in_progress: null,
            done: []
        },
        system_prompt: '',
        prompt: '',
        attachments: [],
        project: null,
        dedicated_context: true,
    };
    component.selectedProjectSlug = '';
    component.isCreating = false;
    component.isEditing = false;
    document.querySelector('[x-data="schedulerSettings"]')?.removeAttribute('data-editing-state');
}

/**
 * Set simplified frequency from preset.
 * @param {object} component - The Alpine component instance (this)
 * @param {string} cron - Cron expression
 */
export function setFrequency(component, cron) {
    component.selectedFrequency = cron;
    if (cron === 'custom') return;

    const parts = cron.split(' ');
    if (parts.length === 5) {
        component.editingTask.schedule.minute = parts[0];
        component.editingTask.schedule.hour = parts[1];
        component.editingTask.schedule.day = parts[2];
        component.editingTask.schedule.month = parts[3];
        component.editingTask.schedule.weekday = parts[4];
    }
}

/**
 * Initialize datetime input with default value (30 minutes from now).
 * @param {object} component - The Alpine component instance (this)
 * @param {Event} event - Input event
 */
export function initDateTimeInput(component, event) {
    if (!event.target.value) {
        const now = new Date();
        now.setMinutes(now.getMinutes() + 30);

        const year = now.getFullYear();
        const month = String(now.getMonth() + 1).padStart(2, '0');
        const day = String(now.getDate()).padStart(2, '0');
        const hours = String(now.getHours()).padStart(2, '0');
        const minutes = String(now.getMinutes()).padStart(2, '0');

        event.target.value = `${year}-${month}-${day}T${hours}:${minutes}`;

        if (event.target._flatpickr) {
            event.target._flatpickr.setDate(event.target.value);
        }
    }
}

/**
 * Generate a random token for ad-hoc tasks.
 * @returns {string} Random 16-character token
 */
export function generateRandomToken() {
    const characters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let token = '';
    for (let i = 0; i < 16; i++) {
        token += characters.charAt(Math.floor(Math.random() * characters.length));
    }
    return token;
}

/**
 * Initialize Flatpickr datetime pickers for both create and edit forms.
 * @param {object} component - The Alpine component instance (this)
 * @param {string} mode - Which pickers to initialize: 'all', 'create', or 'edit'
 */
export function initFlatpickr(component, mode = 'all') {
    const initPicker = (inputId, refName, wrapperClass, options = {}) => {
        let input = component.$refs[refName];

        if (!input) {
            input = document.getElementById(inputId);
            console.log(`Using getElementById fallback for ${inputId}`);
        }

        if (!input) {
            console.warn(`Input element ${inputId} not found by ID or ref`);
            return null;
        }

        const wrapper = document.createElement('div');
        wrapper.className = wrapperClass || 'scheduler-flatpickr-wrapper';
        wrapper.style.overflow = 'visible';

        input.parentNode.insertBefore(wrapper, input);
        wrapper.appendChild(input);
        input.classList.add('scheduler-flatpickr-input');

        const defaultOptions = {
            dateFormat: "Y-m-d H:i",
            enableTime: true,
            time_24hr: true,
            static: false,
            appendTo: document.body,
            theme: "scheduler-theme",
            allowInput: true,
            positionElement: wrapper,
            onOpen: function (selectedDates, dateStr, instance) {
                instance.calendarContainer.style.zIndex = '9999';
                instance.calendarContainer.style.position = 'absolute';
                instance.calendarContainer.style.visibility = 'visible';
                instance.calendarContainer.style.opacity = '1';
                instance.calendarContainer.classList.add('scheduler-theme');
            },
            onReady: function (selectedDates, dateStr, instance) {
                if (!dateStr) {
                    const now = new Date();
                    now.setMinutes(now.getMinutes() + 30);
                    instance.setDate(now, true);
                }
            }
        };

        const mergedOptions = { ...defaultOptions, ...options };

        const fp = flatpickr(input, mergedOptions);

        const clearButton = document.createElement('button');
        clearButton.className = 'scheduler-flatpickr-clear';
        clearButton.innerHTML = '×';
        clearButton.type = 'button';
        clearButton.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (fp) {
                fp.clear();
            }
        });
        wrapper.appendChild(clearButton);

        return fp;
    };

    // Clear any existing Flatpickr instances to prevent duplication
    if (mode === 'all' || mode === 'create') {
        const createInput = document.getElementById('newPlannedTime-create');
        if (createInput && createInput._flatpickr) {
            createInput._flatpickr.destroy();
        }
    }

    if (mode === 'all' || mode === 'edit') {
        const editInput = document.getElementById('newPlannedTime-edit');
        if (editInput && editInput._flatpickr) {
            editInput._flatpickr.destroy();
        }
    }

    // Initialize new instances
    if (mode === 'all' || mode === 'create') {
        initPicker('newPlannedTime-create', 'plannedTimeCreate', 'scheduler-flatpickr-wrapper', {
            minuteIncrement: 5,
            defaultHour: new Date().getHours(),
            defaultMinute: Math.ceil(new Date().getMinutes() / 5) * 5
        });
    }

    if (mode === 'all' || mode === 'edit') {
        initPicker('newPlannedTime-edit', 'plannedTimeEdit', 'scheduler-flatpickr-wrapper', {
            minuteIncrement: 5,
            defaultHour: new Date().getHours(),
            defaultMinute: Math.ceil(new Date().getMinutes() / 5) * 5
        });
    }
}
