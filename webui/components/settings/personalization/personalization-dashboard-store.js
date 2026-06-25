import { createStore } from "../../../js/AlpineStore.js";
import * as API from "../../../js/api.js";
import { openModal, closeModal } from "../../../js/modals.js";
import { store as notificationStore } from "../../notifications/notification-store.js";

function justToast(text, type = "info", timeout = 5000) {
    notificationStore.addFrontendToastOnly(type, text, "", timeout / 1000);
}

const personalizationDashboardStore = {
    // Profile data
    profile: null,
    signals: [],
    signalCount: 0,
    hasProfile: false,
    loading: false,
    error: null,

    async openModal() {
        await openModal("settings/personalization/personalization-dashboard.html");
    },

    async onOpen() {
        await this.loadProfile();
    },

    async loadProfile() {
        this.loading = true;
        this.error = null;

        // Timeout failsafe — prevent infinite loading animation (Issue #763)
        const timeoutId = setTimeout(() => {
            if (this.loading) {
                console.warn("[PERSONALIZATION_STORE] loadProfile timed out after 15s");
                this.loading = false;
                this.error = "Profile load timed out. Click Refresh to retry.";
            }
        }, 15000);

        try {
            const resp = await API.callJsonApi("personalization_profile", { action: "get" });
            console.log("[PERSONALIZATION_STORE] Profile load response:", resp);
            if (resp.success) {
                this.profile = resp.profile;
                this.signalCount = resp.signal_count || 0;
                this.hasProfile = resp.has_profile;
            } else {
                this.error = resp.error || "Failed to load profile";
            }
        } catch (e) {
            this.error = e.message || "Failed to load profile";
        } finally {
            clearTimeout(timeoutId);
            this.loading = false;
        }
    },

    async loadSignals() {
        try {
            const resp = await API.callJsonApi("personalization_profile", { action: "signals" });
            if (resp.success) {
                this.signals = resp.signals || [];
                this.signalCount = resp.total_count || 0;
            }
        } catch (e) {
            console.error("Failed to load signals:", e);
        }
    },

    async resetProfile() {
        const confirmed = await Alpine.store("confirmation").confirm(
            "Are you sure you want to reset your personalization profile? All personality data and collected signals will be deleted.",
            { target: document.activeElement }
        );
        if (!confirmed) return;

        try {
            this.loading = true;
            const resp = await API.callJsonApi("personalization_profile", { action: "reset" });
            if (resp.success) {
                justToast("Personalization profile reset successfully", "success");
                this.profile = null;
                this.signals = [];
                this.signalCount = 0;
                this.hasProfile = false;
            } else {
                justToast(resp.error || "Failed to reset profile", "error");
            }
        } catch (e) {
            justToast(e.message || "Failed to reset profile", "error");
        } finally {
            this.loading = false;
        }
    },

    // Computed helpers
    get confidencePercent() {
        if (!this.profile) return 0;
        const conf = typeof this.profile.confidence === 'number' ? this.profile.confidence :
            (typeof this.profile.confidence_score === 'number' ? this.profile.confidence_score : 0);
        return Math.round(conf * 100);
    },

    get confidenceColor() {
        const pct = this.confidencePercent;
        if (pct >= 80) return "#10b981";  // green
        if (pct >= 50) return "#f59e0b";  // amber
        return "#ef4444";                  // red
    },

    get analysisCount() {
        return this.profile?.analysis_count || 0;
    },

    get lastUpdated() {
        if (!this.profile?.last_updated) return "Never";
        const d = new Date(this.profile.last_updated);
        return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) +
            " " + d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
    },

    get tenets() {
        if (!this.profile || !this.profile.tenets) return [];

        let items = [];
        const rawTenets = this.profile.tenets;

        if (Array.isArray(rawTenets)) {
            items = rawTenets.map((t, i) => {
                if (typeof t === 'object' && t !== null) {
                    return {
                        name: t.name || `Trait ${i + 1}`,
                        score: typeof t.score === 'number' ? t.score : 0.5,
                        description: t.description || ""
                    };
                }
                return { name: `Trait ${i + 1}`, score: 0.5, description: String(t) };
            });
        } else if (typeof rawTenets === 'object') {
            items = Object.entries(rawTenets).map(([key, data]) => {
                const name = key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
                let score = 0.5;
                let description = "";

                if (typeof data === 'object' && data !== null) {
                    score = typeof data.score === 'number' ? data.score : 0.5;
                    description = data.description || "";
                } else {
                    description = String(data);
                }

                return { name, score, description };
            });
        }

        return items.map(item => ({
            ...item,
            scorePercent: Math.round(item.score * 100),
            description: (item.description || "").replace(/^\d+\.\s*/, "")
        }));
    },

    get commStyle() {
        if (!this.profile?.communication_style) return [];
        return Object.entries(this.profile.communication_style).map(([key, val]) => ({
            label: key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()),
            value: val,
        }));
    },
};

export const store = createStore("personalizationDashboard", personalizationDashboardStore);
