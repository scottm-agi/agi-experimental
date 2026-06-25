import * as msgs from "./js/messages.js";
import * as api from "./js/api.js";
import * as css from "./js/css.js";
import { sleep } from "./js/sleep.js";
import {
  saveChatHistoryToCache,
  loadChatHistoryFromCache,
  renderCachedMessages,
  hasCachedMessages
} from "./js/chat-history-cache.js";
import { store as attachmentsStore } from "./components/chat/attachments/attachmentsStore.js";
import { store as speechStore } from "./components/chat/speech/speech-store.js";
import { store as notificationStore } from "./components/notifications/notification-store.js";
import { store as preferencesStore } from "./components/sidebar/bottom/preferences/preferences-store.js";
import { store as inputStore } from "./components/chat/input/input-store.js";
import { store as chatsStore } from "./components/sidebar/chats/chats-store.js";
import { store as tasksStore } from "./components/sidebar/tasks/tasks-store.js";
import { store as scheduledTasksStore } from "./components/sidebar/scheduled-tasks/scheduled-tasks-store.js";
import { store as chatTopStore } from "./components/chat/top-section/chat-top-store.js";
import { store as sidebarStore } from "./components/sidebar/sidebar-store.js";
import { store as scrollButtonsStore } from "./components/chat/scroll-buttons/scroll-buttons-store.js";
import { store as settingsScrollButtonsStore } from "./components/settings/scroll-buttons/settings-scroll-buttons-store.js";
import { store as settingsStore } from "./js/settings-store.js";
import { store as confirmationStore } from "./js/confirmation-store.js";
import { store as wakeLockStore } from "./js/wake-lock-store.js";
import { insertContentToChatHistory } from "./js/dom-utils.js";

// Declare variables for DOM elements, they will be assigned on initialization or DOMContentLoaded
let leftPanel = null,
  rightPanel = null,
  container = null,
  chatInput = null,
  chatHistory = null,
  sendButton = null,
  inputSection = null,
  statusSection = null,
  progressBar = null,
  autoScrollSwitch = null,
  timeDate = null;

function assignDOMElements() {
  leftPanel = document.getElementById("left-panel");
  rightPanel = document.getElementById("right-panel");
  container = document.querySelector(".container");
  chatInput = document.getElementById("chat-input");
  chatHistory = document.getElementById("chat-history");
  sendButton = document.getElementById("send-button");
  inputSection = document.getElementById("input-section");
  statusSection = document.getElementById("status-section");
  progressBar = document.getElementById("progress-bar");
  autoScrollSwitch = document.getElementById("auto-scroll-switch");
  timeDate = document.getElementById("time-date-container");
}

function initUI() {
  // Global error handling for debugging
  window.onerror = function (message, source, lineno, colno, error) {
    console.error(`[GLOBAL_ERROR] ${message} at ${source}:${lineno}:${colno}`, error);
    return false;
  };

  window.onunhandledrejection = function (event) {
    console.error('[UNHANDLED_REJECTION]', event.reason);
  };

  // Heartbeat disabled by default - uncomment for debugging JS execution hangs
  // setInterval(() => {
  //   console.log(`[UI_HEARTBEAT] ${new Date().toLocaleTimeString()}`);
  // }, 5000);

  const runInit = () => {
    assignDOMElements();

    if (chatHistory) {
      chatHistory.addEventListener("scroll", updateAfterScroll);
    }

    // Start polling for updates
    startPolling();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", runInit);
  } else {
    runInit();
  }
}

let autoScroll = true;
let context = null;
globalThis.messageMap = new Map(); // id -> messageElement
console.log("[SEQUENCING] Global messageMap initialized:", globalThis.messageMap);
let skipOneSpeech = false;
let currentPollRenderId = 0;
let isPolling = false;
let isRendering = false;

// Sidebar toggle logic is now handled by sidebar-store.js

export async function sendMessage() {
  const chatInputEl = document.getElementById("chat-input");
  if (!chatInputEl) {
    console.warn("chatInput not available, cannot send message");
    return;
  }
  try {
    let message = chatInputEl.value.trim();
    const attachmentsWithUrls = attachmentsStore.getAttachmentsForSending();

    // AUTO-ATTACHMENT: Convert oversized input to MD attachment (Forgejo #972)
    // This prevents the history summarizer from ever truncating the user's intent.
    const AUTO_ATTACH_THRESHOLD = 4000; // chars — matches legit prompt max size
    if (message.length > AUTO_ATTACH_THRESHOLD) {
      console.log(`[AUTO-ATTACH] Message is ${message.length} chars (>${AUTO_ATTACH_THRESHOLD}), converting to attachment`);
      
      // Create MD file from full text
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const filename = `user-prompt-${timestamp}.md`;
      const blob = new Blob([message], { type: 'text/markdown' });
      const file = new File([blob], filename, { type: 'text/markdown' });
      
      // Add to attachments array (will be sent via FormData)
      attachmentsWithUrls.push({
        file: file,
        type: 'file',
        name: filename,
        extension: 'md',
      });

      // Also add to the store so it shows in UI
      attachmentsStore.addAttachment({
        file: file,
        type: 'file',
        name: filename,
        extension: 'md',
        displayInfo: attachmentsStore.getAttachmentDisplayInfo(file),
      });

      // Replace message with summary header + first 500 chars  
      const preview = message.substring(0, 500);
      message = `📎 Full prompt attached as ${filename} (${message.length} chars)\n\n${preview}...`;
      console.log(`[AUTO-ATTACH] Created attachment ${filename}, message truncated to summary`);
    }
    const hasAttachments = attachmentsWithUrls.length > 0;

    // Handle quoted context
    if (inputStore.quotedContext) {
      const quoteText = inputStore.quotedContext.text;
      message = `> ${quoteText.split('\n').join('\n> ')}\n\n${message}`;
    }

    if (message || hasAttachments) {
      const messageId = generateGUID();

      // OPTIMISTIC UI: Clear input and attachments immediately
      chatInputEl.value = "";
      attachmentsStore.clearAttachments();
      adjustTextareaHeight();

      // OPTIMISTIC UI: Render the user message in history immediately
      const heading = (hasAttachments && attachmentsWithUrls.length > 0)
        ? "Uploading attachments..."
        : "User message";

      setMessage(messageId, "user", heading, message, false, null, {}, null, null, false, false, Date.now(), "");
      updateAfterScroll();

      // Now handle the background request
      const project = chatsStore.getProjectFilter();
      let response;
      if (hasAttachments) {
        const formData = new FormData();
        formData.append("text", message);
        formData.append("context", context);
        formData.append("project", project || "");
        formData.append("message_id", messageId);

        for (let i = 0; i < attachmentsWithUrls.length; i++) {
          formData.append("attachments", attachmentsWithUrls[i].file);
        }

        response = await api.fetchApi("/message_async", {
          method: "POST",
          body: formData,
        });
      } else {
        // For text-only messages
        const data = {
          text: message,
          context,
          project,
          message_id: messageId,
        };
        response = await api.fetchApi("/message_async", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify(data),
        });
      }

      // Handle response (mostly for context reconciliation)
      const jsonResponse = await response.json();
      if (!jsonResponse) {
        console.warn("No valid JSON response from /message_async");
      } else if (jsonResponse.context && jsonResponse.context !== context) {
        // Ensure the store and globals are in sync if context was null or changed
        setContext(jsonResponse.context);
      }
    }
  } catch (e) {
    toastFetchError("Error sending message", e); // Will use new notification system
  }
}
globalThis.sendMessage = sendMessage;

export function toastFetchError(text, error) {
  console.error(text, error);
  // Use new frontend error notification system (async, but we don't need to wait)
  const errorMessage = error?.message || error?.toString() || "Unknown error";

  if (getConnectionStatus()) {
    // Backend is connected, just show the error
    toastFrontendError(`${text}: ${errorMessage}`).catch((e) =>
      console.error("Failed to show error toast:", e)
    );
  } else {
    // Backend is disconnected, show connection error
    toastFrontendError(
      `${text} (backend appears to be disconnected): ${errorMessage}`,
      "Connection Error"
    ).catch((e) => console.error("Failed to show connection error toast:", e));
  }
}
globalThis.toastFetchError = toastFetchError;

// Event listeners will be set up in DOMContentLoaded

export function updateChatInput(text) {
  const chatInputEl = document.getElementById("chat-input");
  if (!chatInputEl) {
    console.warn("`chatInput` element not found, cannot update.");
    return;
  }
  console.log("updateChatInput called with:", text);

  // Append text with proper spacing
  const currentValue = chatInputEl.value;
  const needsSpace = currentValue.length > 0 && !currentValue.endsWith(" ");
  chatInputEl.value = currentValue + (needsSpace ? " " : "") + text + " ";

  // Adjust height and trigger input event
  adjustTextareaHeight();
  chatInputEl.dispatchEvent(new Event("input"));

  console.log("Updated chat input value:", chatInputEl.value);
}

async function updateUserTime() {
  let userTimeElement = document.getElementById("time-date");

  while (!userTimeElement) {
    await sleep(100);
    userTimeElement = document.getElementById("time-date");
  }

  const now = new Date();
  const hours = now.getHours();
  const minutes = now.getMinutes();
  const seconds = now.getSeconds();
  const ampm = hours >= 12 ? "pm" : "am";
  const formattedHours = hours % 12 || 12;

  // Format the time
  const timeString = `${formattedHours}:${minutes
    .toString()
    .padStart(2, "0")}:${seconds.toString().padStart(2, "0")} ${ampm}`;

  // Format the date
  const options = { year: "numeric", month: "short", day: "numeric" };
  const dateString = now.toLocaleDateString(undefined, options);

  // Update the HTML
  userTimeElement.innerHTML = `${timeString}<br><span id="user-date">${dateString}</span>`;
}

updateUserTime();
setInterval(updateUserTime, 1000);

function setMessage(id, type, heading, content, temp, icon = null, kvps = null, timestamp = null, fragment = null, isSummary = false, verbose = false, sequenceId = 0, hash = "") {
  // Use sequenceId and hash for O(1) targeting and performance
  // Prefer hash for mapping to survive refreshes/re-renders
  const existingElement = hash ? globalThis.messageMap.get(hash) : globalThis.messageMap.get(id);

  const result = msgs.setMessage(id, type, heading, content, temp, icon, kvps, timestamp, fragment, isSummary, verbose, sequenceId, hash, existingElement);

  if (result && result.nodeType === 1) {
    globalThis.messageMap.set(hash || id, result);
  }

  // Only update scroll if we're not rendering into a fragment (which means direct DOM update)
  if (!fragment) {
    const chatHistoryEl = document.getElementById("chat-history");
    if (preferencesStore.autoScroll && chatHistoryEl) {
      chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
    }
  }
  return result;
}

globalThis.loadKnowledge = async function () {
  await inputStore.loadKnowledge();
};

function adjustTextareaHeight() {
  const chatInputEl = document.getElementById("chat-input");
  if (chatInputEl) {
    chatInputEl.style.height = "auto";
    chatInputEl.style.height = chatInputEl.scrollHeight + "px";
  }
}

export const sendJsonData = async function (url, data) {
  return await api.callJsonApi(url, data);
  // const response = await api.fetchApi(url, {
  //     method: 'POST',
  //     headers: {
  //         'Content-Type': 'application/json'
  //     },
  //     body: JSON.stringify(data)
  // });

  // if (!response.ok) {
  //     const error = await response.text();
  //     throw new Error(error);
  // }
  // const jsonResponse = await response.json();
  // return jsonResponse;
};
globalThis.sendJsonData = sendJsonData;

function generateGUID() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
    var r = (Math.random() * 16) | 0;
    var v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export function getConnectionStatus() {
  return chatTopStore.connected;
}
globalThis.getConnectionStatus = getConnectionStatus;

function setConnectionStatus(connected) {
  chatTopStore.connected = connected;
  // connectionStatus = connected;
  // // Broadcast connection status without touching Alpine directly
  // try {
  //   window.dispatchEvent(
  //     new CustomEvent("connection-status", { detail: { connected } })
  //   );
  // } catch (_e) {
  //   // no-op
  // }
}

let lastLogVersion = 0;
let lastLogGuid = "";
let lastSpokenNo = 0;

export async function poll() {
  if (isPolling || isRendering) return false;
  isPolling = true;

  let updated = false;
  const startTime = Date.now();
  try {
    // Get timezone from navigator
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;

    const log_from = lastLogVersion;
    const response = await sendJsonData("/poll", {
      log_from: log_from,
      log_guid: lastLogGuid, // Send current log_guid to backend
      notifications_from: notificationStore.lastNotificationVersion || 0,
      context: context || null,
      timezone: timezone,
      // Differential polling versions
      contexts_version: chatsStore.version || -1,
      tasks_version: scheduledTasksStore.version || -1,
    });

    const duration = Date.now() - startTime;
    if (duration > 3000) {
      console.warn(`[UI_DEBUG] Poll request took ${duration}ms`);
    }

    // Check if the response is valid
    if (!response) {
      console.error("Invalid response from poll endpoint");
      return false;
    }

    // deselect chat if it is requested by the backend
    // BUT: Don't deselect if the context belongs to a scheduled task - the task may not have
    // been run yet (so no AgentContext exists), but we should still show the task's context
    if (response.deselect_chat) {
      const contextInScheduledTasks = scheduledTasksStore.tasks.some(
        t => t.context_id === context || t.uuid === context
      );
      if (!contextInScheduledTasks) {
        chatsStore.deselectChat();
        return;
      }
      // If it's a scheduled task, don't deselect - continue with the poll to show the task context
    }

    if (
      response.context != context &&
      !(response.context === null && context === null) &&
      context !== null
    ) {
      return;
    }

    // if the chat has been reset or GUID changed (e.g. pruned), restart this poll with new data
    if (lastLogGuid != response.log_guid) {
      const chatHistoryEl = document.getElementById("chat-history");

      // Optimization: Only clear if we don't have cached content that matches this GUID
      // (Currently we don't store GUID in cache, so we always clear unless we just switched)
      if (chatHistoryEl) chatHistoryEl.innerHTML = "";

      // BUG FIX: Clear messageMap when GUID changes to prevent stale detached DOM
      // references. Without this, the hash-based early exit in setMessage would
      // "update" detached elements (removed by innerHTML="") instead of creating
      // new visible ones — causing user prompts and other messages to disappear.
      // This was intermittent because GUID only changes when gate extensions call
      // remove_item() or when log pruning fires during subordinate agent execution.
      if (globalThis.messageMap) {
        globalThis.messageMap.clear();
        console.log("[SEQUENCING] messageMap cleared on GUID change (was:", lastLogGuid, "now:", response.log_guid, ")");
      }
      msgs.resetMessageState();

      // When GUID changes, we expect a full refresh of the history
      // The backend now returns the last 100 logs on GUID mismatch
      lastLogVersion = 0;
      lastLogGuid = response.log_guid;
    }

    if (lastLogVersion != response.log_version) {
      updated = true;

      // Optimization: Render logs in chunks if there are many
      // Filter out unwanted message types before rendering
      // Filter and sort logs to ensure strict ordering
      const filteredLogs = response.logs.filter(log => {
        return !msgs.isMessageEmpty(log.type, log.heading, log.content, log.kvps);
      }).sort((a, b) => (a.no || 0) - (b.no || 0));

      if (filteredLogs.length > 5) {
        const logs = filteredLogs;
        let index = 0;
        const pollRenderId = ++currentPollRenderId;
        const MAX_FRAME_TIME = 16; // ms

        function renderNextLogChunk() {
          try {
            const startTime = performance.now();

            // Check if this rendering task has been cancelled
            if (pollRenderId !== currentPollRenderId) {
              console.log(`[Poll] Rendering cancelled (ID: ${pollRenderId})`);
              isRendering = false;
              return;
            }

            const fragment = document.createDocumentFragment();

            // Time-budgeted loop: Process messages until we hit the time limit
            while (index < logs.length && (performance.now() - startTime) < MAX_FRAME_TIME) {
              const log = logs[index];
              const messageId = log.id || log.no;
              const hash = log.kvps?.hash || "";
              const sequence_id = log.kvps?.sequence_id || 0;

              const el = setMessage(
                messageId,
                log.type,
                log.heading,
                log.content,
                log.temp,
                log.icon,
                log.kvps,
                log.timestamp,
                fragment,
                log.is_summary || false,
                log.verbose || false,
                sequence_id,
                hash
              );
              index++;

              // If a single message took more than our budget, we break and handle it in the next frame
              if ((performance.now() - startTime) >= MAX_FRAME_TIME) break;
            }

            // Batch process the fragment insertion at the correct location
            const chatHistory = document.getElementById('chat-history');
            if (chatHistory && fragment.childNodes.length > 0) {
              // Find the correct insertion point based on the first log in the batch
              // Hardened: Compare sequence ids of groups
              const firstLogSeq = logs[index - fragment.childNodes.length]?.kvps?.sequence_id || logs[index - fragment.childNodes.length]?.no || 0;
              const nextSibling = Array.from(chatHistory.children)
                .find(el => (parseInt(el.dataset.msgSequence) || parseInt(el.dataset.msgIndex) || 0) > firstLogSeq);

              if (nextSibling) {
                chatHistory.insertBefore(fragment, nextSibling);
              } else {
                chatHistory.appendChild(fragment);
              }

              if (preferencesStore.autoScroll && !nextSibling) {
                chatHistory.scrollTop = chatHistory.scrollHeight;
              }
            }

            if (index < logs.length) {
              requestAnimationFrame(renderNextLogChunk);
            } else {
              isRendering = false;
              afterMessagesUpdate(response);

              // Dev Observability: Log messageMap state if debug_sequencing is enabled
              if (globalThis.messageMap && globalThis.messageMap.size > 0 && localStorage.getItem('debug_sequencing') === 'true') {
                console.log(`[SEQUENCING] messageMap updated (Size: ${globalThis.messageMap.size})`);
                console.table(Array.from(globalThis.messageMap.entries()).map(([key, el]) => ({
                  key,
                  sequence: el.dataset.msgSequence,
                  hash: el.dataset.msgHash,
                  text: el.dataset.msgContent?.substring(0, 30)
                })));
              }
            }
          } catch (e) {
            console.error(`[Poll] Rendering error (ID: ${pollRenderId})`, e);
            isRendering = false;
          }
        }
        isRendering = true;
        renderNextLogChunk();
      } else {
        const fragment = document.createDocumentFragment();
        for (const log of filteredLogs) {
          const messageId = log.id || log.no;
          const hash = log.kvps?.hash || "";
          const sequence_id = log.kvps?.sequence_id || 0;

          setMessage(
            messageId,
            log.type,
            log.heading,
            log.content,
            log.temp,
            log.icon,
            log.kvps,
            log.timestamp,
            fragment,
            log.is_summary || false,
            log.verbose || false,
            sequence_id,
            hash
          );
        }
        const chatHistory = document.getElementById('chat-history');
        if (chatHistory && fragment.childNodes.length > 0) {
          // Fix: Also use insertBefore for short-poll updates to ensure correct order
          const firstLogSeq = filteredLogs[0]?.kvps?.sequence_id || filteredLogs[0]?.no || 0;
          const nextSibling = Array.from(chatHistory.children)
            .find(el => (parseInt(el.dataset.msgSequence) || parseInt(el.dataset.msgIndex) || 0) > firstLogSeq);

          if (nextSibling) {
            chatHistory.insertBefore(fragment, nextSibling);
          } else {
            chatHistory.appendChild(fragment);
            if (preferencesStore.autoScroll) {
              chatHistory.scrollTop = chatHistory.scrollHeight;
            }
          }
        }
        afterMessagesUpdate(response);
      }
    }

    if (response.logs && response.logs.length > 0) {
      // Set lastLogVersion to the index of the next expected log
      lastLogVersion = response.logs[response.logs.length - 1].no + 1;
    }
    lastLogGuid = response.log_guid;

    updateProgress(response.log_progress, response.log_progress_active);

    // Update notifications from response
    notificationStore.updateFromPoll(response);

    //set ui model vars from backend
    inputStore.paused = response.paused;
    globalThis._agentIdle = response.agent_idle;  // Exposed for chat queue drain

    // Issue #775: Push contextual next-step suggestion hints from backend
    if (response.hints && Array.isArray(response.hints)) {
      inputStore.setDynamicHints(response.hints);
    }

    // Issue #724: Keep screen on while agent is working (mobile)
    wakeLockStore.onAgentStateChange(response.paused);

    // Update status icon state
    setConnectionStatus(true);

    // Update chats list using store
    if (response.contexts !== undefined && response.contexts !== null) {
      chatsStore.applyContexts(response.contexts, response.contexts_version);
    }

    // Update scheduled tasks list using store (from scheduler system)
    // Note: response.tasks contains scheduler tasks, NOT session tasks
    // Session tasks are fetched separately by tasksStore.fetchTasks() via /api/session_tasks/{contextId}
    if (response.tasks !== undefined && response.tasks !== null) {
      scheduledTasksStore.applyTasks(response.tasks, response.tasks_version);
    }

    // Make sure the active context is properly selected in both lists
    if (context) {
      // Check if context belongs to a scheduled task to preserve UI state
      const contextInScheduledTasks = scheduledTasksStore.tasks.some(
        t => t.context_id === context || t.uuid === context
      );

      // Update selection in both stores
      chatsStore.setSelected(context, contextInScheduledTasks);

      const contextInChats = chatsStore.contains(context);
      const contextInTasks = tasksStore.contains(context);
      // contextInScheduledTasks is already declared above

      if (contextInTasks) {
        tasksStore.setSelected(context);
      }

      // Don't fall back if context is a scheduled task's context
      if (!contextInChats && !contextInTasks && !contextInScheduledTasks) {
        if (chatsStore.contexts.length > 0) {
          // If it doesn't exist in the list but other contexts do, fall back to the first
          const firstChatId = chatsStore.firstId();
          if (firstChatId) {
            setContext(firstChatId);
            chatsStore.setSelected(firstChatId);
          }
        } else if (typeof deselectChat === "function") {
          // No contexts remain – clear state so the welcome screen can surface
          deselectChat();
        }
      }
    } else {
      const welcomeStore =
        globalThis.Alpine && typeof globalThis.Alpine.store === "function"
          ? globalThis.Alpine.store("welcomeStore")
          : null;
      const welcomeVisible = Boolean(welcomeStore && welcomeStore.isVisible);

      // No context selected, try to select the first available item unless welcome screen is active
      const chatsList = chatsStore.contexts || [];
      if (!welcomeVisible && chatsList.length > 0) {
        const firstChatId = chatsStore.firstId();
        if (firstChatId) {
          setContext(firstChatId);
          chatsStore.setSelected(firstChatId);
        }
      }
    }

    lastLogVersion = response.log_version;
    lastLogGuid = response.log_guid;
  } catch (error) {
    console.error("Error:", error);
    setConnectionStatus(false);
  } finally {
    isPolling = false;
  }

  return updated;
}
globalThis.poll = poll;

function afterMessagesUpdate(response) {
  const logs = response?.logs || [];
  // Ensure the UI context is synced if backend reports a mismatch but we have active logs
  if (response.context && response.context !== context && logs.length > 0) {
    console.warn(`[Poll] Context mismatch: UI=${context} vs Backend=${response.context}. Syncing UI.`);
    setContext(response.context);
  }
  if (localStorage.getItem("speech") == "true") {
    speakMessages(logs);
  }

  // ISSUE #140 FIX: Update cache after messages are received
  // This ensures all chat history stays cached with latest messages for instant switching
  if (context && logs && logs.length > 0) {
    // Debounce: only save if we haven't saved in the last 2 seconds
    const now = Date.now();
    if (!afterMessagesUpdate._lastSave || now - afterMessagesUpdate._lastSave > 2000) {
      afterMessagesUpdate._lastSave = now;
      // Pass logs directly to avoid expensive DOM scraping, include metadata
      saveChatHistoryToCache(context, logs, response.log_guid, response.log_version);
    }
  }
}

function speakMessages(logs) {
  if (skipOneSpeech) {
    skipOneSpeech = false;
    return;
  }
  // log.no, log.type, log.heading, log.content
  for (let i = logs.length - 1; i >= 0; i--) {
    const log = logs[i];

    // if already spoken, end
    // if(log.no < lastSpokenNo) break;

    // finished response
    if (log.type == "response") {
      // lastSpokenNo = log.no;
      speechStore.speakStream(
        getChatBasedId(log.no),
        log.content,
        log.kvps?.finished
      );
      return;

      // finished LLM headline, not response
    } else if (
      log.type == "agent" &&
      log.kvps &&
      log.kvps.headline &&
      log.kvps.tool_args &&
      log.kvps.tool_name != "response"
    ) {
      // lastSpokenNo = log.no;
      speechStore.speakStream(getChatBasedId(log.no), log.kvps.headline, true);
      return;
    }
  }
}

function updateProgress(progress, active) {
  const progressBarEl = document.getElementById("progress-bar");
  if (!progressBarEl) return;
  if (!progress) progress = "";

  if (!active) {
    removeClassFromElement(progressBarEl, "shiny-text");
  } else {
    addClassToElement(progressBarEl, "shiny-text");
  }

  progress = msgs.convertIcons(progress);

  if (progressBarEl.innerHTML != progress) {
    progressBarEl.innerHTML = progress;
  }
}

globalThis.pauseAgent = async function (paused) {
  await inputStore.pauseAgent(paused);
};

function generateShortId() {
  const chars =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let result = "";
  for (let i = 0; i < 8; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return result;
}

export const newContext = function () {
  context = generateShortId();
  setContext(context);
};
globalThis.newContext = newContext;

export const setContext = function (id, isScheduledTask = false) {
  if (id == context) return;

  // ISSUE #140 FIX: Save current chat history to cache before switching
  // This preserves scheduled task chat messages when switching contexts
  if (context) {
    saveChatHistoryToCache(context);
  }

  // Clear global messageMap when switching context to prevent ID collisions
  if (globalThis.messageMap) {
    globalThis.messageMap.clear();
    console.log(`[SEQUENCING] messageMap cleared for context switch to: ${id}`);
  }

  context = id;
  // Always reset the log tracking variables when switching contexts
  // This ensures we get fresh data from the backend
  lastLogGuid = "";
  lastLogVersion = 0;
  lastSpokenNo = 0;

  // Stop speech when switching chats
  speechStore.stopAudio();

  // Clear the chat history immediately to avoid showing stale content
  const chatHistoryEl = document.getElementById("chat-history");
  if (chatHistoryEl) chatHistoryEl.innerHTML = "";

  // ISSUE #140 FIX: Restore cached messages for the new context
  // This shows previously cached messages while waiting for backend poll
  if (id && hasCachedMessages(id)) {
    const cache = loadChatHistoryFromCache(id, true); // Get full cache including metadata
    if (cache && cache.messages && cache.messages.length > 0) {
      renderCachedMessages(cache.messages, msgs.setMessage);
      // Restore metadata to allow the poll to resume correctly
      lastLogGuid = cache.guid || "";
      lastLogVersion = cache.version || 0;

      // Populate messageMap from cache so the next poll doesn't duplicate them
      if (globalThis.messageMap) {
        cache.messages.forEach(msg => {
          const el = document.getElementById(`message-${msg.id}`);
          if (el && msg.hash) {
            globalThis.messageMap.set(msg.hash, el);
          }
        });
      }

      console.log(`[setContext] Restored ${cache.messages.length} cached messages (Version: ${lastLogVersion}) for context ${id}`);
    }
  }

  // Update both selected states using stores
  chatsStore.setSelected(id, isScheduledTask);
  tasksStore.setSelected(id);

  // CRITICAL: Remove dashboard-mode when switching to any regular chat
  // Without this, the CSS hides ALL message elements (message-group, message-container, etc.)
  // SKIP teardown when openDashboard() is actively initializing a dashboard context
  if (document.body.classList.contains('dashboard-mode') && !globalThis._openingDashboard) {
    document.body.classList.remove('dashboard-mode');
    stopDashboardObserver();
    stopDashboardRefresh();
    // Remove all dashboard-injected DOM nodes from chat-history
    // These are message-group elements containing A2UI tiles that openDashboard/refreshDashboard appended
    removeDashboardNodes();
    // Reset project filter to "All Projects" so sidebar shows all chats
    const projectsStore = Alpine.store('projectSearch');
    if (projectsStore && projectsStore.selectedProjectFilter !== '') {
      projectsStore.selectProject('');
    }
    console.log('[setContext] Removed dashboard-mode for chat:', id);
  }

  // Dispatch global event for all components to react to context change
  window.dispatchEvent(
    new CustomEvent("context-changed", { detail: { contextId: id } })
  );

  //skip one speech if enabled when switching context
  if (localStorage.getItem("speech") == "true") skipOneSpeech = true;
};

export const deselectChat = function () {
  // Clear current context to show welcome screen
  setContext(null);

  // Clear localStorage selections so we don't auto-restore
  localStorage.removeItem("lastSelectedChat");
  localStorage.removeItem("lastSelectedTask");

  // Clear the chat history
  if (chatHistory) {
    chatHistory.innerHTML = "";
  } else {
    console.warn("[UI] chatHistory element not found during deselectChat");
    // Attempt fallback lookup
    const el = document.getElementById("chat-history");
    if (el) el.innerHTML = "";
  }
};
globalThis.deselectChat = deselectChat;

export const openDashboard = async function () {
  const projectsStore = Alpine.store('projectSearch');
  if (!projectsStore) return;

  projectsStore.selectProject('agixdashboard');
  const chatsStore = Alpine.store('chats');
  if (!chatsStore) return;

  // Set flag to prevent setContext() from tearing down dashboard-mode
  // during the chat creation/selection that follows
  globalThis._openingDashboard = true;

  // Small delay to allow filter to apply
  await new Promise(r => setTimeout(r, 50));

  try {
    const filtered = chatsStore.getFilteredContexts();
    if (filtered.length > 0) {
      await chatsStore.selectChat(filtered[0].id);
    } else {
      await chatsStore.newChat();
    }
  } finally {
    globalThis._openingDashboard = false;
  }

  // Re-assert dashboard state AFTER chat selection/creation.
  // setContext() may have partially modified state despite the flag guard.
  projectsStore.selectProject('agixdashboard');
  document.body.classList.add('dashboard-mode');
  startDashboardObserver();
  startDashboardRefresh();

  // Name the dashboard chat so it doesn't show as "Chat #N"
  // Must persist via backend API (client-side only gets overwritten by poll)
  const currentId = chatsStore.selected || (globalThis.getContext ? globalThis.getContext() : null);
  if (currentId) {
    // Optimistic UI update (instant feedback)
    chatsStore.contexts = chatsStore.contexts.map(ctx =>
      ctx.id === currentId ? { ...ctx, name: 'System Dashboard' } : ctx
    );
    // Persist to backend (survives poll cycle)
    globalThis.sendJsonData?.("/chat_rename", {
      context_id: currentId,
      name: "System Dashboard"
    }).catch(() => {}); // best-effort, don't block dashboard
  }

  // Wait for chat to be ready — newChat() triggers a poll that can reset the DOM,
  // so we retry tile injection to handle the race condition.
  const injectDashboardTiles = async () => {
    const chatHistory = document.getElementById("chat-history");
    if (!chatHistory) return false;
    if (chatHistory.querySelector('.a2ui-tile')) return true; // Already has tiles

    // GUARD: verify we're still in dashboard mode
    if (!document.body.classList.contains('dashboard-mode')) return true; // bail silently

    try {
      const response = await globalThis.sendJsonData("/dashboard_a2ui", {});
      if (!document.body.classList.contains('dashboard-mode')) return true; // user left
      if (response && response.messages && response.messages.length > 0) {
        const { renderA2UITile } = await import("./components/chat/a2ui-tile/a2ui-tile-renderer.js");
        if (chatHistory && renderA2UITile) {
          const tile = renderA2UITile(response);
          if (tile) {
            tile.classList.add('a2ui-tile--inline');
            const group = document.createElement('div');
            group.className = 'message-group dashboard-visible';
            group.appendChild(tile);
            chatHistory.appendChild(group);
            enforceSingleDashboardPane();
            return true;
          }
        }
      }
    } catch (e) {
      console.warn("[Dashboard] API fetch failed:", e);
    }
    return false;
  };

  // Retry up to 3 times — poll from newChat() can wipe DOM after initial inject
  for (let attempt = 0; attempt < 3; attempt++) {
    await new Promise(r => setTimeout(r, attempt === 0 ? 400 : 600));
    const done = await injectDashboardTiles();
    if (done) break;
  }

  // Fallback: send prompt to LLM if API failed
  const sendInput = document.getElementById("chat-input");
  if (sendInput && globalThis.input) {
    sendInput.value = "Show default dashboard: system overview with stat cards (projects, chats, memory, disk), top active projects table, and token usage bar chart";
    globalThis.input();
  }
};
globalThis.openDashboard = openDashboard;

// Auto-open dashboard on page load (self-healing: always show dashboard on refresh)
setTimeout(() => {
  // Only auto-open if no specific chat is loaded yet (fresh load / refresh)
  const chatHistory = document.getElementById("chat-history");
  const hasTiles = chatHistory && chatHistory.querySelector('.a2ui-tile');
  const hasMessages = chatHistory && chatHistory.querySelector('.message-group');
  // GUARD: Don't auto-open if user has already navigated to a specific chat
  const chatsStore = Alpine.store('chats');
  const hasActiveChat = chatsStore && chatsStore.selected;
  if (!hasTiles && !hasMessages && !hasActiveChat) {
    openDashboard();
  }
}, 1500); // Wait for Alpine stores + contexts to initialize

// Sync body class with agixdashboard project
window.addEventListener("project-filter-changed", (event) => {
  if (event.detail.projectName === 'agixdashboard') {
    document.body.classList.add('dashboard-mode');
    startDashboardObserver();
    startDashboardRefresh(); // Issue #798: auto-refresh
  } else {
    document.body.classList.remove('dashboard-mode');
    stopDashboardObserver();
    stopDashboardRefresh(); // Issue #798: stop auto-refresh
    removeDashboardNodes(); // Remove stale dashboard DOM nodes
  }
});

// Issue #798: Dashboard auto-refresh
let _dashboardRefreshInterval = null;

// Remove all dashboard-injected DOM nodes from #chat-history
// openDashboard() and refreshDashboard() append message-group divs with A2UI tiles
// These MUST be removed when leaving dashboard mode, or they bleed into other chats
function removeDashboardNodes() {
  const chatHistory = document.getElementById('chat-history');
  if (!chatHistory) return;
  const dashboardGroups = chatHistory.querySelectorAll('.message-group.dashboard-visible');
  dashboardGroups.forEach(g => g.remove());
  // Also catch any orphaned A2UI tiles injected directly (not wrapped in message-group)
  const orphanTiles = chatHistory.querySelectorAll('.a2ui-tile--inline');
  orphanTiles.forEach(t => {
    const parent = t.closest('.message-group') || t.parentElement;
    if (parent && parent !== chatHistory) parent.remove();
    else t.remove();
  });
  console.log('[Dashboard] Removed dashboard DOM nodes from chat-history');
}

async function refreshDashboard() {
  if (!document.body.classList.contains('dashboard-mode')) return;
  try {
    const response = await globalThis.sendJsonData("/dashboard_a2ui", {});
    // GUARD: Re-check after async call — user may have left dashboard during fetch
    if (!document.body.classList.contains('dashboard-mode')) {
      console.log('[Dashboard] User left dashboard during refresh, skipping tile injection');
      return;
    }
    if (response && response.messages && response.messages.length > 0) {
      const chatHistory = document.getElementById("chat-history");
      const { renderA2UITile } = await import("./components/chat/a2ui-tile/a2ui-tile-renderer.js");
      if (chatHistory && renderA2UITile) {
        const tile = renderA2UITile(response);
        if (tile) {
          tile.classList.add('a2ui-tile--inline');
          const group = document.createElement('div');
          group.className = 'message-group dashboard-visible';
          group.appendChild(tile);
          chatHistory.appendChild(group);
          enforceSingleDashboardPane();
        }
      }
    }
  } catch (e) {
    console.warn("[Dashboard] Auto-refresh failed:", e);
  }
}
globalThis.refreshDashboard = refreshDashboard;

function startDashboardRefresh() {
  stopDashboardRefresh();
  _dashboardRefreshInterval = setInterval(refreshDashboard, 5 * 60 * 1000); // 5 minutes
}

function stopDashboardRefresh() {
  if (_dashboardRefreshInterval) {
    clearInterval(_dashboardRefreshInterval);
    _dashboardRefreshInterval = null;
  }
}

// Dashboard single-pane observer: only show the LAST A2UI tile
let _dashboardObserver = null;

function enforceSingleDashboardPane() {
  if (!document.body.classList.contains('dashboard-mode')) return;
  const chatHistory = document.getElementById('chat-history');
  if (!chatHistory) return;

  // Find all message-groups and containers that have an A2UI tile
  const allGroups = chatHistory.querySelectorAll('.message-group');
  const allContainers = chatHistory.querySelectorAll('.message-container');

  // Clear all previous visibility markers
  allGroups.forEach(g => g.classList.remove('dashboard-visible'));
  allContainers.forEach(c => c.classList.remove('dashboard-visible'));

  // Find the LAST message-group containing an .a2ui-tile
  let lastTileGroup = null;
  allGroups.forEach(g => {
    if (g.querySelector('.a2ui-tile')) lastTileGroup = g;
  });

  if (lastTileGroup) {
    lastTileGroup.classList.add('dashboard-visible');
    // Also mark the container inside this group that has the tile
    const containers = lastTileGroup.querySelectorAll('.message-container');
    containers.forEach(c => {
      if (c.querySelector('.a2ui-tile')) c.classList.add('dashboard-visible');
    });
  }
}

function startDashboardObserver() {
  stopDashboardObserver(); // cleanup any existing
  const chatHistory = document.getElementById('chat-history');
  if (!chatHistory) return;

  // Run once immediately
  enforceSingleDashboardPane();

  // Watch for DOM changes (new messages arriving)
  _dashboardObserver = new MutationObserver(() => {
    enforceSingleDashboardPane();
  });
  _dashboardObserver.observe(chatHistory, { childList: true, subtree: true });
}

function stopDashboardObserver() {
  if (_dashboardObserver) {
    _dashboardObserver.disconnect();
    _dashboardObserver = null;
  }
  // Clean up visibility classes
  document.querySelectorAll('.dashboard-visible').forEach(el => {
    el.classList.remove('dashboard-visible');
  });
}



export const getContext = function () {
  return context;
};
globalThis.getContext = getContext;
globalThis.setContext = setContext;

export const getChatBasedId = function (id) {
  return context + "-" + globalThis.resetCounter + "-" + id;
};

function addClassToElement(element, className) {
  element.classList.add(className);
}

function removeClassFromElement(element, className) {
  element.classList.remove(className);
}

export function justToast(text, type = "info", timeout = 5000, group = "") {
  notificationStore.addFrontendToastOnly(type, text, "", timeout / 1000, group);
}
globalThis.justToast = justToast;

export function toast(text, type = "info", timeout = 5000) {
  // Convert timeout from milliseconds to seconds for new notification system
  const display_time = Math.max(timeout / 1000, 1); // Minimum 1 second

  // Use new frontend notification system based on type
  switch (type.toLowerCase()) {
    case "error":
      return notificationStore.frontendError(text, "Error", display_time);
    case "success":
      return notificationStore.frontendInfo(text, "Success", display_time);
    case "warning":
      return notificationStore.frontendWarning(text, "Warning", display_time);
    case "info":
    default:
      return notificationStore.frontendInfo(text, "Info", display_time);
  }
}
globalThis.toast = toast;

// OLD: hideToast function removed - now using new notification system

function scrollChanged(isAtBottom) {
  // Reflect scroll state into preferences store; UI is bound via x-model
  // preferencesStore.autoScroll = isAtBottom; 
}

let updateAfterScrollTimeout = null;
export function updateAfterScroll() {
  if (updateAfterScrollTimeout) return;

  updateAfterScrollTimeout = setTimeout(() => {
    const tolerancePx = 10;
    const chatHistory = document.getElementById("chat-history");
    if (!chatHistory) {
      updateAfterScrollTimeout = null;
      return;
    }

    const isAtBottom =
      chatHistory.scrollHeight - chatHistory.scrollTop <=
      chatHistory.clientHeight + tolerancePx;

    scrollChanged(isAtBottom);
    updateAfterScrollTimeout = null;
  }, 100); // Debounce scroll updates to 100ms
}
globalThis.updateAfterScroll = updateAfterScroll;

// setInterval(poll, 250);

async function startPolling() {
  // Polling intervals - balanced for responsiveness vs resource usage
  const shortInterval = 250;   // Fast polling when activity detected (4/sec)
  const longInterval = 2000;   // Slow polling when idle (0.5/sec)
  const shortIntervalPeriod = 20; // Stay in fast mode for ~5 seconds after activity
  let shortIntervalCount = 0;

  async function _doPoll() {
    let nextInterval = longInterval;

    try {
      const result = await poll();
      if (result) shortIntervalCount = shortIntervalPeriod; // Reset the counter when activity detected
      if (shortIntervalCount > 0) shortIntervalCount--; // Decrease the counter on each call
      nextInterval = shortIntervalCount > 0 ? shortInterval : longInterval;
    } catch (error) {
      console.error("Error:", error);
    }

    // Call the function again after the selected interval
    setTimeout(_doPoll.bind(this), nextInterval);
  }

  _doPoll();
}

// DOMContentLoaded listener moved inside initUI()

/*
 * Andy Chat UI
 *
 * Unified sidebar layout:
 * - Both Chats and Tasks lists are always visible in a vertical layout
 * - Both lists are sorted by creation time (newest first)
 * - Tasks use the same context system as chats for communication with the backend
 */

// Open the scheduler detail view for a specific task
function openTaskDetail(taskId) {
  // Wait for Alpine.js to be fully loaded
  if (globalThis.Alpine) {
    // Get the settings modal button and click it to ensure all init logic happens
    const settingsButton = document.getElementById("settings");
    if (settingsButton) {
      // Programmatically click the settings button
      settingsButton.click();

      // Now get a reference to the modal element
      const modalEl = document.getElementById("settingsModal");
      if (!modalEl) {
        console.error("Settings modal element not found after clicking button");
        return;
      }

      // Get the Alpine.js data for the modal
      const modalData = globalThis.Alpine ? Alpine.$data(modalEl) : null;

      // Use a timeout to ensure the modal is fully rendered
      setTimeout(() => {
        // Switch to the scheduler tab first
        modalData.switchTab("scheduler");

        // Use another timeout to ensure the scheduler component is initialized
        setTimeout(() => {
          // Get the scheduler component
          const schedulerComponent = document.querySelector(
            '[x-data="schedulerSettings"]'
          );
          if (!schedulerComponent) {
            console.error("Scheduler component not found");
            return;
          }

          // Get the Alpine.js data for the scheduler component
          const schedulerData = globalThis.Alpine
            ? Alpine.$data(schedulerComponent)
            : null;

          // Show the task detail view for the specific task
          schedulerData.showTaskDetail(taskId);

          console.log("Task detail view opened for task:", taskId);
        }, 50); // Give time for the scheduler tab to initialize
      }, 25); // Give time for the modal to render
    } else {
      console.error("Settings button not found");
    }
  } else {
    console.error("Alpine.js not loaded");
  }
}

// Make the function available globally
globalThis.openTaskDetail = openTaskDetail;

// Start UI Initialization
globalThis.fetchApi = api.fetchApi; // TODO - backward compatibility for non-modular scripts, remove once refactored to alpine

if (globalThis.__index_loaded) {
  console.warn("[UI] index.js code already executed! Skipping duplicate side-effects (polling, events).");
} else {
  globalThis.__index_loaded = true;
  settingsStore.init();
  initUI();
}
