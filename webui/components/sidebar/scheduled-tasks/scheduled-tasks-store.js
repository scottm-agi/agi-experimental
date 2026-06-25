import { createStore } from "../../../js/AlpineStore.js";
import * as api from "../../../js/api.js";
import { getUptimeMinutes } from "../../../js/time-utils.js";

// Use globalThis to avoid circular dependency with index.js
const getSetContext = () => globalThis.setContext;
const getToastFetchError = () => globalThis.toastFetchError;
const getJustToast = () => globalThis.justToast;
// Helper to get confirmation store to avoid circular refs
const getConfirmationStore = () => {
  if (globalThis.Alpine && typeof globalThis.Alpine.store === "function") {
    return globalThis.Alpine.store("confirmation");
  }
  return null;
};

// Get chats store via Alpine to check if context exists
const getChatsStore = () => {
  if (globalThis.Alpine && typeof globalThis.Alpine.store === "function") {
    return globalThis.Alpine.store("chats");
  }
  return null;
};

/**
 * Scheduled Tasks Store
 * 
 * Manages scheduled/adhoc tasks from the scheduler system.
 * Displays tasks in sidebar and allows opening their dedicated chats.
 */
const model = {
  // Task list data from scheduler
  tasks: [],
  version: -1,

  // Project filter (null = all projects)
  _projectFilter: null,

  // UI state
  isLoading: false,
  isExpanded: true,
  tasksEnabled: true, // Global switch for automated tasks

  // Selection state
  selectedTasks: new Set(),

  // Note: Tasks are updated via main poll() in index.js, no separate polling needed
  // The applyTasks() method is called from poll() with response.tasks

  // Filter state
  filterType: "all", // all, scheduled, adhoc, planned
  filterState: "all", // all, idle, running, disabled, error

  /**
   * Get uptime in minutes for a task
   */
  getUptimeMinutes(lastRun) {
    return getUptimeMinutes(lastRun);
  },

  /**
   * Initialize the store
   */
  init() {
    // Load expanded state from localStorage
    const savedExpanded = localStorage.getItem("scheduledTasksExpanded");
    if (savedExpanded !== null) {
      this.isExpanded = savedExpanded === "true";
    }

    // Note: Tasks are loaded via main poll() - no separate fetch needed
    // This prevents duplicate API calls and reduces server load

    // Initialize project filter from localStorage
    const savedFilter = localStorage.getItem("projectFilter");
    if (savedFilter) {
      this._projectFilter = savedFilter === "null" ? null : savedFilter;
    }

    // Initialize tasksEnabled from current settings
    this.syncTasksEnabled();

    // Listen for project filter changes
    window.addEventListener("project-filter-changed", (event) => {
      this._projectFilter = event.detail.projectName;
    });
  },

  /**
   * Toggle expanded/collapsed state
   */
  toggleExpanded() {
    this.isExpanded = !this.isExpanded;
    localStorage.setItem("scheduledTasksExpanded", this.isExpanded.toString());
  },

  /**
   * Fetch tasks from the scheduler API (on-demand only)
   * Note: Regular updates come from main poll() via applyTasks()
   * This method is only for explicit refresh requests
   * @param {boolean} silent - If true, don't show loading state
   */
  async fetchTasks(silent = false) {
    if (!silent) {
      this.isLoading = true;
    }

    try {
      const response = await api.fetchApi("/scheduler_tasks_list");
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();

      if (data.tasks) {
        this.tasks = data.tasks;
      }
    } catch (e) {
      if (!silent) {
        console.error("Error fetching scheduled tasks:", e);
      }
    } finally {
      this.isLoading = false;
    }
  },

  /**
   * Get filtered tasks based on current filter settings
   * @returns {Array} Filtered tasks
   */
  get filteredTasks() {
    let filtered = this.tasks;

    if (this.filterType !== "all") {
      filtered = filtered.filter(t => t.type === this.filterType);
    }

    if (this.filterState !== "all") {
      filtered = filtered.filter(t => t.state === this.filterState);
    }

    // Filter by project if active (case-insensitive)
    if (this._projectFilter) {
      const filterLower = this._projectFilter.toLowerCase();
      filtered = filtered.filter(
        (t) => t.project?.name?.toLowerCase() === filterLower
      );
    }

    return filtered;
  },

  /**
   * Check if there are any tasks
   * @returns {boolean}
   */
  hasTasks() {
    return this.tasks.length > 0;
  },

  /**
   * Selection methods
   */
  toggleSelectTask(taskUuid) {
    if (this.selectedTasks.has(taskUuid)) {
      this.selectedTasks.delete(taskUuid);
    } else {
      this.selectedTasks.add(taskUuid);
    }
    // Trigger reactivity for Set in Alpine
    this.selectedTasks = new Set(this.selectedTasks);
  },

  selectAllTasks() {
    this.tasks.forEach(t => this.selectedTasks.add(t.uuid));
    this.selectedTasks = new Set(this.selectedTasks);
  },

  clearSelection() {
    this.selectedTasks.clear();
    this.selectedTasks = new Set();
  },

  toggleSelectAll() {
    if (this.selectedTasks.size === this.tasks.length && this.tasks.length > 0) {
      this.clearSelection();
    } else {
      this.selectAllTasks();
    }
  },

  async bulkDelete() {
    const taskIds = Array.from(this.selectedTasks);
    if (taskIds.length === 0) return;

    // Use standardized confirmation popover
    const confirmationStore = getConfirmationStore();
    if (confirmationStore) {
      const confirmed = await confirmationStore.confirm(`Are you sure you want to remove ${taskIds.length} scheduled tasks?`);
      if (!confirmed) return;
    } else {
      // Fallback
      if (!confirm(`Are you sure you want to remove ${taskIds.length} scheduled tasks?`)) {
        return;
      }
    }

    try {
      this.isLoading = true;
      const response = await api.fetchApi("/scheduler_tasks_delete_bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_ids: taskIds }),
      });

      const data = await response.json();
      if (data.success || data.ok) {
        const justToast = getJustToast();
        if (justToast) justToast(`Removed ${data.message || data.count + " tasks"}`, "success");
        this.clearSelection();
        await this.fetchTasks();
      } else {
        const justToast = getJustToast();
        if (justToast) justToast(data.message || "Failed to remove tasks", "error");
      }
    } catch (e) {
      console.error("Error removing selected scheduled tasks:", e);
    } finally {
      this.isLoading = false;
    }
  },

  /**
   * Get task count by state
   * @returns {Object} Counts by state
   */
  getStateCounts() {
    return {
      idle: this.tasks.filter(t => t.state === "idle").length,
      running: this.tasks.filter(t => t.state === "running").length,
      disabled: this.tasks.filter(t => t.state === "disabled").length,
      error: this.tasks.filter(t => t.state === "error").length,
    };
  },

  /**
   * Get status badge class for a task state
   * @param {string} state - Task state
   * @returns {string} CSS class
   */
  getStateBadgeClass(state) {
    const classes = {
      idle: "scheduled-status-idle",
      running: "scheduled-status-running",
      disabled: "scheduled-status-disabled",
      error: "scheduled-status-error",
    };
    return classes[state] || "scheduled-status-idle";
  },

  /**
   * Get status icon for a task state
   * @param {string} state - Task state
   * @returns {string} Emoji icon
   */
  getStateIcon(state) {
    const icons = {
      idle: "⏸️",
      running: "▶️",
      disabled: "🚫",
      error: "⚠️",
    };
    return icons[state] || "❓";
  },

  /**
   * Get type icon for a task type
   * @param {string} type - Task type
   * @returns {string} Emoji icon
   */
  getTypeIcon(type) {
    const icons = {
      scheduled: "🕐",
      adhoc: "⚡",
      planned: "📅",
    };
    return icons[type] || "📋";
  },

  /**
   * Humanize schedule for display
   * @param {Object} task - Task object
   * @returns {string} Human-readable schedule
   */
  humanizeSchedule(task) {
    if (task.type === "adhoc") return "Manual";
    if (task.type === "planned") return "Planned";
    if (task.type !== "scheduled" || !task.schedule) return "—";

    const s = task.schedule;

    // 1. Weekly (Mon, Tue, etc.)
    if (s.weekday !== "*" && s.day === "*" && s.month === "*") {
      const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
      if (!isNaN(parseInt(s.weekday))) {
        return days[parseInt(s.weekday)] || "Weekly";
      }
      return s.weekday.charAt(0).toUpperCase() + s.weekday.slice(1, 2); // Mon, Tue...
    }

    // 2. Daily Patterns
    if (s.day === "*" && s.month === "*" && s.weekday === "*") {
      // 0 0 * * * or 0 12 * * *
      if (s.hour !== "*" && (s.minute === "0" || s.minute === "*")) return "Daily";

      // 0 * * * *
      if (s.hour === "*" && s.minute === "0") return "Hourly";

      // */5 * * * *
      if (s.minute.startsWith("*/")) return `${s.minute.substring(2)} Mins`;

      // * * * * *
      if (s.hour === "*" && s.minute === "*") return "Minutely";

      // 15 * * * *
      if (s.hour === "*") return `${s.minute}m`;
    }

    return "Scheduled";
  },

  /**
   * Format schedule for display
   * @param {Object} task - Task object
   * @returns {string} Formatted schedule
   */
  formatSchedule(task) {
    return this.humanizeSchedule(task);
  },

  /**
   * Open or create a chat for a scheduled task
   * The chat name will be the task name
   * Scheduler tasks use their UUID as the context ID
   * @param {Object} task - Task object
   */
  async openTaskChat(task) {
    if (!task) return;

    const setContext = getSetContext();
    const justToast = getJustToast();

    // Scheduler tasks use their UUID as the context ID
    // Check for context_id first, then fall back to uuid
    const contextId = task.context_id || task.uuid;

    // If task has a context_id or has been run before, switch to that context
    // Note: We don't check chatsStore.contains() because scheduled task contexts
    // are stored in response.tasks, not response.contexts
    if (contextId && (task.context_id || task.last_run)) {
      if (setContext) setContext(contextId, true);
      if (justToast) justToast(`Opened chat: ${task.name}`, "info", 1500, "scheduled-task");
      return;
    }

    // Task has never been run - run it to create the context
    try {
      if (justToast) justToast(`Starting task: ${task.name}...`, "info", 2000, "scheduled-task");

      const response = await api.fetchApi(`/scheduler_task_run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: task.uuid }),
      });

      const data = await response.json();

      if (data.success && data.context_id) {
        if (setContext) setContext(data.context_id, true);
        if (justToast) justToast(`Started task: ${task.name}`, "success", 1500, "scheduled-task");
      } else if (data.context_id) {
        if (setContext) setContext(data.context_id, true);
      } else {
        // Task started but no context_id returned - use the task's context_id
        if (setContext) setContext(contextId, true);
        if (justToast) justToast(data.message || "Task started", "info", 1500, "scheduled-task");
      }

      // Refresh tasks to get updated state
      await this.fetchTasks();
    } catch (e) {
      const toastFetchError = getToastFetchError();
      if (toastFetchError) toastFetchError("Error running task", e);
    }
  },

  async runTask(taskUuid) {
    const task = this.tasks.find(t => t.uuid === taskUuid);
    if (!task) return;

    const justToast = getJustToast();
    const setContext = getSetContext();

    try {
      if (justToast) justToast(`Running task: ${task.name}...`, "info", 2000, "scheduled-task");

      const response = await api.fetchApi(`/scheduler_task_run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: task.uuid }),
      });

      const data = await response.json();

      if (data.success || data.ok) {
        if (justToast) justToast(`Task started: ${task.name}`, "success", 1500, "scheduled-task");
        if (data.context_id && setContext) {
          setContext(data.context_id, true);
        }
      } else {
        throw new Error(data.error || "Failed to run task");
      }

      // Refresh tasks to get updated state
      await this.fetchTasks();
    } catch (e) {
      const toastFetchError = getToastFetchError();
      if (toastFetchError) toastFetchError("Error running task", e);
    }
  },

  /**
   * Show a task's chat history
   * @param {string} taskUuid - Task UUID
   */
  async showTask(taskUuid) {
    const task = this.tasks.find(t => t.uuid === taskUuid);
    if (task) {
      await this.openTaskChat(task);
    }
  },

  /**
   * Reset a task state to idle (clears error state)
   * @param {string} taskUuid - Task UUID
   */
  async resetTaskState(taskUuid) {
    const justToast = getJustToast();
    try {
      const response = await api.fetchApi(`/scheduler_task_update`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_id: taskUuid,
          state: "idle"
        }),
      });

      const data = await response.json();

      if (data.ok) {
        if (justToast) justToast("Task state reset to IDLE", "success", 1500, "scheduled-task");
        // Update local tasks state immediately for responsive feel
        const task = this.tasks.find(t => t.uuid === taskUuid);
        if (task) task.state = "idle";
      } else {
        throw new Error(data.error || "Failed to reset task");
      }
    } catch (e) {
      const toastFetchError = getToastFetchError();
      if (toastFetchError) toastFetchError("Error resetting task", e);
    }
  },

  /**
   * Toggle global tasks enabled state
   */
  async toggleTasksEnabled() {
    const justToast = getJustToast();
    const oldValue = this.tasksEnabled;
    const newValue = !oldValue;

    // Optimistic update
    this.tasksEnabled = newValue;

    try {
      const response = await api.callJsonApi("/settings_set_delta", {
        tasks_enabled: newValue
      });

      if (!response.ok && !response.success) {
        throw new Error("Failed to save setting");
      }

      const status = newValue ? "ENABLED" : "DISABLED";
      if (justToast) justToast(`Automated tasks ${status}`, newValue ? "success" : "warning", 2000, "tasks-toggle");
    } catch (e) {
      // Revert on error
      this.tasksEnabled = oldValue;
      const toastFetchError = getToastFetchError();
      if (toastFetchError) toastFetchError("Error toggling tasks", e);
    }
  },

  /**
   * Sync tasksEnabled state from settings
   */
  async syncTasksEnabled() {
    try {
      const response = await api.fetchApi("/settings_get");
      if (response.ok) {
        const data = await response.json();
        const settings = data.settings || {};

        // tasks_enabled is nested in sections[id=tasks].fields[id=tasks_enabled].value
        const sections = settings.sections || [];
        for (const section of sections) {
          if (section.id === "tasks") {
            for (const field of section.fields || []) {
              if (field.id === "tasks_enabled") {
                this.tasksEnabled = field.value;
                return;
              }
            }
          }
        }

        // Fallback: check flat key (in case API format changes)
        if (settings.tasks_enabled !== undefined) {
          this.tasksEnabled = settings.tasks_enabled;
        }
      }
    } catch (e) {
      console.error("Error syncing tasksEnabled:", e);
    }
  },

  /**
   * Apply tasks from poll response
   * Called by poll() in index.js to update tasks from backend
   * @param {Array} tasks - Tasks array from backend poll response
   */
  applyTasks(tasks, version) {
    if (version !== undefined) this.version = version;
    if (Array.isArray(tasks)) {
      const hadTasks = this.tasks.length > 0;
      this.tasks = tasks;
      const hasTasks = this.tasks.length > 0;

      // Also sync tasksEnabled if provided in poll (assuming it might be added to poll later)
      // For now we rely on the initial load and manual toggles

      // Auto-expand sidebar section if we just received our first tasks
      if (!hadTasks && hasTasks) {
        try {
          if (globalThis.Alpine && typeof globalThis.Alpine.store === "function") {
            const sidebar = globalThis.Alpine.store("sidebar");
            if (sidebar && !sidebar.isSectionOpen("scheduled")) {
              sidebar.toggleSection("scheduled");
            }
          }
        } catch (e) {
          console.warn("Could not auto-expand scheduled tasks sidebar section", e);
        }
      }
    }
  },

  /**
   * Open settings modal to scheduler tab
   */
  openSchedulerSettings() {
    // Trigger click on settings button to open modal
    const settingsButton = document.getElementById("settings");
    if (settingsButton) {
      settingsButton.click();

      // Wait for modal to open, then switch to scheduler tab
      setTimeout(() => {
        const modalEl = document.getElementById("settingsModal");
        if (modalEl && globalThis.Alpine) {
          const modalData = Alpine.$data(modalEl);
          if (modalData && modalData.switchTab) {
            modalData.switchTab("scheduler");
          }
        }
      }, 100);
    }
  },

  /**
   * Toggle an individual task's state between IDLE and DISABLED
   * @param {string} taskUuid - The UUID of the task to toggle
   */
  async toggleTaskState(taskUuid) {
    const justToast = getJustToast();
    const task = this.tasks.find(t => t.uuid === taskUuid);
    if (!task) return;

    const oldState = task.state;
    // Possible states: idle, running, error, disabled, pending
    // We toggle between disabled and idle (which represents active)
    const newState = oldState === "disabled" ? "idle" : "disabled";

    // Optimistic update
    task.state = newState;

    try {
      const response = await api.callJsonApi("/scheduler_task_update", {
        task_id: taskUuid,
        state: newState
      });

      if (!response.ok && !response.success) {
        throw new Error("Failed to update task state");
      }

      const status = newState === "idle" ? "ACTIVATED" : "DISABLED";
      if (justToast) justToast(`Task ${status}`, newState === "idle" ? "success" : "warning", 2000, `task-toggle-${taskUuid}`);
    } catch (e) {
      // Revert on error
      task.state = oldState;
      const toastFetchError = getToastFetchError();
      if (toastFetchError) toastFetchError("Error updating task", e);
    }
  },
};

const store = createStore("scheduledTasks", model);

export { store };
