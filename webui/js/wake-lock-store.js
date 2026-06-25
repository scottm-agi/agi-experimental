/**
 * Wake Lock Store — Issue #724
 * Keeps the screen on while the agent is working on mobile devices.
 * Uses the Screen Wake Lock API (navigator.wakeLock).
 * Releases the lock 60 seconds after the agent finishes (paused = true).
 */
import { createStore } from "./AlpineStore.js";

const IDLE_TIMEOUT_MS = 60 * 1000; // 1 minute after agent reply

const model = {
    // State
    _wakeLock: null,
    _releaseTimeout: null,
    _supported: false,
    enabled: false, // User preference — persisted in localStorage
    active: false,  // Whether lock is currently held

    init() {
        this._supported = "wakeLock" in navigator;
        // Restore user preference
        this.enabled = localStorage.getItem("wakeLock") === "true";

        // Re-acquire on visibility change (required by spec — lock is auto-released
        // when page becomes hidden and must be re-requested on visible)
        document.addEventListener("visibilitychange", () => {
            if (document.visibilityState === "visible" && this.active && this._wakeLock === null) {
                this._acquire();
            }
        });
    },

    // Toggle setting (called from UI)
    toggle() {
        this.enabled = !this.enabled;
        localStorage.setItem("wakeLock", this.enabled ? "true" : "false");
        if (!this.enabled) {
            this._release();
        }
    },

    /**
     * Called from the poll loop with the agent's paused state.
     * @param {boolean} paused - true = agent idle, false = agent working
     */
    onAgentStateChange(paused) {
        if (!this._supported || !this.enabled) return;

        if (!paused) {
            // Agent is working — acquire lock and cancel any pending release
            clearTimeout(this._releaseTimeout);
            this._releaseTimeout = null;
            if (!this._wakeLock) {
                this._acquire();
            }
        } else {
            // Agent finished — schedule release after idle timeout
            if (this._wakeLock && !this._releaseTimeout) {
                this._releaseTimeout = setTimeout(() => {
                    this._release();
                    this._releaseTimeout = null;
                }, IDLE_TIMEOUT_MS);
            }
        }
    },

    async _acquire() {
        if (!this._supported || this._wakeLock) return;
        try {
            this._wakeLock = await navigator.wakeLock.request("screen");
            this.active = true;
            this._wakeLock.addEventListener("release", () => {
                this._wakeLock = null;
                this.active = false;
            });
            console.log("[WakeLock] Screen wake lock acquired");
        } catch (err) {
            console.warn("[WakeLock] Failed to acquire:", err.message);
            this._wakeLock = null;
            this.active = false;
        }
    },

    _release() {
        clearTimeout(this._releaseTimeout);
        this._releaseTimeout = null;
        if (this._wakeLock) {
            this._wakeLock.release();
            this._wakeLock = null;
            this.active = false;
            console.log("[WakeLock] Screen wake lock released");
        }
    },

    get isSupported() {
        return this._supported;
    },
};

const store = createStore("wakeLock", model);
export { store };
