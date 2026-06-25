import { createStore } from "../../../js/AlpineStore.js";

export const store = createStore("settingsScrollButtons", {
    isVisible: false,
    showTop: false,
    showBottom: false,
    scroller: null,
    scrollThreshold: 100,

    _updateTimeout: null,
    init() {
        console.log("SettingsScrollButtonsStore initializing...");
        // Wait for next tick to ensure DOM is ready and modal is potentially open
        // We'll also watch for the modal opening to re-attach or re-check
        this.attachScroller();
    },

    attachScroller() {
        if (this.scroller) return;

        // Try to find the scroller periodically if not found immediately
        const findScroller = setInterval(() => {
            this.scroller = document.querySelector('.modal-content');
            if (this.scroller) {
                console.log("Settings scroll container found:", this.scroller);
                this.scroller.addEventListener('scroll', () => this.updateVisibility());

                // Also listen for content changes (MutationObserver) to update visibility
                const observer = new MutationObserver(() => this.updateVisibility());
                observer.observe(this.scroller, { childList: true, subtree: true });

                // Initial check
                this.updateVisibility();
                clearInterval(findScroller);
            }
        }, 500);

        // Stop trying after 10 seconds
        setTimeout(() => clearInterval(findScroller), 10000);
    },

    updateVisibility() {
        if (!this.scroller || this._updateTimeout) return;

        this._updateTimeout = setTimeout(() => {
            const { scrollTop, scrollHeight, clientHeight } = this.scroller;
            const canScroll = scrollHeight > clientHeight + 5;

            this.isVisible = canScroll;
            this.showTop = scrollTop > 50;
            this.showBottom = (scrollTop + clientHeight) < (scrollHeight - 20);
            this._updateTimeout = null;
        }, 150);
    },

    scrollToTop() {
        if (this.scroller) {
            this.scroller.scrollTo({ top: 0, behavior: 'smooth' });
        }
    },

    scrollToBottom() {
        if (this.scroller) {
            this.scroller.scrollTo({ top: this.scroller.scrollHeight + 1000, behavior: 'smooth' });
        }
    }
});
