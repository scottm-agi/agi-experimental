import { createStore } from "./AlpineStore.js";
import { callJsonApi } from "./api.js";

const model = {
    file_access_enabled: true, // Default to true, will be updated from backend
    simple_chat_forced: false,
    hide_sub_agent_tiles: false,
    is_development: true,
    // Per-tab UI visibility flags (defaults match dev mode — all visible)
    history_enabled: true,
    scheduler_enabled: true,
    oauth_enabled: true,
    backup_enabled: true,
    projects_enabled: true,
    mcp_enabled: true,
    developer_tab_enabled: true,
    external_enabled: true,

    async init() {
        try {
            const data = await callJsonApi("/settings_get", null);
            if (data && data.settings) {
                // settings contains the flat settings object from Settings.convert_out()
                this.file_access_enabled = data.settings.file_access_enabled !== false;
                this.simple_chat_forced = data.settings.simple_chat_forced === true;
                this.hide_sub_agent_tiles = data.settings.hide_sub_agent_tiles === true;
                this.is_development = data.is_development !== false;
                // Per-tab UI visibility flags
                this.history_enabled = data.settings.history_enabled !== false;
                this.scheduler_enabled = data.settings.scheduler_enabled !== false;
                this.oauth_enabled = data.settings.oauth_enabled !== false;
                this.backup_enabled = data.settings.backup_enabled !== false;
                this.projects_enabled = data.settings.projects_enabled !== false;
                this.mcp_enabled = data.settings.mcp_enabled !== false;
                this.developer_tab_enabled = data.settings.developer_tab_enabled !== false;
                this.external_enabled = data.settings.external_enabled !== false;
                console.log("[SETTINGS_STORE] Feature flags loaded:", {
                    file_access: this.file_access_enabled,
                    history: this.history_enabled,
                    scheduler: this.scheduler_enabled,
                    developer: this.developer_tab_enabled,
                    production: data.settings.is_production,
                });
            }
        } catch (e) {
            console.error("[SETTINGS_STORE] Failed to load settings:", e);
        }
    }
};

export const store = createStore("settings", model);
