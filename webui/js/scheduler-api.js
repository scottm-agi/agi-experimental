/**
 * Scheduler API Module
 * Handles all API communication for the task scheduler component.
 * Extracted from scheduler.js for modularization (P1.3).
 */

import { getUserTimezone } from './time-utils.js';
import { store as chatsStore } from "../components/sidebar/chats/chats-store.js";
import * as api from "./api.js";

/**
 * Fetch tasks from the backend API.
 * @param {object} component - The Alpine component instance (this)
 * @param {Function} showToast - Toast notification function
 */
export async function fetchTasks(component, showToast) {
    // Don't fetch if polling is inactive (prevents race conditions)
    if (!component.pollingActive && component.pollingInterval) {
        return;
    }

    // Don't fetch while creating/editing a task
    if (component.isCreating || component.isEditing) {
        return;
    }

    component.isLoading = true;
    try {
        const response = await api.fetchApi('/scheduler_tasks_list', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                timezone: getUserTimezone()
            })
        });

        if (!response.ok) {
            throw new Error('Failed to fetch tasks');
        }

        const data = await response.json();

        // Check if data.tasks exists and is an array
        if (!data || !data.tasks) {
            console.error('Invalid response: data.tasks is missing', data);
            component.tasks = [];
        } else if (!Array.isArray(data.tasks)) {
            console.error('Invalid response: data.tasks is not an array', data.tasks);
            component.tasks = [];
        } else {
            // Verify each task has necessary properties
            const validTasks = data.tasks.filter(task => {
                if (!task || typeof task !== 'object') {
                    console.error('Invalid task (not an object):', task);
                    return false;
                }
                if (!task.uuid) {
                    console.error('Task missing uuid:', task);
                    return false;
                }
                if (!task.name) {
                    console.error('Task missing name:', task);
                    return false;
                }
                if (!task.type) {
                    console.error('Task missing type:', task);
                    return false;
                }
                return true;
            });

            if (validTasks.length !== data.tasks.length) {
                console.warn(`Filtered out ${data.tasks.length - validTasks.length} invalid tasks`);
            }

            component.tasks = validTasks;

            // Explicitly compute filtered tasks for Alpine reactivity
            component.computeFilteredTasks();

            // Update UI using the shared function
            component.updateTasksUI();
        }
    } catch (error) {
        console.error('Error fetching tasks:', error);
        // Only show toast for errors on manual refresh, not during polling
        if (!component.pollingInterval) {
            showToast('Failed to fetch tasks: ' + error.message, 'error');
        }
        // Reset tasks to empty array on error
        component.tasks = [];
    } finally {
        component.isLoading = false;
    }
}

/**
 * Save task (create new or update existing).
 * @param {object} component - The Alpine component instance (this)
 * @param {Function} showToast - Toast notification function
 */
export async function saveTask(component, showToast) {
    // Validate task data
    if (!component.editingTask.name.trim() || !component.editingTask.prompt.trim()) {
        alert('Task name and prompt are required');
        return;
    }

    try {
        let apiEndpoint, taskData;

        // Prepare task data
        taskData = {
            name: component.editingTask.name,
            system_prompt: component.editingTask.system_prompt || '',
            prompt: component.editingTask.prompt || '',
            state: component.editingTask.state || 'idle',
            timezone: getUserTimezone()
        };

        if (component.isCreating && component.editingTask.project) {
            if (component.editingTask.project.name) {
                taskData.project_name = component.editingTask.project.name;
            }
            if (component.editingTask.project.color) {
                taskData.project_color = component.editingTask.project.color;
            }
        }

        // Process attachments - now always stored as array
        taskData.attachments = Array.isArray(component.editingTask.attachments)
            ? component.editingTask.attachments
                .map(line => typeof line === 'string' ? line.trim() : line)
                .filter(line => line && line.trim().length > 0)
            : [];

        // Handle task type specific data
        if (component.editingTask.type === 'scheduled') {
            // Ensure schedule is properly formatted as an object
            if (typeof component.editingTask.schedule === 'string') {
                const parts = component.editingTask.schedule.split(' ');
                taskData.schedule = {
                    minute: parts[0] || '*',
                    hour: parts[1] || '*',
                    day: parts[2] || '*',
                    month: parts[3] || '*',
                    weekday: parts[4] || '*',
                    timezone: getUserTimezone()
                };
            } else {
                taskData.schedule = {
                    ...component.editingTask.schedule,
                    timezone: component.editingTask.schedule.timezone || getUserTimezone()
                };
            }
            delete taskData.token;
            delete taskData.plan;
        } else if (component.editingTask.type === 'adhoc') {
            if (!component.editingTask.token) {
                component.editingTask.token = component.generateRandomToken();
                console.log('Generated new token for adhoc task:', component.editingTask.token);
            }

            console.log('Setting token in taskData:', component.editingTask.token);
            taskData.token = component.editingTask.token;

            delete taskData.schedule;
            delete taskData.plan;
        } else if (component.editingTask.type === 'planned') {
            if (!component.editingTask.plan) {
                component.editingTask.plan = {
                    todo: [],
                    in_progress: null,
                    done: []
                };
            }

            if (!Array.isArray(component.editingTask.plan.todo)) {
                component.editingTask.plan.todo = [];
            }

            if (!Array.isArray(component.editingTask.plan.done)) {
                component.editingTask.plan.done = [];
            }

            // Validate each date in the todo list
            const validatedTodo = [];
            for (const dateStr of component.editingTask.plan.todo) {
                try {
                    const date = new Date(dateStr);
                    if (!isNaN(date.getTime())) {
                        validatedTodo.push(date.toISOString());
                    } else {
                        console.warn(`Skipping invalid date in todo list: ${dateStr}`);
                    }
                } catch (error) {
                    console.warn(`Error processing date: ${error.message}`);
                }
            }

            component.editingTask.plan.todo = validatedTodo;
            component.editingTask.plan.todo.sort();

            taskData.plan = {
                todo: component.editingTask.plan.todo,
                in_progress: component.editingTask.plan.in_progress,
                done: component.editingTask.plan.done || []
            };

            console.log('Planned task plan data:', JSON.stringify(taskData.plan, null, 2));

            delete taskData.schedule;
            delete taskData.token;
        }

        // Determine if creating or updating
        if (component.isCreating) {
            apiEndpoint = '/scheduler_task_create';
        } else {
            apiEndpoint = '/scheduler_task_update';
            taskData.task_id = component.editingTask.uuid;
        }

        // Debug: Log the final task data being sent
        console.log('Final task data being sent to API:', JSON.stringify(taskData, null, 2));

        // Make API request
        const response = await api.fetchApi(apiEndpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(taskData)
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || 'Failed to save task');
        }

        const responseData = await response.json();

        showToast(component.isCreating ? 'Task created successfully' : 'Task updated successfully', 'success');

        // Immediately update the UI if the response includes the task
        if (responseData && responseData.task) {
            console.log('Task received in response:', responseData.task);

            if (component.isCreating) {
                component.tasks = [...component.tasks, responseData.task];
            } else {
                component.tasks = component.tasks.map(t =>
                    t.uuid === responseData.task.uuid ? responseData.task : t
                );
            }

            component.updateTasksUI();
        } else {
            await fetchTasks(component, showToast);
        }

        // Clean up Flatpickr instances
        const destroyFlatpickr = (inputId) => {
            const input = document.getElementById(inputId);
            if (input && input._flatpickr) {
                input._flatpickr.destroy();
            }
        };

        if (component.isCreating) {
            destroyFlatpickr('newPlannedTime-create');
        } else if (component.isEditing) {
            destroyFlatpickr('newPlannedTime-edit');
        }

        // Reset task data and form state
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
        component.isCreating = false;
        component.isEditing = false;
        document.querySelector('[x-data="schedulerSettings"]')?.removeAttribute('data-editing-state');
    } catch (error) {
        console.error('Error saving task:', error);
        showToast('Failed to save task: ' + error.message, 'error');
    }
}

/**
 * Run a task immediately.
 * @param {object} component - The Alpine component instance (this)
 * @param {string} taskId - Task UUID
 * @param {Function} showToast - Toast notification function
 */
export async function runTask(component, taskId, showToast) {
    try {
        const response = await api.fetchApi('/scheduler_task_run', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                task_id: taskId,
                timezone: getUserTimezone()
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data?.error || 'Failed to run task');
        }

        const toastMessage = data.warning || data.message || 'Task started successfully';
        const toastType = data.warning ? 'warning' : 'success';
        showToast(toastMessage, toastType);

        // Refresh task list
        fetchTasks(component, showToast);
    } catch (error) {
        console.error('Error running task:', error);
        showToast('Failed to run task: ' + error.message, 'error');
    }
}

/**
 * Reset a task's state to idle.
 * @param {object} component - The Alpine component instance (this)
 * @param {string} taskId - Task UUID
 * @param {Function} showToast - Toast notification function
 */
export async function resetTaskState(component, taskId, showToast) {
    try {
        const task = component.tasks.find(t => t.uuid === taskId);
        if (!task) {
            showToast('Task not found', 'error');
            return;
        }

        if (task.state === 'idle') {
            showToast('Task is already in idle state', 'info');
            return;
        }

        component.showLoadingState = true;

        const response = await api.fetchApi('/scheduler_task_update', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                task_id: taskId,
                state: 'idle',
                timezone: getUserTimezone()
            })
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || 'Failed to reset task state');
        }

        showToast('Task state reset to idle', 'success');

        await fetchTasks(component, showToast);
        component.showLoadingState = false;
    } catch (error) {
        console.error('Error resetting task state:', error);
        showToast('Failed to reset task state: ' + error.message, 'error');
        component.showLoadingState = false;
    }
}

/**
 * Delete a single task.
 * @param {object} component - The Alpine component instance (this)
 * @param {string} taskId - Task UUID
 * @param {Function} showToast - Toast notification function
 */
export async function deleteTask(component, taskId, showToast) {
    const confirmed = await Alpine.store('confirmation').confirm(
        'Are you sure you want to delete this task? This action cannot be undone.',
        { target: document.activeElement }
    );
    if (!confirmed) {
        return;
    }

    try {
        // if we delete selected context, switch to another first
        await chatsStore.switchFromContext(taskId);

        const response = await api.fetchApi('/scheduler_task_delete', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                task_id: taskId,
                timezone: getUserTimezone()
            })
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || 'Failed to delete task');
        }

        showToast('Task deleted successfully', 'success');

        // If we were viewing the detail of the deleted task, close the detail view
        if (component.selectedTaskForDetail && component.selectedTaskForDetail.uuid === taskId) {
            component.closeTaskDetail();
        }

        // Remove from selection if it was selected
        component.selectedTaskIds = component.selectedTaskIds.filter(id => id !== taskId);

        // Immediately update UI without waiting for polling
        component.tasks = component.tasks.filter(t => t.uuid !== taskId);

        // Update UI using the shared function
        component.updateTasksUI();
    } catch (error) {
        console.error('Error deleting task:', error);
        showToast('Failed to delete task: ' + error.message, 'error');
    }
}

/**
 * Bulk delete selected tasks.
 * @param {object} component - The Alpine component instance (this)
 * @param {Function} showToast - Toast notification function
 */
export async function deleteSelectedTasks(component, showToast) {
    if (component.selectedTaskIds.length === 0) return;

    const confirmed = await Alpine.store('confirmation').confirm(
        `Are you sure you want to delete ${component.selectedTaskIds.length} tasks? This action cannot be undone.`,
        { target: document.activeElement }
    );
    if (!confirmed) {
        return;
    }

    component.showLoadingState = true;
    try {
        // Switch contexts for all tasks being deleted if they are current
        for (const taskId of component.selectedTaskIds) {
            await chatsStore.switchFromContext(taskId);
        }

        const response = await api.fetchApi('/scheduler_tasks_delete_bulk', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                task_ids: component.selectedTaskIds,
                timezone: getUserTimezone()
            })
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'Failed to delete tasks');
        }

        if (result.errors && result.errors.length > 0) {
            const errorMsg = result.errors.join('\n');
            showToast(`Deleted ${result.count_deleted} tasks. Errors:\n${errorMsg}`, 'warning');
        } else {
            showToast(`Successfully deleted ${result.count_deleted} tasks`, 'success');
        }

        // If any of the deleted tasks were in detail view, close it
        if (component.selectedTaskForDetail && component.selectedTaskIds.includes(component.selectedTaskForDetail.uuid)) {
            component.closeTaskDetail();
        }

        // Immediately update UI
        const deletedIds = component.selectedTaskIds;
        component.tasks = component.tasks.filter(t => !deletedIds.includes(t.uuid));
        component.selectedTaskIds = [];
        component.updateSelectionState();

        // Update UI using the shared function
        component.updateTasksUI();
    } catch (error) {
        console.error('Error during bulk deletion:', error);
        showToast('Failed to delete tasks: ' + error.message, 'error');
    } finally {
        component.showLoadingState = false;
    }
}

/**
 * Navigate to a task's chat and close the settings modal.
 * @param {object} component - The Alpine component instance (this)
 * @param {object} task - Task object
 * @param {Function} showToast - Toast notification function
 */
export async function navigateToTaskChat(component, task, showToast) {
    if (!task) return;

    try {
        // 1. Open or create the task chat using the scheduledTasks store
        const scheduledTasksStore = globalThis.Alpine ? globalThis.Alpine.store('scheduledTasks') : null;
        if (scheduledTasksStore && typeof scheduledTasksStore.openTaskChat === 'function') {
            await scheduledTasksStore.openTaskChat(task);
        } else {
            console.error('scheduledTasks store or openTaskChat method not found');
            showToast('Could not open task chat', 'error');
            return;
        }

        // 2. Close the settings modal
        const modalEl = document.getElementById('settingsModal');
        if (modalEl && globalThis.Alpine) {
            const modalData = globalThis.Alpine.$data(modalEl);
            if (modalData && typeof modalData.handleButton === 'function') {
                modalData.handleButton('cancel');
            } else if (modalData) {
                modalData.isOpen = false;
            }
        }
    } catch (e) {
        console.error('Error in navigateToTaskChat:', e);
        showToast('Error navigating to chat', 'error');
    }
}
