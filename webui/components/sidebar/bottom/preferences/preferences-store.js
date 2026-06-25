import { createStore } from "../../../../js/AlpineStore.js";
import * as css from "../../../../js/css.js";
import { store as speechStore } from "../../../chat/speech/speech-store.js";
import { callJsonApi } from "../../../../js/api.js";
import * as device from "../../../../js/device.js";

// Preferences store centralizes user preference toggles and side-effects
const model = {
  // UI toggles (initialized with safe defaults, enriched from localStorage in init)
  get autoScroll() { return this._autoScroll; },
  set autoScroll(value) {
    this._autoScroll = value;
    this._applyAutoScroll(value);
  },
  _autoScroll: localStorage.getItem("autoScroll") !== "false",

  get darkMode() { return this._darkMode; },
  set darkMode(value) {
    this._darkMode = value;
    this._applyDarkMode(value);
  },
  _darkMode: localStorage.getItem("darkMode") === "true",

  get speech() { return this._speech; },
  set speech(value) {
    this._speech = value;
    this._applySpeech(value);
  },
  _speech: localStorage.getItem("speech") === "true",

  get showThoughts() { return this._showThoughts; },
  set showThoughts(value) {
    this._showThoughts = value;
    this._applyShowThoughts(value);
  },
  _showThoughts: localStorage.getItem("showThoughts") !== "false",

  get showJson() { return this._showJson; },
  set showJson(value) {
    this._showJson = value;
    this._applyShowJson(value);
  },
  _showJson: localStorage.getItem("showJson") === "true",

  get showUtils() { return this._showUtils; },
  set showUtils(value) {
    this._showUtils = value;
    this._applyShowUtils(value);
  },
  _showUtils: localStorage.getItem("showUtils") === "true",

  get expandTiles() { return this._expandTiles; },
  set expandTiles(value) {
    this._expandTiles = value;
    this._applyExpandTiles(value);
  },
  _expandTiles: localStorage.getItem("expandTiles") !== "false",

  get showBackgroundUpdates() { return this._showBackgroundUpdates; },
  set showBackgroundUpdates(value) {
    this._showBackgroundUpdates = value;
    this._applyShowBackgroundUpdates(value);
  },
  _showBackgroundUpdates: localStorage.getItem("showBackgroundUpdates") === "true",

  get showDebugInfo() { return this._showDebugInfo; },
  set showDebugInfo(value) {
    this._showDebugInfo = value;
    this._applyShowDebugInfo(value);
  },
  _showDebugInfo: localStorage.getItem("showDebugInfo") === "true",

  get simpleChat() { return this._simpleChat; },
  set simpleChat(value) {
    if (this.isMobile()) {
      console.log("[PREFERENCES] Simple chat forced ON for mobile");
      value = true;
    }
    this._simpleChat = value;
    this._applySimpleChat(value);
  },
  _simpleChat: localStorage.getItem("simpleChat") === "true",

  get promptEnhancement() { return this._promptEnhancement; },
  set promptEnhancement(value) {
    this._promptEnhancement = value;
    this._applyPromptEnhancement(value);
  },
  _promptEnhancement: localStorage.getItem("promptEnhancement") !== "false",

  get personalizedReply() { return this._personalizedReply; },
  set personalizedReply(value) {
    this._personalizedReply = value;
    this._applyPersonalizedReply(value);
  },
  _personalizedReply: localStorage.getItem("personalizedReply") !== "false",

  // Initialize and apply current state
  async init() {
    try {
      console.log("[PREFERENCES] Initializing store...");

      const settingsStore = globalThis.Alpine?.store("settings");

      // Production/Shared environment logic:
      // Autoscroll, Enhanced Prompt, and Simple Chat are always ON if not in development.
      // Light theme is enforced in production (no dark mode toggle).
      if (!settingsStore?.is_development) {
        console.log("[PREFERENCES] Production environment: Forcing standard features ON, light theme enforced");
        this._autoScroll = true;
        this._promptEnhancement = true;
        this._simpleChat = true;
        this._darkMode = false; // Enforce light theme in production
      } else {
        // Development logic for Simple Chat:
        // 1. If strictly forced, force it.
        // 2. Otherwise, if no user preference, use system default.
        if (settingsStore?.simple_chat_forced || this.isMobile()) {
          console.log("[PREFERENCES] Simple chat forced items ON (forced=" + settingsStore?.simple_chat_forced + ", mobile=" + this.isMobile() + ")");
          this._simpleChat = true;
        } else if (localStorage.getItem("simpleChat") === null && settingsStore?.simple_chat_enabled_default) {
          console.log("[PREFERENCES] No user preference, enabling Simple Chat by system default");
          this._simpleChat = true;
        }
      }

      // Initial application
      this._applyDarkMode(this._darkMode);
      this._applyAutoScroll(this._autoScroll);
      this._applySpeech(this._speech);
      this._applyShowThoughts(this._showThoughts);
      this._applyShowJson(this._showJson);
      this._applyShowUtils(this._showUtils);
      this._applyExpandTiles(this._expandTiles);
      this._applyShowBackgroundUpdates(this._showBackgroundUpdates);
      this._applyShowDebugInfo(this._showDebugInfo);
      this._applySimpleChat(this._simpleChat);
      this._applyPromptEnhancement(this._promptEnhancement);
      this._applyPersonalizedReply(this._personalizedReply);

      console.log("[PREFERENCES] Store initialized");
    } catch (e) {
      console.error("[PREFERENCES] Failed to initialize preferences store", e);
    }
  },

  _applyDarkMode(value) {
    localStorage.setItem("darkMode", value);
    if (value) {
      document.body.classList.remove("light-mode");
      document.body.classList.add("dark-mode");
    } else {
      document.body.classList.remove("dark-mode");
      document.body.classList.add("light-mode");
    }
  },

  _applySpeech(value) {
    localStorage.setItem("speech", value);
    speechStore.enabled = value;
  },

  _applyAutoScroll(value) {
    localStorage.setItem("autoScroll", value);
  },

  _applyShowThoughts(value) {
    localStorage.setItem("showThoughts", value);
    if (value) {
      document.body.classList.remove("hide-thoughts");
    } else {
      document.body.classList.add("hide-thoughts");
    }
  },

  _applyShowJson(value) {
    localStorage.setItem("showJson", value);
    if (value) {
      document.body.classList.remove("hide-json");
    } else {
      document.body.classList.add("hide-json");
    }
  },

  _applyShowUtils(value) {
    localStorage.setItem("showUtils", value);
    if (value) {
      document.body.classList.remove("hide-utils");
    } else {
      document.body.classList.add("hide-utils");
    }
  },

  _applyExpandTiles(value) {
    localStorage.setItem("expandTiles", value);
    if (value) {
      document.body.classList.add("expand-agent-tiles");
    } else {
      document.body.classList.remove("expand-agent-tiles");
    }
  },

  _applyShowBackgroundUpdates(value) {
    localStorage.setItem("showBackgroundUpdates", value);
  },

  _applyShowDebugInfo(value) {
    localStorage.setItem("showDebugInfo", value);
    if (value) {
      document.body.classList.add("show-debug-info");
    } else {
      document.body.classList.remove("show-debug-info");
    }
  },

  _applySimpleChat(value) {
    localStorage.setItem("simpleChat", value);
    if (value) {
      document.body.classList.add("simple-chat-mode");
    } else {
      document.body.classList.remove("simple-chat-mode");
    }
  },

  _applyPromptEnhancement(value) {
    localStorage.setItem("promptEnhancement", value);
  },

  _applyPersonalizedReply(value) {
    localStorage.setItem("personalizedReply", value);
    // Sync to server settings (fetch → patch → save)
    callJsonApi("/settings_get", null).then(resp => {
      if (resp?.settings) {
        resp.settings.personalized_reply = value;
        return callJsonApi("/settings_set", resp.settings);
      }
    }).catch(e =>
      console.error("[PREFERENCES] Failed to sync personalizedReply to server", e)
    );
  },

  isMobile() {
    return window.innerWidth < 768 || device.getInputType() === 'touch';
  }
};

export const store = createStore("preferences", model);
