import { createStore } from "../js/AlpineStore.js";

// Tooltips store manages global tooltip state and hover listeners
const model = {
    show: false,
    text: "",
    x: 0,
    y: 0,
    timer: null,

    init() {
        // Global hover listener
        document.addEventListener("mouseover", (e) => {
            const target = e.target.closest("[data-tooltip]");
            if (target && this.isEnabled()) {
                const text = target.getAttribute("data-tooltip");
                if (!text) return;

                clearTimeout(this.timer);
                this.timer = setTimeout(() => {
                    const rect = target.getBoundingClientRect();
                    this.text = text;
                    this.x = rect.left + rect.width / 2;
                    this.y = rect.top - 10;
                    this.show = true;
                }, 300);
            }
        });

        document.addEventListener("mouseout", (e) => {
            if (e.target.closest("[data-tooltip]")) {
                clearTimeout(this.timer);
                this.show = false;
            }
        });

        // Close on click
        document.addEventListener("mousedown", () => {
            clearTimeout(this.timer);
            this.show = false;
        });
    },

    isEnabled() {
        // Safe access to settings store
        const settings = globalThis.Alpine?.store("settings")?.settings;
        return settings ? settings.ui_tooltips_enabled !== false : true;
    }
};

export const store = createStore("tooltips", model);
