import { createStore } from "../../../js/AlpineStore.js";
import { toggleCssProperty } from "../../../js/css.js";

const model = {
  settings: {},

  async init() {
    this.settings = this._migrateSettings(
      JSON.parse(localStorage.getItem("messageResizeSettings") || "null")
    ) || this._getDefaultSettings();
    this._applyAllSettings();
  },

  /**
   * Migrate old 3-state settings ({ minimized, maximized }) to new 2-state ({ collapsed }).
   * Old mapping: minimized=true → collapsed=true, else collapsed=false.
   */
  _migrateSettings(oldSettings) {
    if (!oldSettings) return null;
    // Already migrated if first value has 'collapsed' key
    const firstKey = Object.keys(oldSettings)[0];
    if (firstKey && oldSettings[firstKey].collapsed !== undefined) return oldSettings;

    const migrated = {};
    for (const [key, val] of Object.entries(oldSettings)) {
      migrated[key] = { collapsed: val.minimized ?? false };
    }
    localStorage.setItem("messageResizeSettings", JSON.stringify(migrated));
    return migrated;
  },

  _getDefaultSettings() {
    return {
      message: { collapsed: false },
      "message-agent": { collapsed: true },
      "message-agent-response": { collapsed: false },
      "message-tool": { collapsed: true },
      "message-code-exe": { collapsed: true },
      "message-browser": { collapsed: true },
      "message-util": { collapsed: true },
      "message-info": { collapsed: true },
      "message-warning": { collapsed: true },
      "message-agent-delegation": { collapsed: true },
      "message-default": { collapsed: true },
    };
  },

  getSetting(className) {
    return this.settings[className] || { collapsed: false };
  },

  _setSetting(className, setting) {
    this.settings = { ...this.settings, [className]: setting };
    localStorage.setItem(
      "messageResizeSettings",
      JSON.stringify(this.settings)
    );
  },

  _applyAllSettings() {
    for (const [className, setting] of Object.entries(this.settings)) {
      this._applySetting(className, setting);
    }
  },

  /**
   * Toggle between collapsed and expanded for a message class.
   */
  async toggleMessageClass(className, event) {
    const set = this.getSetting(className);
    set.collapsed = !set.collapsed;
    this._setSetting(className, set);
    this._applySetting(className, set);
    this._applyScroll(event);
  },

  // Keep legacy methods for backward compatibility with any external callers
  async minimizeMessageClass(className, event) {
    return this.toggleMessageClass(className, event);
  },

  async maximizeMessageClass(className, event) {
    return this.toggleMessageClass(className, event);
  },

  _applyScroll(event) {
    if (!event || !event.target) {
      return;
    }

    // Store the element reference to avoid issues with event being modified
    const targetElement = event.target;
    const clickY = event.clientY;

    try {
      // Get fresh measurements after potential re-renders
      const rect = targetElement.getBoundingClientRect();
      const viewHeight = window.innerHeight || document.documentElement.clientHeight;

      // Get chat history element
      const chatHistory = document.getElementById('chat-history');
      if (!chatHistory) {
        return;
      }

      // Get chat history position
      const chatRect = chatHistory.getBoundingClientRect();

      // Calculate element's middle position relative to chat history
      const elementHeight = rect.height;
      const elementMiddle = rect.top + (elementHeight / 2);
      const relativeMiddle = elementMiddle - chatRect.top;

      // Calculate target scroll position
      let scrollTop;

      if (typeof clickY === 'number') {
        // Calculate based on click position
        const clickRelativeToChat = clickY - chatRect.top;
        // Add current scroll position and adjust to keep element middle at click position
        scrollTop = chatHistory.scrollTop + relativeMiddle - clickRelativeToChat;
      } else {
        // Position element middle at 50% from the top of chat history viewport (center)
        const targetPosition = chatHistory.clientHeight * 0.5;
        scrollTop = chatHistory.scrollTop + relativeMiddle - targetPosition;
      }

      // Apply scroll with instant behavior
      chatHistory.scrollTo({
        top: scrollTop,
        behavior: "auto"
      });
    } catch (e) {
      // Silent error handling
    }
  },

  _applySetting(className, setting) {
    if (setting.collapsed) {
      // Collapsed: hide body and model info
      toggleCssProperty(`.${className} .message-body`, "display", "none");
      toggleCssProperty(`.${className} .msg-model-info`, "display", "none");
    } else {
      // Expanded: show everything, no height limit
      toggleCssProperty(`.${className} .message-body`, "display", "block");
      toggleCssProperty(`.${className} .message-body`, "max-height", "unset");
      toggleCssProperty(`.${className} .message-body`, "overflow-y", "hidden");
      toggleCssProperty(`.${className} .msg-model-info`, "display", "block");
    }
  },
};

const store = createStore("messageResize", model);
globalThis.messageResizeStore = store;

export { store };
