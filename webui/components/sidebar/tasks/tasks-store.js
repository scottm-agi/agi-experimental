import { createStore } from "../../../js/AlpineStore.js";
import * as api from "../../../js/api.js";
import { getUptimeMinutes } from "../../../js/time-utils.js";

// Use globalThis to avoid circular dependency with index.js
// These functions are set on globalThis by index.js
const getGetContext = () => globalThis.getContext;
const getToastFetchError = () => globalThis.toastFetchError;
const getJustToast = () => globalThis.justToast;

/**
 * Session Tasks Store
 * 
 * Manages per-session task/todo lists for the current chat context.
 * Tasks are fetched from the backend API and displayed in the sidebar.
 */
const model = {
  // Task list data
  tasks: [],
  mission: "",
  owner: "andy",

  // Progress statistics
  progress: {
    total: 0,
    completed: 0,
    in_progress: 0,
    pending: 0,
    blocked: 0,
    failed: 0,
    skipped: 0,
    percent_complete: 0.0,
  },

  // UI state
  isLoading: false,
  isExpanded: true,
  lastFetchedContext: null,

  // Polling interval (ms) - 10 seconds when active, no polling when no tasks
  pollInterval: 10000,
  _pollTimer: null,

  // Queue system for sequential task processing
  queue: [],                    // Tasks waiting to be sent to agent
  isProcessingQueue: false,     // Whether queue processor is running
  currentQueueTaskId: null,     // ID of task currently being worked on
  _lastAgentActive: false,      // Track agent activity state for completion detection

  // Selection state
  selectedTasks: new Set(),

  toggleSelectTask(taskId) {
    if (this.selectedTasks.has(taskId)) {
      this.selectedTasks.delete(taskId);
    } else {
      this.selectedTasks.add(taskId);
    }
    // Trigger reactivity for Set in Alpine
    this.selectedTasks = new Set(this.selectedTasks);
  },

  selectAllTasks() {
    this.tasks.forEach(t => this.selectedTasks.add(t.id));
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

  async removeSelectedTasks() {
    const taskIds = Array.from(this.selectedTasks);
    if (taskIds.length === 0) return;

    const confirmed = await Alpine.store('confirmation').confirm(
      `Are you sure you want to remove ${taskIds.length} tasks?`,
      { target: document.activeElement }
    );
    if (!confirmed) return;

    const getContext = getGetContext();
    const contextId = getContext ? getContext() : null;
    if (!contextId) return;

    try {
      this.isLoading = true;
      const response = await api.fetchApi(`/api/session_tasks/${contextId}/bulk_delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_ids: taskIds }),
      });

      const data = await response.json();
      if (data.success) {
        const justToast = getJustToast();
        if (justToast) justToast(`Removed ${data.count} tasks`, "success");
        this.clearSelection();
        await this.fetchTasks();
      } else {
        const justToast = getJustToast();
        if (justToast) justToast(data.message || "Failed to remove tasks", "error");
      }
    } catch (e) {
      console.error("Error removing selected tasks:", e);
    } finally {
      this.isLoading = false;
    }
  },

  /**
   * Get uptime in minutes for a task
   */
  getUptimeMinutes(startTime) {
    return getUptimeMinutes(startTime);
  },

  /**
   * Initialize the store
   */
  init() {
    // Load expanded state from localStorage
    const savedExpanded = localStorage.getItem("tasksExpanded");
    if (savedExpanded !== null) {
      this.isExpanded = savedExpanded === "true";
    }

    // Listen for context changes
    window.addEventListener("context-changed", (event) => {
      this.clearSelection();
      // Update lastFetchedContext even if it is null (deselect case)
      this.lastFetchedContext = event.detail?.contextId ?? null;
      this.fetchTasks();
    });

    // Delay initial fetch to allow globalThis.getContext to be set by index.js
    // This ensures the context is available when we first try to fetch tasks
    setTimeout(() => {
      this.fetchTasks();
    }, 100);

    // Start polling
    this.startPolling();
  },

  /**
   * Toggle expanded/collapsed state
   */
  toggleExpanded() {
    this.isExpanded = !this.isExpanded;
    localStorage.setItem("tasksExpanded", this.isExpanded.toString());
  },

  /**
   * Start polling for task updates
   * Only polls when there are active (non-completed) tasks
   */
  startPolling() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
    }
    this._pollTimer = setInterval(() => {
      // Only poll if there are active tasks (pending, in_progress, or blocked)
      // Don't poll if there are no tasks - wait for context change or user action
      const hasActiveTasks = this.tasks.some(t =>
        t.status === "pending" || t.status === "in_progress" || t.status === "blocked"
      );
      if (hasActiveTasks) {
        this.fetchTasks(true); // silent fetch
      }
    }, this.pollInterval);
  },

  /**
   * Stop polling
   */
  stopPolling() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
  },

  /**
   * Fetch tasks from the backend API
   * @param {boolean} silent - If true, don't show loading state
   */
  async fetchTasks(silent = false) {
    const getContext = getGetContext();
    const contextId = getContext ? getContext() : null;
    if (!contextId) {
      this.tasks = [];
      this.mission = "";
      this.progress = this._emptyProgress();
      return;
    }

    // Skip if same context and silent (polling)
    if (silent && contextId === this.lastFetchedContext && this.tasks.length > 0) {
      // Still fetch to check for updates, but don't show loading
    }

    if (!silent) {
      this.isLoading = true;
    }

    try {
      const response = await api.fetchApi(`/api/session_tasks/${contextId}`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();

      if (data.success) {
        this.tasks = data.tasks || [];
        this.mission = data.mission || "";
        this.owner = data.owner || "andy";
        this.lastFetchedContext = contextId;

        // Update progress from tasks (avoid extra API call)
        this._updateProgressFromTasks();
      }
    } catch (e) {
      if (!silent) {
        console.error("Error fetching tasks:", e);
      }
    } finally {
      this.isLoading = false;
    }
  },

  /**
   * Fetch progress statistics
   * @param {string} contextId - Context ID
   */
  async fetchProgress(contextId) {
    try {
      const response = await api.fetchApi(`/api/session_tasks/${contextId}/progress`);
      if (response.ok) {
        const data = await response.json();
        if (data.success && data.progress) {
          this.progress = data.progress;
        }
      }
    } catch (e) {
      console.error("Error fetching progress:", e);
    }
  },

  /**
   * Add a new task to the queue
   * Tasks are queued and processed sequentially - one at a time
   * @param {string} description - Task description
   * @param {number} priority - Priority (1-5)
   */
  async addTask(description, priority = 3) {
    const getContext = getGetContext();
    const contextId = getContext ? getContext() : null;
    if (!contextId || !description) return;

    try {
      // Create task in backend with pending status
      const response = await api.fetchApi(`/api/session_tasks/${contextId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          description,
          priority,
          created_by: "user",
        }),
      });

      const data = await response.json();

      if (data.success) {
        const taskId = data.task?.id || data.task_id;

        // Add to queue
        this.queue.push({
          id: taskId,
          description,
          priority,
          contextId,
          queuedAt: Date.now(),
        });

        const queuePosition = this.queue.length;
        const justToast = getJustToast();
        if (justToast) justToast(`Task queued (#${queuePosition})`, "success", 1500, "task-add");

        await this.fetchTasks();

        // Start processing queue if not already running
        this._processQueue();
      } else {
        const justToast = getJustToast();
        if (justToast) justToast(data.message || "Failed to add task", "error", 3000, "task-add");
      }
    } catch (e) {
      const toastFetchError = getToastFetchError();
      if (toastFetchError) toastFetchError("Error adding task", e);
    }
  },

  /**
   * Process the task queue - sends one task at a time to the agent
   * Waits for agent to become idle before sending next task
   * @private
   */
  async _processQueue() {
    // Don't start if already processing or queue is empty
    if (this.isProcessingQueue || this.queue.length === 0) {
      return;
    }

    this.isProcessingQueue = true;

    while (this.queue.length > 0) {
      const task = this.queue[0]; // Peek at first task
      this.currentQueueTaskId = task.id;

      // CRITICAL: Wait for agent to be idle BEFORE sending task.
      // If we send via /message_async while agent is busy, the message
      // gets treated as an intervention (silently absorbed into the
      // current monologue) instead of being processed as a standalone task.
      await this._waitForAgentIdle();

      // Mark task as in_progress in backend
      await this.startTask(task.id);

      // Send to agent (now guaranteed idle)
      await this._sendTaskToAgent(task.description, task.contextId, task.id);

      // Wait for THIS task's monologue to complete
      // Small initial delay to let the agent start processing before polling
      await new Promise(resolve => setTimeout(resolve, 3000));
      await this._waitForAgentIdle();

      // Mark task as completed
      await this.completeTask(task.id, "Completed via queue");

      // Remove from queue
      this.queue.shift();
      this.currentQueueTaskId = null;

      // Small delay between tasks
      await new Promise(resolve => setTimeout(resolve, 500));
    }

    this.isProcessingQueue = false;
  },

  /**
   * Wait for the agent to become idle (no active progress)
   * Uses polling to detect when agent finishes current work
   * @private
   */
  async _waitForAgentIdle() {
    const maxWaitTime = 10 * 60 * 1000; // 10 minutes max
    const pollInterval = 2000; // Check every 2 seconds
    const idleThreshold = 3; // Need 3 consecutive idle checks

    let idleCount = 0;
    const startTime = Date.now();

    // globalThis._agentIdle is set by the poll loop in index.js from
    // the backend's "agent_idle" field, which checks whether the Python
    // DeferredTask (context.task) is still alive. This is the definitive
    // idle indicator — unlike "paused" (user-initiated pause) or the
    // progress bar's shiny-text CSS class (stays active during post-
    // monologue extensions like personalization analysis).

    while (Date.now() - startTime < maxWaitTime) {
      await new Promise(resolve => setTimeout(resolve, pollInterval));

      // Primary check: backend task lifecycle (definitive)
      const backendIdle = globalThis._agentIdle === true;

      // Secondary check: progress bar CSS class (visual indicator)
      const progressBar = document.getElementById("progress-bar");
      const visuallyIdle = !progressBar || !progressBar.classList.contains("shiny-text");

      // Agent is idle when EITHER the backend task is done OR the UI says idle
      const isIdle = backendIdle || visuallyIdle;

      if (isIdle) {
        idleCount++;
        if (idleCount >= idleThreshold) {
          // Agent has been idle for threshold checks - task is done
          return;
        }
      } else {
        idleCount = 0; // Reset if agent becomes active again
      }
    }

    // Timeout - proceed anyway
    console.warn("Agent idle wait timed out after 10 minutes");
  },

  /**
   * Send task to agent for execution
   * @param {string} taskDescription - The task description
   * @param {string} contextId - The chat context ID
   * @param {string} taskId - The task ID for reference
   * @private
   */
  async _sendTaskToAgent(taskDescription, contextId, taskId) {
    try {
      const queuePosition = this.queue.findIndex(t => t.id === taskId) + 1;
      const totalQueued = this.queue.length;

      let message = `**Task ${queuePosition}/${totalQueued}:** ${taskDescription}`;
      if (totalQueued > 1) {
        message += `\n\n_${totalQueued - 1} more task(s) queued after this._`;
      }

      const response = await api.fetchApi("/message_async", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          text: message,
          context: contextId,
        }),
      });

      const jsonResponse = await response.json();
      if (jsonResponse) {
        const justToast = getJustToast();
        if (justToast) justToast("Task sent to agent", "info", 1500, "task-agent");
      }
    } catch (e) {
      console.error("Error sending task to agent:", e);
      // Don't show error toast - task was added successfully, agent trigger is secondary
    }
  },

  /**
   * Get the current queue length
   * @returns {number} Number of tasks in queue
   */
  getQueueLength() {
    return this.queue.length;
  },

  /**
   * Check if a task is currently being processed
   * @param {string} taskId - Task ID to check
   * @returns {boolean} True if this task is currently being processed
   */
  isCurrentTask(taskId) {
    return this.currentQueueTaskId === taskId;
  },

  /**
   * Get queue position for a task (1-indexed, 0 if not in queue)
   * @param {string} taskId - Task ID to check
   * @returns {number} Queue position or 0 if not queued
   */
  getQueuePosition(taskId) {
    const index = this.queue.findIndex(t => t.id === taskId);
    return index >= 0 ? index + 1 : 0;
  },

  /**
   * Clear the queue (cancel pending tasks)
   */
  clearQueue() {
    this.queue = [];
    const justToast = getJustToast();
    if (justToast) justToast("Queue cleared", "info", 1500, "queue-clear");
  },

  /**
   * Start a task
   * @param {string} taskId - Task ID
   */
  async startTask(taskId) {
    const getContext = getGetContext();
    const contextId = getContext ? getContext() : null;
    if (!contextId || !taskId) return;

    try {
      const response = await api.fetchApi(`/api/session_tasks/${contextId}/${taskId}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });

      const data = await response.json();

      if (data.success) {
        await this.fetchTasks();
      } else {
        const justToast = getJustToast();
        if (justToast) justToast(data.message || "Failed to start task", "error", 3000, "task-start");
      }
    } catch (e) {
      const toastFetchError = getToastFetchError();
      if (toastFetchError) toastFetchError("Error starting task", e);
    }
  },

  /**
   * Complete a task
   * @param {string} taskId - Task ID
   * @param {string} result - Optional result summary
   */
  async completeTask(taskId, result = null) {
    const getContext = getGetContext();
    const contextId = getContext ? getContext() : null;
    if (!contextId || !taskId) return;

    try {
      const response = await api.fetchApi(`/api/session_tasks/${contextId}/${taskId}/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ result }),
      });

      const data = await response.json();

      if (data.success) {
        const justToast = getJustToast();
        if (justToast) justToast("Task completed", "success", 1500, "task-complete");
        await this.fetchTasks();
      } else {
        const justToast = getJustToast();
        if (justToast) justToast(data.message || "Failed to complete task", "error", 3000, "task-complete");
      }
    } catch (e) {
      const toastFetchError = getToastFetchError();
      if (toastFetchError) toastFetchError("Error completing task", e);
    }
  },

  /**
   * Fail a task
   * @param {string} taskId - Task ID
   * @param {string} error - Optional error message
   */
  async failTask(taskId, error = null) {
    const getContext = getGetContext();
    const contextId = getContext ? getContext() : null;
    if (!contextId || !taskId) return;

    try {
      const response = await api.fetchApi(`/api/session_tasks/${contextId}/${taskId}/fail`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ error }),
      });

      const data = await response.json();

      if (data.success) {
        await this.fetchTasks();
      } else {
        const justToast = getJustToast();
        if (justToast) justToast(data.message || "Failed to mark task as failed", "error", 3000, "task-fail");
      }
    } catch (e) {
      const toastFetchError = getToastFetchError();
      if (toastFetchError) toastFetchError("Error failing task", e);
    }
  },

  /**
   * Remove a task
   * @param {string} taskId - Task ID
   */
  async removeTask(taskId) {
    const getContext = getGetContext();
    const contextId = getContext ? getContext() : null;
    if (!contextId || !taskId) return;

    try {
      const response = await api.fetchApi(`/api/session_tasks/${contextId}/${taskId}`, {
        method: "DELETE",
      });

      const data = await response.json();

      if (data.success) {
        const justToast = getJustToast();
        if (justToast) justToast("Task removed", "success", 1500, "task-remove");
        await this.fetchTasks();
      } else {
        const justToast = getJustToast();
        if (justToast) justToast(data.message || "Failed to remove task", "error", 3000, "task-remove");
      }
    } catch (e) {
      const toastFetchError = getToastFetchError();
      if (toastFetchError) toastFetchError("Error removing task", e);
    }
  },

  /**
   * Get status icon for a task
   * @param {string} status - Task status
   * @returns {string} Emoji icon
   */
  getStatusIcon(status) {
    const icons = {
      pending: "⏳",
      in_progress: "🔄",
      completed: "✅",
      blocked: "🚫",
      failed: "❌",
      skipped: "⏭️",
    };
    return icons[status] || "❓";
  },

  /**
   * Get CSS class for task status
   * @param {string} status - Task status
   * @returns {string} CSS class
   */
  getStatusClass(status) {
    return `task-status-${status}`;
  },

  /**
   * Get priority label
   * @param {number} priority - Priority level (1-5)
   * @returns {string} Priority label
   */
  getPriorityLabel(priority) {
    const labels = {
      1: "Critical",
      2: "High",
      3: "Medium",
      4: "Low",
      5: "Optional",
    };
    return labels[priority] || "Medium";
  },

  /**
   * Check if there are any tasks
   * @returns {boolean}
   */
  hasTasks() {
    return this.tasks.length > 0;
  },

  /**
   * Get tasks grouped by status
   * @returns {Object} Tasks grouped by status
   */
  getTasksByStatus() {
    return {
      in_progress: this.tasks.filter(t => t.status === "in_progress"),
      pending: this.tasks.filter(t => t.status === "pending"),
      blocked: this.tasks.filter(t => t.status === "blocked"),
      completed: this.tasks.filter(t => t.status === "completed"),
      failed: this.tasks.filter(t => t.status === "failed"),
      skipped: this.tasks.filter(t => t.status === "skipped"),
    };
  },

  /**
   * Get active tasks (in progress + pending)
   * @returns {Array} Active tasks
   */
  getActiveTasks() {
    return this.tasks.filter(t =>
      t.status === "in_progress" || t.status === "pending"
    );
  },

  /**
   * Empty progress object
   * @returns {Object}
   */
  _emptyProgress() {
    return {
      total: 0,
      completed: 0,
      in_progress: 0,
      pending: 0,
      blocked: 0,
      failed: 0,
      skipped: 0,
      percent_complete: 0.0,
    };
  },

  /**
   * Apply tasks from poll response
   * Called by poll() in index.js to update tasks from backend
   * @param {Array} tasks - Tasks array from backend poll response
   */
  applyTasks(tasks) {
    if (Array.isArray(tasks)) {
      this.tasks = tasks;
      // Update progress based on new tasks
      this._updateProgressFromTasks();
    }
  },

  /**
   * Update progress statistics from current tasks
   * @private
   */
  _updateProgressFromTasks() {
    const total = this.tasks.length;
    const completed = this.tasks.filter(t => t.status === "completed").length;
    const in_progress = this.tasks.filter(t => t.status === "in_progress").length;
    const pending = this.tasks.filter(t => t.status === "pending").length;
    const blocked = this.tasks.filter(t => t.status === "blocked").length;
    const failed = this.tasks.filter(t => t.status === "failed").length;
    const skipped = this.tasks.filter(t => t.status === "skipped").length;

    this.progress = {
      total,
      completed,
      in_progress,
      pending,
      blocked,
      failed,
      skipped,
      percent_complete: total > 0 ? (completed / total) * 100 : 0,
    };
  },

  /**
   * Check if store contains a task with the given context ID
   * @param {string} contextId - Context ID to check
   * @returns {boolean} True if a task with this context exists
   */
  contains(contextId) {
    if (!contextId) return false;
    return this.tasks.some(t => t.context_id === contextId || t.id === contextId);
  },

  /**
   * Set the selected context ID
   * Used for UI highlighting and tracking current selection
   * @param {string} contextId - Context ID to select
   */
  setSelected(contextId) {
    this.lastFetchedContext = contextId;
  },

  /**
   * Get the first task's context ID
   * Used for fallback selection when current context is invalid
   * @returns {string|null} First task's context ID or null if no tasks
   */
  firstId() {
    if (this.tasks.length > 0) {
      return this.tasks[0].context_id || this.tasks[0].id || null;
    }
    return null;
  },
};

const store = createStore("tasks", model);

export { store };
