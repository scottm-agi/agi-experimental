/**
 * Scheduler Display Module
 * Handles formatting, filtering, sorting, and display-related functions
 * for the task scheduler component.
 * Extracted from scheduler.js for modularization (P1.3).
 */

import { formatDateTime } from './time-utils.js';

/**
 * Format a date string for display.
 * @param {string} dateString - ISO date string
 * @returns {string} Formatted date string
 */
export function formatDate(dateString) {
    if (!dateString) return 'Never';
    return formatDateTime(dateString, 'full');
}

/**
 * Format a task's plan for display.
 * @param {object} task - Task object
 * @returns {string} Formatted plan string
 */
export function formatPlan(task) {
    if (!task || !task.plan) return 'No plan';

    const todoCount = Array.isArray(task.plan.todo) ? task.plan.todo.length : 0;
    const inProgress = task.plan.in_progress ? 'Yes' : 'No';
    const doneCount = Array.isArray(task.plan.done) ? task.plan.done.length : 0;

    let nextRun = '';
    if (Array.isArray(task.plan.todo) && task.plan.todo.length > 0) {
        try {
            const nextTime = new Date(task.plan.todo[0]);

            // Verify it's a valid date before formatting
            if (!isNaN(nextTime.getTime())) {
                nextRun = formatDateTime(nextTime, 'short');
            } else {
                nextRun = 'Invalid date';
                console.warn(`Invalid date format in plan.todo[0]: ${task.plan.todo[0]}`);
            }
        } catch (error) {
            console.error(`Error formatting next run time: ${error.message}`);
            nextRun = 'Error';
        }
    } else {
        nextRun = 'None';
    }

    return `Next: ${nextRun}\nTodo: ${todoCount}\nIn Progress: ${inProgress}\nDone: ${doneCount}`;
}

/**
 * Format a task's schedule for display.
 * @param {object} task - Task object
 * @returns {string} Formatted schedule string
 */
export function formatSchedule(task) {
    if (!task.schedule) return 'None';

    let schedule = '';
    if (typeof task.schedule === 'string') {
        schedule = task.schedule;
    } else if (typeof task.schedule === 'object') {
        schedule = `${task.schedule.minute || '*'} ${task.schedule.hour || '*'} ${task.schedule.day || '*'} ${task.schedule.month || '*'} ${task.schedule.weekday || '*'}`;
    }

    return schedule;
}

/**
 * Get CSS class for a state badge.
 * @param {string} state - Task state
 * @returns {string} CSS class name
 */
export function getStateBadgeClass(state) {
    switch (state) {
        case 'idle': return 'scheduler-status-idle';
        case 'running': return 'scheduler-status-running';
        case 'disabled': return 'scheduler-status-disabled';
        case 'error': return 'scheduler-status-error';
        default: return '';
    }
}

/**
 * Change sort field/direction.
 * @param {object} component - The Alpine component instance (this)
 * @param {string} field - Sort field name
 */
export function changeSort(component, field) {
    if (component.sortField === field) {
        component.sortDirection = component.sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
        component.sortField = field;
        component.sortDirection = 'asc';
    }
}

/**
 * Toggle expanded task row.
 * @param {object} component - The Alpine component instance (this)
 * @param {string} taskId - Task UUID
 */
export function toggleTaskExpand(component, taskId) {
    if (component.expandedTaskId === taskId) {
        component.expandedTaskId = null;
    } else {
        component.expandedTaskId = taskId;
    }
}

/**
 * Show task detail view.
 * @param {object} component - The Alpine component instance (this)
 * @param {string} taskId - Task UUID
 * @param {Function} showToast - Toast notification function
 */
export function showTaskDetail(component, taskId, showToast) {
    const task = component.tasks.find(t => t.uuid === taskId);
    if (!task) {
        showToast('Task not found', 'error');
        return;
    }

    component.selectedTaskForDetail = JSON.parse(JSON.stringify(task));

    if (!component.selectedTaskForDetail.attachments) {
        component.selectedTaskForDetail.attachments = [];
    }

    component.viewMode = 'detail';
}

/**
 * Close detail view and return to list.
 * @param {object} component - The Alpine component instance (this)
 */
export function closeTaskDetail(component) {
    component.selectedTaskForDetail = null;
    component.viewMode = 'list';
}

/**
 * Compute filtered tasks based on current filter/sort settings.
 * @param {object} component - The Alpine component instance (this)
 */
export function computeFilteredTasks(component) {
    if (!Array.isArray(component.tasks)) {
        console.warn('computeFilteredTasks: Tasks is not an array:', component.tasks);
        component.filteredTasks = [];
        return;
    }

    let filtered = [...component.tasks];

    // Apply text search filter
    if (component.taskSearchQuery && component.taskSearchQuery.trim()) {
        const query = component.taskSearchQuery.trim().toLowerCase();
        filtered = filtered.filter(task => {
            const name = (task.name || '').toLowerCase();
            const type = (task.type || '').toLowerCase();
            const state = (task.state || '').toLowerCase();
            const project = (task.project_slug || '').toLowerCase();
            const schedule = (task.schedule || '').toLowerCase();
            return name.includes(query) || type.includes(query) || state.includes(query) || project.includes(query) || schedule.includes(query);
        });
    }

    // Apply type filter with case-insensitive comparison
    if (component.filterType && component.filterType !== 'all') {
        filtered = filtered.filter(task => {
            if (!task.type) return false;
            return String(task.type).toLowerCase() === component.filterType.toLowerCase();
        });
    }

    // Apply state filter with case-insensitive comparison
    if (component.filterState && component.filterState !== 'all') {
        filtered = filtered.filter(task => {
            if (!task.state) return false;
            return String(task.state).toLowerCase() === component.filterState.toLowerCase();
        });
    }

    // Sort the filtered tasks and assign to reactive property
    component.filteredTasks = sortTasks(component, filtered);
    console.log('computeFilteredTasks: Updated filteredTasks with', component.filteredTasks.length, 'tasks');
}

/**
 * Sort tasks based on sort field and direction.
 * @param {object} component - The Alpine component instance (this)
 * @param {Array} tasks - Tasks array to sort
 * @returns {Array} Sorted tasks
 */
export function sortTasks(component, tasks) {
    if (!Array.isArray(tasks) || tasks.length === 0) {
        return tasks;
    }

    return [...tasks].sort((a, b) => {
        if (!component.sortField) return 0;

        const fieldA = a[component.sortField];
        const fieldB = b[component.sortField];

        if (fieldA === undefined && fieldB === undefined) return 0;
        if (fieldA === undefined) return 1;
        if (fieldB === undefined) return -1;

        // For dates, convert to timestamps
        if (component.sortField === 'createdAt' || component.sortField === 'updatedAt') {
            const dateA = new Date(fieldA).getTime();
            const dateB = new Date(fieldB).getTime();
            return component.sortDirection === 'asc' ? dateA - dateB : dateB - dateA;
        }

        // For string comparisons
        if (typeof fieldA === 'string' && typeof fieldB === 'string') {
            return component.sortDirection === 'asc'
                ? fieldA.localeCompare(fieldB)
                : fieldB.localeCompare(fieldA);
        }

        // For numerical comparisons
        return component.sortDirection === 'asc' ? fieldA - fieldB : fieldB - fieldA;
    });
}

/**
 * Update tasks UI — toggle empty state and table visibility.
 * @param {object} component - The Alpine component instance (this)
 */
export function updateTasksUI(component) {
    // First update filteredTasks if that method exists
    if (typeof component.updateFilteredTasks === 'function') {
        component.updateFilteredTasks();
    }

    // Wait for UI to update
    component.$nextTick(() => {
        const emptyElement = document.querySelector('.scheduler-empty');
        const tableElement = document.querySelector('.scheduler-task-list');

        const hasFilteredTasks = Array.isArray(component.filteredTasks) && component.filteredTasks.length > 0;

        if (emptyElement) {
            emptyElement.style.display = !hasFilteredTasks ? '' : 'none';
        }

        if (tableElement) {
            tableElement.style.display = hasFilteredTasks ? '' : 'none';
        }
    });
}

/**
 * Debug method to test filtering logic.
 * @param {object} component - The Alpine component instance (this)
 */
export function testFiltering(component) {
    console.group('SchedulerSettings Debug: Filter Test');
    console.log('Current Filter Settings:');
    console.log('- Filter Type:', component.filterType);
    console.log('- Filter State:', component.filterState);
    console.log('- Sort Field:', component.sortField);
    console.log('- Sort Direction:', component.sortDirection);

    if (!Array.isArray(component.tasks)) {
        console.error('ERROR: this.tasks is not an array!', component.tasks);
        console.groupEnd();
        return;
    }

    console.log(`Raw Tasks (${component.tasks.length}):`, component.tasks);

    // Test filtering by type
    console.group('Filter by Type Test');
    ['all', 'adhoc', 'scheduled', 'recurring'].forEach(type => {
        const filtered = component.tasks.filter(task =>
            type === 'all' ||
            (task.type && String(task.type).toLowerCase() === type)
        );
        console.log(`Type "${type}": ${filtered.length} tasks`, filtered);
    });
    console.groupEnd();

    // Test filtering by state
    console.group('Filter by State Test');
    ['all', 'idle', 'running', 'completed', 'failed'].forEach(state => {
        const filtered = component.tasks.filter(task =>
            state === 'all' ||
            (task.state && String(task.state).toLowerCase() === state)
        );
        console.log(`State "${state}": ${filtered.length} tasks`, filtered);
    });
    console.groupEnd();

    console.log('Current Filtered Tasks:', component.filteredTasks);

    console.groupEnd();
}

/**
 * Comprehensive debug method for scheduler tasks.
 * @param {object} component - The Alpine component instance (this)
 */
export function debugTasks(component) {
    console.group('SchedulerSettings Comprehensive Debug');

    // Component state
    console.log('Component State:');
    console.log({
        filterType: component.filterType,
        filterState: component.filterState,
        sortField: component.sortField,
        sortDirection: component.sortDirection,
        isLoading: component.isLoading,
        isEditing: component.isEditing,
        isCreating: component.isCreating,
        viewMode: component.viewMode
    });

    // Tasks validation
    if (!component.tasks) {
        console.error('ERROR: this.tasks is undefined or null!');
        console.groupEnd();
        return;
    }

    if (!Array.isArray(component.tasks)) {
        console.error('ERROR: this.tasks is not an array!', typeof component.tasks, component.tasks);
        console.groupEnd();
        return;
    }

    // Raw tasks
    console.group('Raw Tasks');
    console.log(`Count: ${component.tasks.length}`);
    if (component.tasks.length > 0) {
        console.table(component.tasks.map(t => ({
            uuid: t.uuid,
            name: t.name,
            type: t.type,
            state: t.state
        })));

        // Inspect first task in detail
        console.log('First Task Structure:', JSON.stringify(component.tasks[0], null, 2));
    } else {
        console.log('No tasks available');
    }
    console.groupEnd();

    // Filtered tasks
    console.group('Filtered Tasks');
    const filteredTasks = component.filteredTasks;
    console.log(`Count: ${filteredTasks.length}`);
    if (filteredTasks.length > 0) {
        console.table(filteredTasks.map(t => ({
            uuid: t.uuid,
            name: t.name,
            type: t.type,
            state: t.state
        })));
    } else {
        console.log('No filtered tasks');
    }
    console.groupEnd();

    // Check for potential issues
    console.group('Potential Issues');

    if (component.tasks.length > 0 && filteredTasks.length === 0) {
        console.warn('Filter seems to exclude all tasks. Checking why:');

        const uniqueTypes = [...new Set(component.tasks.map(t => t.type))];
        console.log('Unique task types in data:', uniqueTypes);

        const uniqueStates = [...new Set(component.tasks.map(t => t.state))];
        console.log('Unique task states in data:', uniqueStates);

        if (component.filterType !== 'all') {
            const typeMatch = component.tasks.some(t =>
                t.type && String(t.type).toLowerCase() === component.filterType.toLowerCase()
            );
            console.log(`Type "${component.filterType}" matches found:`, typeMatch);
        }

        if (component.filterState !== 'all') {
            const stateMatch = component.tasks.some(t =>
                t.state && String(t.state).toLowerCase() === component.filterState.toLowerCase()
            );
            console.log(`State "${component.filterState}" matches found:`, stateMatch);
        }
    }

    const hasUndefinedType = component.tasks.some(t => t.type === undefined || t.type === null);
    const hasUndefinedState = component.tasks.some(t => t.state === undefined || t.state === null);

    if (hasUndefinedType) {
        console.warn('Some tasks have undefined or null type values!');
    }

    if (hasUndefinedState) {
        console.warn('Some tasks have undefined or null state values!');
    }

    console.groupEnd();

    console.groupEnd();
}

/**
 * Toggle selection of a single task.
 * @param {object} component - The Alpine component instance (this)
 * @param {string} taskId - Task UUID
 */
export function toggleTaskSelection(component, taskId) {
    if (component.selectedTaskIds.includes(taskId)) {
        component.selectedTaskIds = component.selectedTaskIds.filter(id => id !== taskId);
    } else {
        component.selectedTaskIds.push(taskId);
    }
    updateSelectionState(component);
}

/**
 * Check if a task is selected.
 * @param {object} component - The Alpine component instance (this)
 * @param {string} taskId - Task UUID
 * @returns {boolean}
 */
export function isTaskSelected(component, taskId) {
    return component.selectedTaskIds.includes(taskId);
}

/**
 * Toggle "Select All" for filtered tasks.
 * @param {object} component - The Alpine component instance (this)
 */
export function toggleSelectAllTasks(component) {
    if (component.allTasksSelected) {
        component.selectedTaskIds = [];
    } else {
        component.selectedTaskIds = component.filteredTasks.map(task => task.uuid);
    }
    updateSelectionState(component);
}

/**
 * Update reactive selection state.
 * @param {object} component - The Alpine component instance (this)
 */
export function updateSelectionState(component) {
    const filteredUuids = component.filteredTasks.map(t => t.uuid);
    component.someTasksSelected = component.selectedTaskIds.length > 0;

    component.allTasksSelected = filteredUuids.length > 0 &&
        filteredUuids.every(uuid => component.selectedTaskIds.includes(uuid));
}
