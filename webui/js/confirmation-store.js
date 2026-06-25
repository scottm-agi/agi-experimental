import { createStore } from "../js/AlpineStore.js";

/**
 * Confirmation store manages global confirmation state for custom popover dialogs.
 * Includes a click-away guard to prevent race conditions where the triggering click
 * also fires @click.away on the popover, dismissing it instantly.
 */
const model = {
    isOpen: false,
    clickAwayReady: false,
    title: "Confirm Action",
    message: "Are you sure you want to proceed?",
    okText: "Confirm",
    cancelText: "Cancel",
    x: 0,
    y: 0,
    resolve: null,

    /**
     * Show a confirmation popover near the triggering element.
     * @param {string} message - The message to display.
     * @param {Object} options - Additional options.
     * @param {string} [options.title] - Optional title.
     * @param {string} [options.okText] - Text for the confirm button.
     * @param {string} [options.cancelText] - Text for the cancel button.
     * @param {HTMLElement} [options.target] - The element that triggered the confirmation (for positioning).
     * @returns {Promise<boolean>}
     */
    confirm(message, options = {}) {
        return new Promise((resolve) => {
            this.message = message;
            this.title = options.title || "Confirm Action";
            this.okText = options.okText || "Confirm";
            this.cancelText = options.cancelText || "Cancel";

            // Positioning: Center over the target with offset
            if (options.target) {
                const rect = options.target.getBoundingClientRect();
                this.x = rect.left + (rect.width / 2);
                this.y = rect.top - 2; // Offset slightly above the target
            } else {
                // Fallback to center of screen
                this.x = window.innerWidth / 2;
                this.y = window.innerHeight / 2;
            }

            // Ensure the popover Y position is at least 80px from top (not off-screen)
            if (this.y < 80) {
                this.y = 80;
            }

            this.resolve = resolve;
            this.clickAwayReady = false;
            this.isOpen = true;

            // Guard: delay click-away activation to prevent the originating click
            // from being interpreted as a click-away event
            setTimeout(() => {
                this.clickAwayReady = true;
            }, 150);
        });
    },

    handleConfirm() {
        this.isOpen = false;
        this.clickAwayReady = false;
        if (this.resolve) this.resolve(true);
        this.resolve = null;
    },

    handleCancel() {
        // Only allow cancel via click-away after the guard period
        if (!this.isOpen) return;
        this.isOpen = false;
        this.clickAwayReady = false;
        if (this.resolve) this.resolve(false);
        this.resolve = null;
    },

    /**
     * Guarded click-away handler. Only cancels if clickAwayReady is true.
     * Prevents race condition where the button click that opens the popover
     * also triggers @click.away.
     */
    handleClickAway() {
        if (this.clickAwayReady) {
            this.handleCancel();
        }
    }
};

export const store = createStore("confirmation", model);
