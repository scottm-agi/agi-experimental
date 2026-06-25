import { createStore } from "../../../js/AlpineStore.js";

// Sidebar Bottom store manages version info display
const model = {
  versionNo: "",
  commitTime: "",
  commitHash: "",

  get versionLabel() {
    return this.versionNo && this.commitTime
      ? `Version ${this.versionNo} ${this.commitTime}${this.commitHash ? ` (${this.commitHash.substring(0, 7)})` : ""}`
      : "";
  },

  init() {
    // Load version info from global scope (exposed in index.html)
    const gi = globalThis.gitinfo;
    if (gi && gi.version && gi.commit_time) {
      this.versionNo = gi.version;
      this.commitTime = gi.commit_time;
      this.commitHash = gi.commit_hash || "";
    }
  },
};

export const store = createStore("sidebarBottom", model);

