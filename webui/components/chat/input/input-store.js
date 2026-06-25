import { createStore } from "../../../js/AlpineStore.js";
import * as shortcuts from "../../../js/shortcuts.js";
import { store as fileBrowserStore } from "../../modals/file-browser/file-browser-store.js";
import { fetchApi } from "../../../js/api.js";

const model = {
  paused: false,
  commonPrompts: [],
  showCommonPrompts: false,
  commonPromptsSearch: "",

  message: '', // Holds current input text
  quotedContext: null, // { text: string, id: string }
  dynamicHints: [], // Array of { text, label?, icon? } for contextual suggestions

  init() {
    console.log("Input store initialized");
    // Event listeners are now handled via Alpine directives in the component
  },

  async sendMessage() {
    // Delegate to the global function
    if (globalThis.sendMessage) {
      await globalThis.sendMessage();
      this.message = ""; // Clear synced message
      this.clearQuote();
    }
  },

  setQuote(text, id) {
    this.quotedContext = { text, id };
    const chatInput = document.getElementById("chat-input");
    if (chatInput) chatInput.focus();
  },

  clearQuote() {
    this.quotedContext = null;
  },

  adjustTextareaHeight() {
    const chatInput = document.getElementById("chat-input");
    if (chatInput) {
      chatInput.style.height = "auto";
      chatInput.style.height = chatInput.scrollHeight + "px";
    }
  },

  async pauseAgent(paused) {
    const prev = this.paused;
    this.paused = paused;
    try {
      const context = globalThis.getContext?.();
      await shortcuts.callJsonApi("/pause", { paused, context });
    } catch (e) {
      this.paused = prev;
      if (globalThis.toastFetchError) {
        globalThis.toastFetchError("Error pausing agent", e);
      }
    }
  },

  async nudge() {
    try {
      const context = globalThis.getContext();
      await shortcuts.callJsonApi("/nudge", { ctxid: context });
    } catch (e) {
      if (globalThis.toastFetchError) {
        globalThis.toastFetchError("Error nudging agent", e);
      }
    }
  },

  async loadKnowledge() {
    try {
      const resp = await shortcuts.callJsonApi("/knowledge_path_get", {
        ctxid: shortcuts.getCurrentContextId(),
      });
      if (!resp.ok) throw new Error("Error getting knowledge path");
      const path = resp.path;

      // open file browser and wait for it to close
      await fileBrowserStore.open(path);

      // progress notification
      shortcuts.frontendNotification({
        type: shortcuts.NotificationType.PROGRESS,
        message: "Loading knowledge...",
        priority: shortcuts.NotificationPriority.NORMAL,
        displayTime: 999,
        group: "knowledge_load",
        frontendOnly: true,
      });

      // then reindex knowledge
      await shortcuts.callJsonApi("/knowledge_reindex", {
        ctxid: shortcuts.getCurrentContextId(),
      });

      // finished notification
      shortcuts.frontendNotification({
        type: shortcuts.NotificationType.SUCCESS,
        message: "Knowledge loaded successfully",
        priority: shortcuts.NotificationPriority.NORMAL,
        displayTime: 2,
        group: "knowledge_load",
        frontendOnly: true,
      });
    } catch (e) {
      // error notification
      shortcuts.frontendNotification({
        type: shortcuts.NotificationType.ERROR,
        message: "Error loading knowledge",
        priority: shortcuts.NotificationPriority.NORMAL,
        displayTime: 5,
        group: "knowledge_load",
        frontendOnly: true,
      });
    }
  },

  // previous implementation without projects
  async _loadKnowledge() {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".txt,.pdf,.csv,.html,.json,.md";
    input.multiple = true;

    input.onchange = async () => {
      try {
        const formData = new FormData();
        for (let file of input.files) {
          formData.append("files[]", file);
        }

        formData.append("ctxid", globalThis.getContext());

        const response = await fetchApi("/import_knowledge", {
          method: "POST",
          body: formData,
        });

        if (!response.ok) {
          if (globalThis.toast)
            globalThis.toast(await response.text(), "error");
        } else {
          const data = await response.json();
          if (globalThis.toast) {
            globalThis.toast(
              "Knowledge files imported: " + data.filenames.join(", "),
              "success"
            );
          }
        }
      } catch (e) {
        if (globalThis.toastFetchError) {
          globalThis.toastFetchError("Error loading knowledge", e);
        }
      }
    };

    input.click();
  },

  async browseFiles(path) {
    if (!path) {
      try {
        const resp = await shortcuts.callJsonApi("/chat_files_path_get", {
          ctxid: shortcuts.getCurrentContextId(),
        });
        if (resp.ok) path = resp.path;
      } catch (_e) {
        console.error("Error getting chat files path", _e);
      }
    }
    await fileBrowserStore.open(path);
  },

  getFilteredPrompts() {
    let filtered = this.commonPrompts;
    if (this.commonPromptsSearch) {
      const search = this.commonPromptsSearch.toLowerCase();
      filtered = filtered.filter(p => p.prompt.toLowerCase().includes(search));
    }
    // Limit to 5 results as requested
    return filtered.slice(0, 5);
  },

  async deletePrompt(prompt) {
    const confirmed = await Alpine.store('confirmation').confirm(
      'Are you sure you want to delete this prompt?',
      { target: document.activeElement }
    );
    if (!confirmed) return;

    try {
      const resp = await shortcuts.callJsonApi("/api/prompts/common/delete", { prompt });
      if (resp && resp.success) {
        // Refresh the list
        await this.loadCommonPrompts();
        if (globalThis.toast) {
          globalThis.toast(resp.message || "Prompt deleted successfully", "success");
        }
      } else {
        throw new Error(resp.error || "Failed to delete prompt");
      }
    } catch (e) {
      console.error("Error deleting prompt", e);
      if (globalThis.toastFetchError) {
        globalThis.toastFetchError("Error deleting prompt", e);
      }
    }
  },

  formatPromptTitle(text) {
    if (!text) return "";
    const words = text.split(/\s+/);
    if (words.length <= 7) return text;
    return words.slice(0, 7).join(" ") + "...";
  },

  async loadCommonPrompts() {
    try {
      const resp = await shortcuts.callJsonApi("/api/prompts/common");
      if (resp && resp.success) {
        this.commonPrompts = resp.prompts || [];
      }
    } catch (e) {
      console.error("Error loading common prompts", e);
    }
  },

  async selectPrompt(prompt) {
    const textarea = document.getElementById('chat-input');
    if (textarea) {
      textarea.value = prompt;
      this.adjustTextareaHeight();
      textarea.focus();
    }
    this.showCommonPrompts = false;
  },

  autoAttachWarning: false, // true when input exceeds auto-attachment threshold
  charCount: 0, // live character count

  handleInput(event) {
    this.message = event.target.value;
    this.charCount = this.message.length;
    // Show warning when approaching auto-attachment threshold (4000 chars)
    this.autoAttachWarning = this.charCount > 4000;
    this.adjustTextareaHeight();
  },

  selectHint(text) {
    const textarea = document.getElementById('chat-input');
    if (textarea) {
      textarea.value = text;
      this.message = text;
      this.adjustTextareaHeight();
      textarea.focus();
    }
    // Clear hints after selection
    this.dynamicHints = [];
  },

  setDynamicHints(hints) {
    // hints: Array of { text, label?, icon? }
    this.dynamicHints = (hints || []).slice(0, 5); // Max 5 hints
  },

  clearDynamicHints() {
    this.dynamicHints = [];
  },

  async saveAsGoldenPrompt(promptText = null) {
    const textarea = document.getElementById('chat-input');
    const text = promptText || (textarea ? textarea.value : '');
    if (!text || !text.trim()) return;

    try {
      const resp = await shortcuts.callJsonApi("/api/prompts/golden/save", { prompt: text.trim() });
      if (resp && resp.success) {
        if (window.showToast) window.showToast("Saved as prompt template", "success");
        this.loadCommonPrompts();
      } else {
        throw new Error(resp.error || "Failed to save prompt");
      }
    } catch (e) {
      console.error("Error saving prompt", e);
      if (window.showToast) window.showToast("Error saving prompt", "error");
    }
  },
};

const store = createStore("chatInput", model);

export { store };
