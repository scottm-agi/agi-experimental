import historySettings from '../components/settings/history/history-settings-store.js';
import commonPromptsSettings from '../components/settings/prompts/prompts-settings-store.js';
import { store as personalizationDashboardStore } from '../components/settings/personalization/personalization-dashboard-store.js';
import { callJsonApi, fetchApi } from '../js/api.js';
import * as device from './device.js';

/**
 * Registers all settings-related Alpine components and stores.
 * Handles race conditions where Alpine might already be initialized.
 */
let _settingsComponentsRegistered = false;

function registerSettings() {
    if (!window.Alpine) {
        console.warn('[settings.js] Alpine not available yet, skipping registration');
        return;
    }

    // Prevent double registration
    if (_settingsComponentsRegistered) {
        console.log('[settings.js] Components already registered, skipping');
        return;
    }
    _settingsComponentsRegistered = true;
    console.log('[settings.js] Registering settings components...');

    // Check if store already exists (from a previous partial registration)
    try {
        if (Alpine.store('root')) {
            console.log('[settings.js] Root store already exists');
        }
    } catch (e) {
        // Alpine.store error if doesn't exist is expected on first run
    }


    // Initialize the root store first
    Alpine.store('root', {
        activeTab: localStorage.getItem('settingsActiveTab') || 'agent',
        isOpen: false,

        toggleSettings() {
            this.isOpen = !this.isOpen;
        }
    });

    // Main settings modal component
    Alpine.data('settingsModal', () => ({
        isOpen: false,
        settings: { sections: [] },
        activeTab: localStorage.getItem('settingsActiveTab') || 'agent',
        isDevelopment: true, // Default to true, updated from backend
        isProduction: false, // Default to false, updated from backend
        isMobile: window.innerWidth < 768 || device.getInputType() === 'touch',
        resolvePromise: null,
        pollingInterval: null,
        sensitivePatterns: ["KEY", "TOKEN", "PASSWORD", "PWD", "SECRET", "AUTH", "CREDENTIAL", "APIKEY", "ACCESS", "PRIVATE", "CERT", "JSON", "SESSION", "PAT", "SID", "JWT"],

        isSensitiveKey(field) {
            if (!field) return false;
            if (field.isSecret) return true;
            const patterns = this.sensitivePatterns;
            const id = (field.id || "").toUpperCase();
            const title = (field.title || "").toUpperCase();
            return patterns.some(p => id.includes(p) || title.includes(p));
        },

        get filteredSections() {
            if (!this.settings || !this.settings.sections) return [];

            const customTabs = ['scheduler', 'projects', 'history'];
            if (customTabs.includes(this.activeTab)) {
                return [];
            }

            const sections = this.settings.sections.filter(section => section.tab === this.activeTab);

            // Dynamic field visibility - NO CLONING so changes propagate to this.settings
            // We only toggle hidden property which doesn't affect saved data
            return sections.map(section => {
                const profilesEnabledField = section.fields.find(f => f.id === 'agent_profiles_enabled');
                const profilesEnabled = profilesEnabledField ? profilesEnabledField.value : true;

                // Find the profile to edit if we are in the profiles section
                const profileToEditField = section.fields.find(f => f.id === 'agent_profile_to_edit');
                const profileToEdit = profileToEditField ? profileToEditField.value : null;

                section.fields.forEach(field => {
                    if (field.id === 'agent_profile') {
                        field.hidden = !profilesEnabled;
                    }

                    // Filter profile-specific fields based on selected profile
                    if (profileToEdit && field.id.startsWith('profile_')) {
                        // Use suffix matching to correctly identify profile name (handles max_tokens, ctx_length etc)
                        const suffixes = ['_provider', '_name', '_ctx_length', '_max_tokens', '_kwargs'];
                        let profileName = null;
                        for (const suffix of suffixes) {
                            if (field.id.endsWith(suffix)) {
                                profileName = field.id.substring(8, field.id.length - suffix.length);
                                break;
                            }
                        }

                        if (profileName) {
                            field.hidden = profileName !== profileToEdit;
                        }
                    }
                });
                return section;
            });
        },

        init() {
            // Sync with global store if present
            if (Alpine.store('root')) {
                this.activeTab = Alpine.store('root').activeTab;
            }

            // Watch for external open requests via the store if needed
            this.$watch('$store.root.isOpen', (val) => {
                if (val && !this.isOpen) this.openModal();
            });

            // Sync with store
            this.$watch('activeTab', (val) => {
                const rootStore = Alpine.store('root');
                if (rootStore) rootStore.activeTab = val;
                localStorage.setItem('settingsActiveTab', val);

                // Auto-scroll active tab into view
                this.$nextTick(() => {
                    const activeTabEl = document.querySelector('.settings-tab.active');
                    if (activeTabEl) {
                        activeTabEl.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
                    }

                    if (val === 'scheduler') {
                        this.initScheduler();
                    } else if (val === 'projects') {
                        this.initProjects();
                    } else if (val === 'history') {
                        this.initHistory();
                        this.initCommonPrompts();
                    }
                });
            });

            // Watch for template selection
            this.$watch('settings.sections', (sections) => {
                if (!sections || !Array.isArray(sections)) return;
                const mgmt = sections.find(s => s.id === 'management');
                if (mgmt) {
                    const templateField = mgmt.fields.find(f => f.id === 'settings_template_apply');
                    if (templateField && templateField.value && templateField.value !== "") {
                        const templateName = templateField.value;
                        templateField.value = ""; // Reset immediately to allow re-selecting same template
                        this.applyTemplate(templateName);
                    }
                }
            }, { deep: true });

            // Check for OAuth success redirect OR tab request in URL
            const urlParams = new URLSearchParams(window.location.search);
            const requestedTab = urlParams.get('activeTab');

            this.isMobile = window.innerWidth < 768 || device.getInputType() === 'touch';

            if (requestedTab) {
                // Feature flag guard for manual URL navigation
                if (!this.isTabAllowed(requestedTab)) {
                    console.warn(`Tab '${requestedTab}' is not available.`);
                    this.activeTab = 'agent';
                } else {
                    this.activeTab = requestedTab;
                }
                localStorage.setItem('settingsActiveTab', this.activeTab);
            }

            if (urlParams.get('google_chat_auth') === 'success') {
                showToast("Google Chat connected successfully!", "success");
                // The delay ensures Alpine component is fully stabilized before API call
                setTimeout(() => {
                    this.openModal();
                }, 100);

                // Clear the param from URL without refreshing
                const newUrl = window.location.pathname;
                window.history.replaceState({}, document.title, newUrl);
            }
        },

        async applyTemplate(templateName) {
            if (!templateName) return;
            // Find the template select field in the management section for proper popover positioning
            const mgmtSection = [...document.querySelectorAll('#settingsModal .section-title')]
                .find(el => el.textContent.trim().includes('Configuration Management'));
            const templateSelectEl = mgmtSection?.closest('.section')?.querySelector('.searchable-select-input');

            const confirmed = await Alpine.store('confirmation').confirm(
                `Apply the '${templateName}' template? Current changes will be overwritten.`,
                { target: templateSelectEl || document.activeElement }
            );
            if (!confirmed) return;

            try {
                const resp = await callJsonApi("/settings_apply_template", { template_name: templateName });
                if (resp.ok) {
                    showToast(resp.message, "success");
                    // Close and re-open to refresh all fields
                    this.closeModal();
                    setTimeout(() => this.openModal(), 1000);
                }
            } catch (e) {
                showToast(`Failed to apply template: ${e.message || e}`, "error");
            }
        },

        async openModal() {
            console.log('Settings modal opening');
            this.startPolling();
            try {
                const data = await callJsonApi("/settings_get", null);

                this.settings = {
                    title: "Settings",
                    sections: data.settings.sections,
                    buttons: [
                        { id: 'save', title: 'Save', classes: 'btn btn-ok' },
                        { id: 'cancel', title: 'Cancel', classes: 'btn btn-cancel' }
                    ]
                };
                this.isDevelopment = data.is_development;
                this.isProduction = data.settings?.is_production || false;
                // Sync per-tab feature flags from backend
                this._syncTabFlags(data.settings);

                this.isOpen = true;
                const rootStore = Alpine.store('root');
                if (rootStore) rootStore.isOpen = true;

                // Set initial tab from storage
                let savedTab = localStorage.getItem('settingsActiveTab') || 'agent';

                // Feature flag guard for saved tab
                if (!this.isTabAllowed(savedTab)) {
                    savedTab = 'agent';
                    localStorage.setItem('settingsActiveTab', 'agent');
                }
                this.activeTab = savedTab;

                if (savedTab === 'scheduler') {
                    this.initScheduler();
                } else if (savedTab === 'projects') {
                    this.initProjects();
                } else if (savedTab === 'history') {
                    this.initHistory();
                    this.initCommonPrompts();
                }

                // Initialize searchable-select fields state
                this.settings.sections.forEach(section => {
                    section.fields.forEach(field => {
                        // Force all native selects to use the searchable component for UI unity
                        if (field.type === 'select') {
                            field.type = 'searchable-select';
                        }

                        if (field.type === 'searchable-select') {
                            field.isDropdownOpen = false;
                            field.searchQuery = '';
                        }

                        // Initialize showPassword state for sensitive fields
                        if (field.type === 'password' || this.isSensitiveKey(field)) {
                            field.showPassword = false;
                        }
                    });
                });

                return new Promise(resolve => {
                    this.resolvePromise = resolve;
                });
            } catch (e) {
                if (window.toastFetchError) window.toastFetchError("Error getting settings", e);
                else console.error("Error getting settings:", e);
            }
        },

        initScheduler() {
            console.log('Initializing scheduler tab...');
            const el = document.querySelector('[x-data="schedulerSettings"]');
            if (el) {
                const data = Alpine.$data(el);
                if (data) {
                    if (typeof data.startPolling === 'function') data.startPolling();
                    if (typeof data.fetchTasks === 'function') data.fetchTasks();
                }
            }
        },

        initProjects() {
            console.log('Initializing projects tab...');
            if (Alpine.store('projects')) {
                const store = Alpine.store('projects');
                if (typeof store.startPolling === 'function') store.startPolling();
                if (typeof store.loadProjectsList === 'function') store.loadProjectsList();
            }
        },

        initHistory() {
            console.log('Initializing history tab...');
            const el = document.querySelector('[x-data="historySettings"]');
            if (el) {
                const data = Alpine.$data(el);
                if (data) {
                    if (typeof data.startPolling === 'function') data.startPolling();
                    if (typeof data.fetchChats === 'function') data.fetchChats();
                }
            }
        },

        initCommonPrompts() {
            console.log('Initializing common prompts...');
            const el = document.querySelector('[x-data="commonPromptsSettings"]');
            if (el) {
                const data = Alpine.$data(el);
                if (data) {
                    if (typeof data.startPolling === 'function') data.startPolling();
                    if (typeof data.fetchPrompts === 'function') data.fetchPrompts();
                }
            }
        },

        startPolling() {
            if (this.pollingInterval) return;
            console.log('[settings.js] Starting settings polling...');
            this.pollingInterval = setInterval(async () => {
                if (!this.isOpen) {
                    this.stopPolling();
                    return;
                }

                // Skip refresh if any input in the MODAL is focused
                const activeEl = document.activeElement;
                if (activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA' || activeEl.tagName === 'SELECT')) {
                    const isInModal = activeEl.closest('.modal-container');
                    if (isInModal) {
                        // console.log('[settings.js] Skipping refresh: input focused');
                        return;
                    }
                }

                try {
                    // console.log('[settings.js] Refreshing settings...');
                    const data = await callJsonApi("/settings_get", null);
                    if (data && data.settings && data.settings.sections) {
                        this.isDevelopment = data.is_development;
                        this.isProduction = data.settings?.is_production || false;
                        this._syncTabFlags(data.settings);
                        data.settings.sections.forEach(section => {
                            section.fields.forEach(field => {
                                if (field.type === 'select') {
                                    field.type = 'searchable-select';
                                    field.isDropdownOpen = false;
                                    field.searchQuery = '';
                                }
                            });
                        });
                        this.settings.sections = data.settings.sections;
                    }
                } catch (e) {
                    console.error("Settings polling error:", e);
                }
            }, 10000); // 10 seconds
        },

        stopPolling() {
            if (this.pollingInterval) {
                console.log('[settings.js] Stopping settings polling...');
                clearInterval(this.pollingInterval);
                this.pollingInterval = null;
            }
        },

        // Flag-driven tab allowlist — maps tab name to the settings store flag
        _tabFlagMap: {
            'external': 'external_enabled',
            'mcp': 'mcp_enabled',
            'developer': 'developer_tab_enabled',
            'backup': 'backup_enabled',
            'oauth': 'oauth_enabled',
            'projects': 'projects_enabled',
            'history': 'history_enabled',
            'scheduler': 'scheduler_enabled',
        },

        // Sync per-tab flags from backend settings payload
        _syncTabFlags(settings) {
            if (!settings) return;
            const store = Alpine.store('settings');
            if (store) {
                store.history_enabled = settings.history_enabled !== false;
                store.scheduler_enabled = settings.scheduler_enabled !== false;
                store.oauth_enabled = settings.oauth_enabled !== false;
                store.backup_enabled = settings.backup_enabled !== false;
                store.projects_enabled = settings.projects_enabled !== false;
                store.mcp_enabled = settings.mcp_enabled !== false;
                store.developer_tab_enabled = settings.developer_tab_enabled !== false;
                store.external_enabled = settings.external_enabled !== false;
            }
        },

        // Check if a tab is allowed based on feature flags
        isTabAllowed(tab) {
            this.isMobile = window.innerWidth < 768 || device.getInputType() === 'touch';
            // Developer tab has an extra mobile guard
            if (tab === 'developer' && this.isMobile) return false;
            // Check flag-driven visibility
            const flagKey = this._tabFlagMap[tab];
            if (flagKey) {
                const store = Alpine.store('settings');
                if (store && store[flagKey] === false) return false;
            }
            return true;
        },

        switchTab(tab) {
            if (!this.isTabAllowed(tab)) {
                console.warn(`Tab '${tab}' is not available.`);
                return;
            }
            this.activeTab = tab;
        },

        async handleButton(buttonId) {
            if (buttonId === 'save') {
                // Serialize KVP editors
                document.querySelectorAll('.kvp-editor').forEach(el => {
                    const data = Alpine.$data(el);
                    if (data && typeof data.serializeItems === 'function') data.serializeItems();
                });

                try {
                    const resp = await callJsonApi("/settings_set", this.settings);
                    document.dispatchEvent(new CustomEvent('settings-updated', { detail: resp.settings }));
                    showToast("Settings saved successfully", "success");
                    if (this.resolvePromise) this.resolvePromise({ status: 'saved', data: resp.settings });
                } catch (e) {
                    showToast(`Error saving settings: ${e.message || e}`, "error");
                    return;
                }
            } else if (buttonId === 'cancel') {
                if (this.resolvePromise) this.resolvePromise({ status: 'cancelled', data: null });
            }

            this.closeModal();
        },

        closeModal() {
            this.isOpen = false;
            const rootStore = Alpine.store('root');
            if (rootStore) rootStore.isOpen = false;

            this.stopPolling();

            // Stop scheduler polling
            const schEl = document.querySelector('[x-data="schedulerSettings"]');
            if (schEl) {
                const data = Alpine.$data(schEl);
                if (data && typeof data.stopPolling === 'function') data.stopPolling();
            }

            // Stop projects polling
            if (Alpine.store('projects') && typeof Alpine.store('projects').stopPolling === 'function') {
                Alpine.store('projects').stopPolling();
            }

            // Stop history and prompts polling
            const histEl = document.querySelector('[x-data="historySettings"]');
            if (histEl) {
                const data = Alpine.$data(histEl);
                if (data && typeof data.stopPolling === 'function') data.stopPolling();
            }
            const promptsEl = document.querySelector('[x-data="commonPromptsSettings"]');
            if (promptsEl) {
                const data = Alpine.$data(promptsEl);
                if (data && typeof data.stopPolling === 'function') data.stopPolling();
            }

            // Trigger an immediate poll to refresh the chat list after settings close
            // This ensures the sidebar re-renders with the latest data
            setTimeout(() => {
                if (globalThis.poll && typeof globalThis.poll === 'function') {
                    globalThis.poll();
                }
            }, 300);
        },

        async handleFieldButton(field) {
            console.log(`Button clicked: ${field.id}`);
            if (field.id === "mcp_servers_config") openModal("settings/mcp/client/mcp-servers.html");
            else if (field.id === "backup_create") openModal("settings/backup/backup.html");
            else if (field.id === "backup_restore") openModal("settings/backup/restore.html");
            else if (field.id === "show_a2a_connection") openModal("settings/external/a2a-connection.html");
            else if (field.id === "external_api_examples") openModal("settings/external/api-examples.html");
            else if (field.id === "memory_dashboard") openModal("settings/memory/memory-dashboard.html");
            else if (field.id === "personalization_dashboard") openModal("settings/personalization/personalization-dashboard.html");
            else if (field.id === "personalization_reset") {
                const confirmed = await Alpine.store('confirmation').confirm(
                    "Are you sure you want to reset your personalization profile? All personality data and collected signals will be deleted. The system will start learning from scratch.",
                    { target: document.activeElement }
                );
                if (confirmed) {
                    try {
                        const resp = await callJsonApi("/personalization_profile", { action: "reset" });
                        if (resp.success) {
                            showToast("Personalization profile and signals cleared successfully", "success");
                        } else {
                            showToast(resp.error || "Failed to reset personalization", "error");
                        }
                    } catch (e) {
                        showToast(`Reset failed: ${e.message || e}`, "error");
                    }
                }
            }
            else if (field.id === "settings_export_bundle") {
                try {
                    const bundle = await callJsonApi("/settings_export", {});
                    const blob = new Blob([JSON.stringify(bundle, null, 4)], { type: 'application/json' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `agix-settings-${new Date().toISOString().split('T')[0]}.json`;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                    showToast("Configuration bundle exported successfully", "success");
                } catch (e) {
                    showToast(`Export failed: ${e.message || e}`, "error");
                }
            }
            else if (field.id === "settings_import_bundle") {
                const input = document.createElement('input');
                input.type = 'file';
                input.accept = '.json';
                input.onchange = async (e) => {
                    const file = e.target.files[0];
                    if (!file) return;
                    const reader = new FileReader();
                    reader.onload = async (event) => {
                        try {
                            const bundle = JSON.parse(event.target.result);
                            const confirmed = await Alpine.store('confirmation').confirm(
                                "This will overwrite your current settings, secrets, and parameters. Proceed?",
                                { target: document.activeElement }
                            );
                            if (confirmed) {
                                const resp = await callJsonApi("/settings_import", bundle);
                                if (resp.ok) {
                                    showToast("Settings imported successfully. Refreshing UI...", "success");
                                    // Close and re-open modal to refresh state
                                    this.closeModal();
                                    setTimeout(() => this.openModal(), 1000);
                                }
                            }
                        } catch (err) {
                            showToast(`Import failed: ${err.message || err}`, "error");
                        }
                    };
                    reader.readAsText(file);
                };
                input.click();
            }
            else if (field.id === "system_update") {
                const confirmed = await Alpine.store('confirmation').confirm(
                    "Update system? Uncommitted changes will be lost!",
                    { target: document.activeElement }
                );
                if (confirmed) {
                    showToast("Update initiated...", "info");
                    try {
                        const res = await callJsonApi("/system_update", {});
                        showToast(res.message || "Update successful. Restarting...", res.status === "success" ? "success" : "error");
                    } catch (e) {
                        showToast(`Error: ${e.message || e}`, "error");
                    }
                }
            }
            else if (field.action === "google_chat_auth_initiate") {
                try {
                    let resp = await callJsonApi("/google_chat_auth", { action: "initiate" });

                    if (resp.status === "setup_required") {
                        const tray = document.getElementById('gc-manual-tray');
                        if (tray) {
                            tray.open = true;
                            // Ensure it's scrolled into view
                            this.$nextTick(() => {
                                tray.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            });
                            showToast("Please provide your Google Cloud credentials in the manual configuration section below.", "info");
                        } else {
                            // Fallback to legacy modal if tray UI not found in DOM
                            if (window.openModal) openModal("settings/oauth/google-chat.html");
                            else console.error("openModal function not found and no tray UI present");
                        }
                        return;
                    }

                    if (resp.auth_url) {
                        window.location.href = resp.auth_url;
                    } else if (resp.message) {
                        showToast(resp.message, "error");
                    } else {
                        showToast("Failed to initiate OAuth: No auth URL returned.", "error");
                    }
                } catch (e) {
                    showToast(`OAuth Error: ${e.message || e}`, "error");
                }
            }
            else if (field.action === "google_chat_auth_disconnect") {
                const confirmed = await Alpine.store('confirmation').confirm(
                    "Are you sure you want to disconnect Google Chat? This will delete all stored tokens and credentials.",
                    { target: document.activeElement }
                );
                if (confirmed) {
                    try {
                        let resp = await callJsonApi("/google_chat_auth", { action: "disconnect" });
                        if (resp.status === "success") {
                            showToast(resp.message, "success");
                            // Refresh settings
                            this.settings = { sections: [] }; // Clear briefly to force refresh
                            setTimeout(async () => {
                                const data = await callJsonApi("/settings_get", null);
                                this.settings = {
                                    title: "Settings",
                                    sections: data.settings.sections,
                                    buttons: [
                                        { id: 'save', title: 'Save', classes: 'btn btn-ok' },
                                        { id: 'cancel', title: 'Cancel', classes: 'btn btn-cancel' }
                                    ]
                                };
                            }, 100);
                        } else {
                            showToast(resp.message || "Disconnect failed", "error");
                        }
                    } catch (e) {
                        showToast(`Disconnect Error: ${e.message || e}`, "error");
                    }
                }
            }
        }
    }));

    Alpine.data('historySettings', historySettings);
    // Initialize Common Prompts component
    Alpine.data('commonPromptsSettings', commonPromptsSettings);

    // Register Personalization Dashboard store
    // Use the imported object directly as it performs its own registerStore call usually,
    // but we ensure it's in the Alpine global scope.
    if (!Alpine.store('personalizationDashboard')) {
        Alpine.store('personalizationDashboard', personalizationDashboardStore);
    }
}

// Export for explicit registration from initFw.js
export { registerSettings };

// Also attempt self-registration as fallback
console.log('[settings.js] Module loaded, checking Alpine state...');

if (window.Alpine) {
    console.log('[settings.js] Alpine already available, registering...');
    registerSettings();
} else {
    console.log('[settings.js] Alpine not yet available, waiting for alpine:init...');
    document.addEventListener('alpine:init', registerSettings);
}

setTimeout(() => {
    if (window.Alpine) {
        registerSettings();
    }
}, 50);



// Manual Google Chat Configuration Submission
window.submitManualGoogleChat = async function () {
    const textarea = document.getElementById('gc_manual_json');
    if (!textarea) return;

    const configJson = textarea.value.trim();
    if (!configJson) {
        showToast("Please paste your credentials JSON first.", "warning");
        return;
    }

    try {
        JSON.parse(configJson); // Basic validation
    } catch (e) {
        showToast("Invalid JSON format. Please check your credentials file content.", "error");
        return;
    }

    try {
        const resp = await callJsonApi("/google_chat_auth", {
            action: "initiate",
            config_json: configJson
        });

        if (resp.auth_url) {
            // Success, starting OAuth flow
            window.location.href = resp.auth_url;
        } else if (resp.message) {
            showToast(resp.message, "error");
        }
    } catch (e) {
        showToast("Failed to save credentials: " + e.message || e, "error");
    }
};

// Backward compatibility for index.html which might use settingsModalProxy or global functions
window.settingsModalProxy = {
    get settings() {
        const el = document.getElementById('settingsModal');
        if (el) {
            const data = Alpine.$data(el);
            return data ? data.settings : { sections: [] };
        }
        return { sections: [] };
    },
    openModal() {
        return window.openSettings();
    }
};

window.openSettings = function () {
    const el = document.getElementById('settingsModal');
    if (el) {
        const data = Alpine.$data(el);
        if (data && typeof data.openModal === 'function') {
            return data.openModal().then(res => console.log('Settings closed:', res));
        }
    }
    return Promise.resolve();
};

window.showToast = showToast;

// Show toast notification - now uses new notification system
function showToast(message, type = 'info') {
    // Use new frontend notification system based on type
    if (window.Alpine && window.Alpine.store && window.Alpine.store('notificationStore')) {
        const store = window.Alpine.store('notificationStore');
        switch (type.toLowerCase()) {
            case 'error':
                return store.frontendError(message, "Settings", 5);
            case 'success':
                return store.frontendInfo(message, "Settings", 3);
            case 'warning':
                return store.frontendWarning(message, "Settings", 4);
            case 'info':
            default:
                return store.frontendInfo(message, "Settings", 3);
        }
    } else {
        // Fallback if Alpine/store not ready
        console.log(`SETTINGS ${type.toUpperCase()}: ${message}`);
        return null;
    }
}
