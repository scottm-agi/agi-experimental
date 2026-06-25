import { createStore } from "../../../js/AlpineStore.js";

export const store = createStore("scrollButtons", {
    isVisible: false,
    showTop: false,
    showBottom: false,
    scroller: null,
    scrollThreshold: 100,

    _updateTimeout: null,
    init() {
        console.log("ScrollButtonsStore initializing...");
        // Wait for next tick to ensure DOM is ready
        setTimeout(() => {
            this.scroller = document.getElementById('chat-history');
            if (this.scroller) {
                console.log("Scroll container found:", this.scroller.id);
                this.scroller.addEventListener('scroll', () => this.updateVisibility());

                // Also listen for content changes (MutationObserver) to update visibility
                const observer = new MutationObserver(() => this.updateVisibility());
                observer.observe(this.scroller, { childList: true, subtree: true });

                // Initial check
                this.updateVisibility();
            } else {
                console.warn("Scroll container #chat-history not found!");
            }
        }, 500);
    },

    updateVisibility() {
        if (!this.scroller || this._updateTimeout) return;

        this._updateTimeout = setTimeout(() => {
            const { scrollTop, scrollHeight, clientHeight } = this.scroller;
            const canScroll = scrollHeight > clientHeight + 5;

            this.isVisible = canScroll;
            this.showTop = scrollTop > 50; // Show sooner (50px instead of 100px)
            this.showBottom = (scrollTop + clientHeight) < (scrollHeight - 20); // Show longer (20px from bottom instead of 100px)
            this._updateTimeout = null;
        }, 150); // Debounce visibility updates to 150ms
    },

    scrollToTop() {
        if (this.scroller) {
            // Smooth scroll first
            this.scroller.scrollTo({ top: 0, behavior: 'smooth' });

            // Backup instant scroll to handle edge cases or dynamic content
            setTimeout(() => {
                if (this.scroller.scrollTop > 0) {
                    this.scroller.scrollTop = 0;
                }
            }, 800); // After most smooth scrolls should be done
        }
    },

    scrollToBottom() {
        if (this.scroller) {
            // Use a very large number to truly hit the end regardless of sub-pixels or padding
            this.scroller.scrollTo({ top: this.scroller.scrollHeight + 1000, behavior: 'smooth' });

            // Backup instant scroll
            setTimeout(() => {
                const atBottom = Math.ceil(this.scroller.scrollTop + this.scroller.clientHeight) >= this.scroller.scrollHeight - 5;
                if (!atBottom) {
                    this.scroller.scrollTop = this.scroller.scrollHeight;
                }
            }, 800);
        }
    }
});
