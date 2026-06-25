/**
 * Task Scheduler Component for Settings Modal
 * Manages scheduled and ad-hoc tasks through a dedicated settings tab.
 *
 * Core Alpine.js shell — delegates to extracted modules:
 *   - scheduler-api.js      (API communication)
 *   - scheduler-display.js  (formatting, filtering, sorting, debug)
 *   - scheduler-forms.js    (create/edit forms, Flatpickr, project dropdown)
 */

import { getUserTimezone } from './time-utils.js';
import { store as chatsStore } from "../components/sidebar/chats/chats-store.js";
import { store as notificationsStore } from "../components/notifications/notification-store.js";
import { store as projectsStore } from "../components/projects/projects-store.js";
import * as api from "./api.js";

// --- Sub-module imports ---
import * as schedulerApi from './scheduler-api.js';
import * as schedulerDisplay from './scheduler-display.js';
import * as schedulerForms from './scheduler-forms.js';

// Ensure the showToast function is available
const showToast = function (message, type = 'info') {
    // Use new frontend notification system
    switch (type.toLowerCase()) {
        case 'error':
            return notificationsStore.frontendError(message, "Scheduler", 5);
        case 'success':
            return notificationsStore.frontendInfo(message, "Scheduler", 3);
        case 'warning':
            return notificationsStore.frontendWarning(message, "Scheduler", 4);
        case 'info':
        default:
            return notificationsStore.frontendInfo(message, "Scheduler", 3);
    }
};

// Define the full component implementation
const fullComponentImplementation = function () {
    return {
        // --- State properties ---
        tasks: [],
        isLoading: true,
        selectedTask: null,
        expandedTaskId: null,
        sortField: 'createdAt',
        sortDirection: 'desc',
        filterType: 'all',  // all, scheduled, adhoc, planned
        filterState: 'all',  // all, idle, running, disabled, error
        pollingInterval: null,
        pollingActive: false,
        selectedFrequency: 'custom',
        editingTask: {
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
        },
        projectOptions: [],
        selectedProjectSlug: '',
        projectSearchQuery: '',
        isProjectDropdownOpen: false,
        isCreating: false,
        isEditing: false,
        showLoadingState: false,
        viewMode: 'list',
        selectedTaskForDetail: null,
        attachmentsText: '',
        taskSearchQuery: '',
        filteredTasks: [],
        hasNoTasks: true,
        selectedTaskIds: [],
        allTasksSelected: false,
        someTasksSelected: false,

        // ================================================================
        // Lifecycle
        // ================================================================

        init() {
            this.tasks = [];
            this.isLoading = true;
            this.hasNoTasks = true;
            this.filterType = 'all';
            this.filterState = 'all';
            this.sortField = 'createdAt';
            this.sortDirection = 'desc';
            this.pollingInterval = null;
            this.pollingActive = false;

            this.startPolling();
            this.fetchTasks();

            // Tab selection handler
            document.addEventListener('click', (event) => {
                const clickedTab = event.target.closest('.settings-tab');
                if (clickedTab && clickedTab.getAttribute('data-tab') === 'scheduler') {
                    setTimeout(() => {
                        this.fetchTasks();
                    }, 100);
                }
            });

            // Watchers for reactive updates
            this.$watch('tasks', () => {
                this.computeFilteredTasks();
                this.updateTasksUI();
            });

            this.$watch('filterType', () => {
                this.computeFilteredTasks();
                this.updateTasksUI();
            });

            this.$watch('filterState', () => {
                this.computeFilteredTasks();
                this.updateTasksUI();
            });

            // Restore view mode
            this.viewMode = localStorage.getItem('scheduler_view_mode') || 'list';
            this.selectedTask = null;
            this.expandedTaskId = null;
            this.editingTask = {
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
                token: this.generateRandomToken ? this.generateRandomToken() : '',
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
            this.refreshProjectOptions();

            // Initialize Flatpickr after Alpine is fully initialized
            this.$nextTick(() => {
                setTimeout(() => {
                    if (this.isCreating) {
                        this.initFlatpickr('create');
                    } else if (this.isEditing) {
                        this.initFlatpickr('edit');
                    }
                }, 100);
            });

            // Cleanup on component destruction
            this.$cleanup = () => {
                console.log('Cleaning up schedulerSettings component');
                this.stopPolling();

                const createInput = document.getElementById('newPlannedTime-create');
                if (createInput && createInput._flatpickr) {
                    createInput._flatpickr.destroy();
                }

                const editInput = document.getElementById('newPlannedTime-edit');
                if (editInput && editInput._flatpickr) {
                    editInput._flatpickr.destroy();
                }
            };
        },

        // ================================================================
        // Polling
        // ================================================================

        startPolling() {
            if (this.pollingInterval) {
                console.log('Polling already active, not starting again');
                return;
            }

            console.log('Starting task polling');
            this.pollingActive = true;

            this.fetchTasks();
            this.pollingInterval = setInterval(() => {
                if (this.pollingActive) {
                    this.fetchTasks();
                }
            }, 10000);
        },

        stopPolling() {
            console.log('Stopping task polling');
            this.pollingActive = false;

            if (this.pollingInterval) {
                clearInterval(this.pollingInterval);
                this.pollingInterval = null;
            }
        },

        // ================================================================
        // API delegates (scheduler-api.js)
        // ================================================================

        async fetchTasks() {
            return schedulerApi.fetchTasks(this, showToast);
        },

        async saveTask() {
            return schedulerApi.saveTask(this, showToast);
        },

        async runTask(taskId) {
            return schedulerApi.runTask(this, taskId, showToast);
        },

        async resetTaskState(taskId) {
            return schedulerApi.resetTaskState(this, taskId, showToast);
        },

        async deleteTask(taskId) {
            return schedulerApi.deleteTask(this, taskId, showToast);
        },

        async deleteSelectedTasks() {
            return schedulerApi.deleteSelectedTasks(this, showToast);
        },

        async navigateToTaskChat(task) {
            return schedulerApi.navigateToTaskChat(this, task, showToast);
        },

        // ================================================================
        // Display delegates (scheduler-display.js)
        // ================================================================

        formatDate(dateString) {
            return schedulerDisplay.formatDate(dateString);
        },

        formatPlan(task) {
            return schedulerDisplay.formatPlan(task);
        },

        formatSchedule(task) {
            return schedulerDisplay.formatSchedule(task);
        },

        getStateBadgeClass(state) {
            return schedulerDisplay.getStateBadgeClass(state);
        },

        changeSort(field) {
            return schedulerDisplay.changeSort(this, field);
        },

        toggleTaskExpand(taskId) {
            return schedulerDisplay.toggleTaskExpand(this, taskId);
        },

        showTaskDetail(taskId) {
            return schedulerDisplay.showTaskDetail(this, taskId, showToast);
        },

        closeTaskDetail() {
            return schedulerDisplay.closeTaskDetail(this);
        },

        computeFilteredTasks() {
            return schedulerDisplay.computeFilteredTasks(this);
        },

        sortTasks(tasks) {
            return schedulerDisplay.sortTasks(this, tasks);
        },

        updateTasksUI() {
            return schedulerDisplay.updateTasksUI(this);
        },

        testFiltering() {
            return schedulerDisplay.testFiltering(this);
        },

        debugTasks() {
            return schedulerDisplay.debugTasks(this);
        },

        toggleTaskSelection(taskId) {
            return schedulerDisplay.toggleTaskSelection(this, taskId);
        },

        isTaskSelected(taskId) {
            return schedulerDisplay.isTaskSelected(this, taskId);
        },

        toggleSelectAllTasks() {
            return schedulerDisplay.toggleSelectAllTasks(this);
        },

        updateSelectionState() {
            return schedulerDisplay.updateSelectionState(this);
        },

        // ================================================================
        // Form delegates (scheduler-forms.js)
        // ================================================================

        deriveActiveProject() {
            return schedulerForms.deriveActiveProject(chatsStore);
        },

        formatProjectName(project) {
            return schedulerForms.formatProjectName(project);
        },

        formatProjectLabel(project) {
            return schedulerForms.formatProjectLabel(project);
        },

        async refreshProjectOptions() {
            return schedulerForms.refreshProjectOptions(this);
        },

        onProjectSelect(slug) {
            return schedulerForms.onProjectSelect(this, slug);
        },

        getFilteredProjectOptions() {
            return schedulerForms.getFilteredProjectOptions(this);
        },

        toggleProjectDropdown() {
            return schedulerForms.toggleProjectDropdown(this);
        },

        closeProjectDropdown() {
            return schedulerForms.closeProjectDropdown(this);
        },

        selectProjectFromDropdown(project) {
            return schedulerForms.selectProjectFromDropdown(this, project);
        },

        openCreateProjectModal() {
            return schedulerForms.openCreateProjectModal(this);
        },

        extractTaskProject(task) {
            return schedulerForms.extractTaskProject(task);
        },

        formatTaskProject(task) {
            return schedulerForms.formatTaskProject(task);
        },

        async startCreateTask() {
            return schedulerForms.startCreateTask(this, chatsStore);
        },

        async startEditTask(taskId) {
            return schedulerForms.startEditTask(this, taskId, showToast);
        },

        cancelEdit() {
            return schedulerForms.cancelEdit(this);
        },

        setFrequency(cron) {
            return schedulerForms.setFrequency(this, cron);
        },

        initDateTimeInput(event) {
            return schedulerForms.initDateTimeInput(this, event);
        },

        generateRandomToken() {
            return schedulerForms.generateRandomToken();
        },

        initFlatpickr(mode = 'all') {
            return schedulerForms.initFlatpickr(this, mode);
        },

        // ================================================================
        // Computed properties (getter/setter)
        // ================================================================

        get attachmentsText() {
            const attachments = Array.isArray(this.editingTask.attachments)
                ? this.editingTask.attachments
                : [];
            return attachments.join('\n');
        },

        set attachmentsText(value) {
            if (typeof value === 'string') {
                this.editingTask.attachments = value.split('\n');
            } else {
                this.editingTask.attachments = [];
            }
        },
    };
};


// Only define the component if it doesn't already exist or extend the existing one
if (!window.schedulerSettings) {
    console.log('Defining schedulerSettings component from scratch');
    window.schedulerSettings = fullComponentImplementation;
} else {
    console.log('Extending existing schedulerSettings component');
    // Store the original function
    const originalSchedulerSettings = window.schedulerSettings;

    // Replace with enhanced version that merges the pre-initialized stub with the full implementation
    window.schedulerSettings = function () {
        // Get the base pre-initialized component
        const baseComponent = originalSchedulerSettings();

        // Create a backup of the original init function
        const originalInit = baseComponent.init || function () { };

        // Create our enhanced init function that adds the missing functionality
        baseComponent.init = function () {
            // Call the original init if it exists
            originalInit.call(this);

            console.log('Enhanced init running: adding missing methods to component');

            // Get the full implementation
            const fullImpl = fullComponentImplementation();

            // Register all implementation properties and methods (except init) directly
            Object.keys(fullImpl).forEach((key) => {
                if (key === 'init') {
                    return;
                }
                // Check if this is a getter/setter - need to copy them properly
                const descriptor = Object.getOwnPropertyDescriptor(fullImpl, key);
                if (descriptor && (descriptor.get || descriptor.set)) {
                    // It's a getter/setter - copy it using defineProperty to preserve reactivity
                    Object.defineProperty(this, key, descriptor);
                } else {
                    // It's a regular property or method - copy directly
                    this[key] = fullImpl[key];
                }
            });

            if (typeof this.refreshProjectOptions === 'function') {
                this.refreshProjectOptions();
            }

            // hack to expose deleteTask
            window.deleteTaskGlobal = this.deleteTask.bind(this);

            // Initialize essential properties if missing
            if (!Array.isArray(this.tasks)) {
                this.tasks = [];
            }

            if (!Array.isArray(this.projectOptions)) {
                this.projectOptions = [];
            }

            if (typeof this.selectedProjectSlug !== 'string') {
                this.selectedProjectSlug = '';
            }

            // Selection state initialization
            if (!Array.isArray(this.selectedTaskIds)) {
                this.selectedTaskIds = [];
            }
            this.allTasksSelected = false;
            this.someTasksSelected = false;

            // Ensure selection methods are present
            if (typeof this.updateSelectionState !== 'function') {
                this.updateSelectionState = function () {
                    const filteredUuids = (this.filteredTasks || []).map(t => t.uuid);
                    this.someTasksSelected = (this.selectedTaskIds || []).length > 0;
                    this.allTasksSelected = filteredUuids.length > 0 &&
                        filteredUuids.every(uuid => this.selectedTaskIds.includes(uuid));
                };
            }

            if (typeof this.toggleTaskSelection !== 'function') {
                this.toggleTaskSelection = function (taskId) {
                    if (this.selectedTaskIds.includes(taskId)) {
                        this.selectedTaskIds = this.selectedTaskIds.filter(id => id !== taskId);
                    } else {
                        this.selectedTaskIds.push(taskId);
                    }
                    this.updateSelectionState();
                };
            }

            if (typeof this.isTaskSelected !== 'function') {
                this.isTaskSelected = function (taskId) {
                    return (this.selectedTaskIds || []).includes(taskId);
                };
            }

            if (typeof this.toggleSelectAllTasks !== 'function') {
                this.toggleSelectAllTasks = function () {
                    if (this.allTasksSelected) {
                        this.selectedTaskIds = [];
                    } else {
                        this.selectedTaskIds = (this.filteredTasks || []).map(task => task.uuid);
                    }
                    this.updateSelectionState();
                };
            }

            // Make sure attachmentsText getter/setter are defined
            if (!Object.getOwnPropertyDescriptor(this, 'attachmentsText')?.get) {
                Object.defineProperty(this, 'attachmentsText', {
                    get: function () {
                        const attachments = Array.isArray(this.editingTask?.attachments)
                            ? this.editingTask.attachments
                            : [];
                        return attachments.join('\n');
                    },
                    set: function (value) {
                        if (!this.editingTask) {
                            this.editingTask = {
                                attachments: [],
                                project: null,
                                dedicated_context: true,
                            };
                        }

                        if (typeof value === 'string') {
                            this.editingTask.attachments = value.split('\n');
                        } else {
                            this.editingTask.attachments = [];
                        }
                    }
                });
            }

            // Add methods for updating filteredTasks directly
            if (typeof this.updateFilteredTasks !== 'function') {
                this.updateFilteredTasks = function () {
                    if (typeof this.computeFilteredTasks === 'function') {
                        this.computeFilteredTasks();
                    } else {
                        console.error('computeFilteredTasks method not available');
                    }
                };
            }

            // Set up watchers to update filtered tasks when dependencies change
            this.$nextTick(() => {
                // Watch for changes to projectsStore.projectList to refresh dropdown after project creation
                const watchProjectList = () => {
                    let lastKnownLength = this.projectOptions.length;
                    setInterval(() => {
                        if (projectsStore && Array.isArray(projectsStore.projectList)) {
                            const storeLength = projectsStore.projectList.length;
                            if (storeLength !== lastKnownLength) {
                                console.log(`Project list changed: ${lastKnownLength} -> ${storeLength}, refreshing scheduler project options`);
                                lastKnownLength = storeLength;
                                this.refreshProjectOptions();
                            }
                        }
                    }, 1000);
                };
                watchProjectList();

                this.$watch('tasks', () => {
                    this.updateFilteredTasks();
                });

                this.$watch('filterType', () => {
                    this.updateFilteredTasks();
                });

                this.$watch('filterState', () => {
                    this.updateFilteredTasks();
                });

                this.$watch('sortField', () => {
                    this.updateFilteredTasks();
                });

                this.$watch('sortDirection', () => {
                    this.updateFilteredTasks();
                });

                // Initial update
                this.updateFilteredTasks();

                // Set up watcher for task type changes to initialize Flatpickr for planned tasks
                this.$watch('editingTask.type', (newType) => {
                    if (newType === 'planned') {
                        this.$nextTick(() => {
                            if (this.isCreating) {
                                this.initFlatpickr('create');
                            } else if (this.isEditing) {
                                this.initFlatpickr('edit');
                            }
                        });
                    }
                });

                // Initialize Flatpickr
                this.$nextTick(() => {
                    if (typeof this.initFlatpickr === 'function') {
                        this.initFlatpickr();
                    } else {
                        console.error('initFlatpickr is not available');
                    }
                });
            });

            // Try starting polling after a short delay
            setTimeout(() => {
                if (typeof this.startPolling === 'function') {
                    console.log('Starting polling from enhanced init');
                    this.startPolling();
                } else if (typeof this.fetchTasks === 'function') {
                    console.log('Falling back to fetchTasks (startPolling not available)');
                    this.pollingActive = true;
                    this.fetchTasks();
                } else {
                    console.error('fetchTasks still not available after enhancement');
                }
            }, 100);

            console.log('Enhanced init complete');
        };

        return baseComponent;
    };
}

// Force Alpine.js to register the component immediately
if (window.Alpine) {
    console.log('Alpine already loaded, registering schedulerSettings component now');
    window.Alpine.data('schedulerSettings', window.schedulerSettings);
} else {
    document.addEventListener('alpine:init', () => {
        console.log('Alpine:init - immediately registering schedulerSettings component');
        Alpine.data('schedulerSettings', window.schedulerSettings);
    });
}

// Add a document ready event handler to ensure the scheduler tab can be clicked on first load
document.addEventListener('DOMContentLoaded', function () {
    console.log('DOMContentLoaded - setting up scheduler tab click handler');
    const setupSchedulerTab = () => {
        const settingsModal = document.getElementById('settingsModal');
        if (!settingsModal) {
            setTimeout(setupSchedulerTab, 100);
            return;
        }

        document.addEventListener('click', function (e) {
            const schedulerTab = e.target.closest('.settings-tab[title="Task Scheduler"]');
            if (!schedulerTab) return;

            e.preventDefault();
            e.stopPropagation();

            try {
                const modalData = Alpine.$data(settingsModal);
                if (modalData.activeTab !== 'scheduler') {
                    modalData.switchTab('scheduler');
                }

                setTimeout(() => {
                    const schedulerElement = document.querySelector('[x-data="schedulerSettings"]');
                    if (schedulerElement) {
                        const schedulerData = Alpine.$data(schedulerElement);

                        if (typeof schedulerData.fetchTasks === 'function') {
                            schedulerData.fetchTasks();
                        } else {
                            console.error('fetchTasks is not a function on scheduler component');
                        }

                        if (typeof schedulerData.startPolling === 'function') {
                            schedulerData.startPolling();
                        } else {
                            console.error('startPolling is not a function on scheduler component');
                        }
                    } else {
                        console.error('Could not find scheduler component element');
                    }
                }, 100);
            } catch (err) {
                console.error('Error handling scheduler tab click:', err);
            }
        }, true);
    };

    setupSchedulerTab();
});
