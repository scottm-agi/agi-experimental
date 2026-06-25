import { createStore } from "../js/AlpineStore.js";
import { fetchApi } from "../js/api.js";

const model = {
    status: 'unknown',
    errors: [],
    config: {},
    metrics: {
        token_summary: { total_tokens: 0, total_estimated_cost: 0.0 },
        disk_usage: { used_pct: 0 },
        supervisor_stats: { total_interventions: 0 }
    },
    _initialized: false,

    init() {
        if (this._initialized) return;
        this._initialized = true;
        this.pollStatus();
        this.pollMetrics();

        // Poll status every 5s if not ready, 30s if ready
        this._pollInterval = setInterval(() => {
            if (this.status !== 'ready') this.pollStatus();
        }, 5000);

        this._slowPollInterval = setInterval(() => {
            if (this.status === 'ready') {
                this.pollStatus();
                this.pollMetrics();
            }
        }, 30000);
    },

    async pollStatus() {
        try {
            const response = await fetchApi("/health");
            if (response.ok) {
                const data = await response.json();
                this.status = data.init_status || 'ready';
                this.errors = data.init_errors || [];
                this.config = data.config_status || {};
            }
        } catch (e) {
            console.warn("[SystemStatus] Failed to poll status:", e);
        }
    },

    async pollMetrics() {
        try {
            const response = await fetchApi("/api/v1/system/metrics", {
                method: "POST",
                body: JSON.stringify({ action: "get_dashboard_metrics", days: 1 })
            });
            if (response.ok) {
                const data = await response.json();
                this.metrics = data;
                // Add disk usage if not provided by aggregate_dashboard_metrics
                if (!this.metrics.disk_usage) {
                    this.metrics.disk_usage = { used_pct: 0 };
                }
            }
        } catch (e) {
            console.warn("[SystemStatus] Failed to poll metrics:", e);
        }
    },

    isInitializing() {
        return this.status !== 'ready' && this.status !== 'error' && this.status !== 'unknown';
    },

    isReady() {
        return this.status === 'ready';
    },

    hasError() {
        return this.status === 'error';
    },

    needsSetup() {
        if (!this.config) return false;
        return this.config.chat_model === false || this.config.dotenv_exists === false;
    }
};

export const store = createStore("systemStatus", model);
