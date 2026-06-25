/**
 * Mode Selector Store
 * 
 * Manages agent mode state for the MultiAgentDev system.
 * Provides mode switching, listing, and auto-suggestion functionality.
 */

import { createStore } from "../../../js/AlpineStore.js";
import { fetchApi } from "../../../js/api.js";

const model = {
    // Current mode state
    currentMode: "code",
    modes: [],
    isLoading: false,
    isOpen: false,  // Dropdown open state

    // Mode icons mapping
    modeIcons: {
        code: "💻",
        architect: "🏗️",
        ask: "❓",
        debug: "🔍",
        review: "📝"
    },

    // Mode colors for visual distinction
    modeColors: {
        code: "#4CAF50",      // Green
        architect: "#2196F3", // Blue
        ask: "#9C27B0",       // Purple
        debug: "#FF9800",     // Orange
        review: "#607D8B"     // Blue-grey
    },

    async init() {
        console.log("Mode store initialized");
        await this.fetchModes();
    },

    /**
     * Fetch available modes from the backend
     */
    async fetchModes() {
        this.isLoading = true;
        try {
            const response = await fetchApi("/api/mode/list", {
                method: "POST",
                headers: { "Content-Type": "application/json" }
            });

            if (response.ok) {
                const data = await response.json();
                if (data.ok) {
                    this.modes = data.modes || [];
                    this.currentMode = data.current_mode || "code";
                    console.log(`Loaded ${this.modes.length} modes, current: ${this.currentMode}`);
                }
            }
        } catch (error) {
            console.error("Error fetching modes:", error);
            // Set default modes if fetch fails
            this.modes = [
                { slug: "code", name: "💻 Code", display_name: "Code Mode", description: "Full-featured development mode" },
                { slug: "architect", name: "🏗️ Architect", display_name: "Architect Mode", description: "Design and planning mode" },
                { slug: "ask", name: "❓ Ask", display_name: "Ask Mode", description: "Question-answering mode" },
                { slug: "debug", name: "🔍 Debug", display_name: "Debug Mode", description: "Troubleshooting mode" },
                { slug: "review", name: "📝 Review", display_name: "Review Mode", description: "Code review mode" }
            ];
        } finally {
            this.isLoading = false;
        }
    },

    /**
     * Switch to a different mode
     * @param {string} modeSlug - The mode to switch to
     * @param {boolean} force - Force switch even if transition not allowed
     */
    async switchMode(modeSlug, force = false) {
        if (modeSlug === this.currentMode) {
            this.isOpen = false;
            return true;
        }

        this.isLoading = true;
        try {
            const response = await fetchApi("/api/mode/switch", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ mode: modeSlug, force })
            });

            const data = await response.json();

            if (data.ok) {
                const previousMode = this.currentMode;
                this.currentMode = data.current_mode;
                this.isOpen = false;

                // Show success notification
                if (globalThis.toast) {
                    globalThis.toast(
                        `Switched from ${previousMode} to ${this.currentMode} mode`,
                        "success",
                        3000
                    );
                }

                console.log(`Mode switched: ${previousMode} → ${this.currentMode}`);
                return true;
            } else {
                // Show error notification
                if (globalThis.toast) {
                    globalThis.toast(
                        data.error || "Failed to switch mode",
                        "error",
                        5000
                    );
                }
                return false;
            }
        } catch (error) {
            console.error("Error switching mode:", error);
            if (globalThis.toastFetchError) {
                globalThis.toastFetchError("Error switching mode", error);
            }
            return false;
        } finally {
            this.isLoading = false;
        }
    },

    /**
     * Get suggested mode based on task text
     * @param {string} taskText - The task description
     */
    async suggestMode(taskText) {
        if (!taskText || taskText.trim().length < 3) {
            return null;
        }

        try {
            const response = await fetchApi("/api/mode/suggest", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ task: taskText })
            });

            const data = await response.json();

            if (data.ok && data.suggested_mode) {
                return data.suggested_mode;
            }
            return null;
        } catch (error) {
            console.error("Error getting mode suggestion:", error);
            return null;
        }
    },

    /**
     * Toggle dropdown open state
     */
    toggleDropdown() {
        this.isOpen = !this.isOpen;
    },

    /**
     * Close dropdown
     */
    closeDropdown() {
        this.isOpen = false;
    },

    /**
     * Get icon for a mode
     * @param {string} modeSlug - The mode slug
     */
    getIcon(modeSlug) {
        return this.modeIcons[modeSlug] || "⚙️";
    },

    /**
     * Get color for a mode
     * @param {string} modeSlug - The mode slug
     */
    getColor(modeSlug) {
        return this.modeColors[modeSlug] || "#666";
    },

    /**
     * Get current mode display name
     */
    get currentModeDisplay() {
        const mode = this.modes.find(m => m.slug === this.currentMode);
        return mode ? mode.name : this.currentMode;
    },

    /**
     * Get current mode description
     */
    get currentModeDescription() {
        const mode = this.modes.find(m => m.slug === this.currentMode);
        return mode ? mode.description : "";
    }
};

const store = createStore("modeSelector", model);

export { store };
