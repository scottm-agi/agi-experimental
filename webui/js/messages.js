/**
 * Message Core Module
 * Entry point for the chat message rendering system.
 * Orchestrates message creation, handler dispatch, and DOM insertion.
 *
 * Delegates to extracted modules:
 *   - message-helpers.js    (regex, escaping, formatting, KVP, Scroller)
 *   - message-renderers.js  (all drawMessage* handler functions)
 */

import { store as imageViewerStore } from "../components/modals/image-viewer/image-viewer-store.js";
import { renderA2UITile } from "../components/chat/a2ui-tile/a2ui-tile-renderer.js";
import { marked } from "../vendor/marked/marked.esm.js";
import { store as _messageResizeStore } from "../components/messages/resize/message-resize-store.js"; // keep here, required in html
import { store as attachmentsStore } from "../components/chat/attachments/attachmentsStore.js";
import { addActionButtonsToElement } from "../components/messages/action-buttons/simple-action-buttons.js";
import { insertContentToChatHistory } from "./dom-utils.js";

// Sub-module imports
import {
    getApiBase,
    Scroller,
    standardSkipKeys,
    isMessageEmpty,
    convertIcons,
    convertImageTags,
    convertImgFilePaths,
    convertPathsToLinks,
    convertHTML,
    escapeHTML,
    maskSecretsInElement,
    addBlankTargetsToLinks,
    drawKvpsIncremental,
    adjustMarkdownRender,
    imageTagRegex,
    iconRegex,
    pathRegex,
    combinedPathTagRegex,
} from './message-helpers.js';

import {
    drawMessageDefault,
    drawMessageAgent,
    drawMessageResponse,
    drawMessageTool,
    drawMessageCodeExe,
    drawMessageBrowser,
    drawMessageUser,
    drawMessageDelegation,
    drawMessageAgentPlain,
    drawMessageInfo,
    drawMessageUtil,
    drawMessageWarning,
    drawMessageError,
    setDrawMessageFn,
} from './message-renderers.js';

// Re-export public API from sub-modules for backward compatibility
export {
    standardSkipKeys,
    isMessageEmpty,
    convertIcons,
    addBlankTargetsToLinks,
    drawMessageDefault,
    drawMessageAgent,
    drawMessageResponse,
    drawMessageTool,
    drawMessageCodeExe,
    drawMessageBrowser,
    drawMessageUser,
    drawMessageDelegation,
    drawMessageAgentPlain,
    drawMessageInfo,
    drawMessageUtil,
    drawMessageWarning,
    drawMessageError,
};

// ─── Chat History Setup ────────────────────────────────────────────

const chatHistory = document.getElementById("chat-history");

// Global Image Click Handler (Delegation)
chatHistory.addEventListener("click", (e) => {
  const target = e.target;
  if (target.tagName === "IMG" &&
    !target.classList.contains("agent-icon") &&
    !target.classList.contains("file-icon") &&
    !target.classList.contains("attachment-preview") &&
    !target.closest(".modal")) {
    imageViewerStore.open(target.src, { name: target.alt || "Image" });
  }
});

let messageGroup = null;

/**
 * Resets the module-level messageGroup state.
 * Should be called when the chat history is cleared (e.g., when switching contexts).
 */
export function resetMessageState() {
  console.log("[MESSAGES] Resetting message state (clearing messageGroup)");
  messageGroup = null;
}

// ─── Marked Configuration ──────────────────────────────────────────

// Configure marked with a high-performance renderer
marked.use({
  breaks: true,
  renderer: {
    link(href_or_token, title, text) {
      // Handle both old (href, title, text) and new (token) marked APIs
      let href, t, txt;
      if (typeof href_or_token === 'object' && href_or_token !== null) {
        href = href_or_token.href;
        t = href_or_token.title;
        txt = href_or_token.text;
      } else {
        href = href_or_token;
        t = title;
        txt = text;
      }

      href = href || "";
      if (href.startsWith("#") || href.trim().toLowerCase().startsWith("javascript")) {
        return `<a href="${href}" title="${t || ''}">${txt}</a>`;
      }
      return `<a href="${href}" title="${t || ''}" target="_blank" rel="noopener noreferrer">${txt}</a>`;
    }
  }
});

// ─── Core: setMessage ──────────────────────────────────────────────

export function setMessage(id, type, heading, content, temp, icon = null, kvps = null, timestamp = null, fragment = null, isSummary = false, verbose = false, sequence_id = 0, hash = "", existingElement = null) {
  const chatHistory = document.getElementById("chat-history");
  if (!chatHistory || !id) return;

  // RECONCILIATION: Check for existing message by ID first
  let messageContainer = existingElement || document.getElementById(`message-${id}`);

  // FALLBACK 1: Use global messageMap for hash-based lookup
  if (!messageContainer && globalThis.messageMap && hash) {
    messageContainer = globalThis.messageMap.get(hash);
    if (messageContainer) {
      messageContainer.id = `message-${id}`;
    }
  }

  // RECONCILE: Match optimistic user message by content
  if (chatHistory) {
    const userContainers = chatHistory.querySelectorAll(`.message-container.user-container`);
    for (const container of userContainers) {
      if (container.id.startsWith("message-temp-")) {
        const storedContent = container.dataset.msgContent || "";
        if (storedContent.trim() === content.trim()) {
          messageContainer = container;
          messageContainer.id = `message-${id}`;
          messageContainer.dataset.msgTemp = "false";
          console.log(`[RECONCILE] Matched optimistic user message by content: ${id} in active chat`);
          break;
        }
      }
    }
  }

  // Skip update if hash matches (performance check)
  if (messageContainer && hash && messageContainer.dataset.msgHash === hash) {
    return messageContainer;
  }

  let isNewMessage = !messageContainer;
  const oldSeq = messageContainer ? parseInt(messageContainer.dataset.msgSequence) : NaN;
  const oldHash = messageContainer ? messageContainer.dataset.msgHash : null;

  // EARLY EXIT: Prevent empty chat bubbles from being created (#815)
  if (isNewMessage && isMessageEmpty(type, heading, content, kvps)) {
    return null;
  }

  if (messageContainer) {
    // Don't clear innerHTML - we'll do incremental updates
  } else {
    isNewMessage = true;
    const sender = type === "user" ? "user" : "ai";
    messageContainer = document.createElement("div");
    messageContainer.id = `message-${id}`;
    messageContainer.classList.add("message-container", `${sender}-container`);
  }

  // Handle verbose logs for background update filtering
  if (verbose) {
    messageContainer.classList.add("verbose-log");
  } else {
    messageContainer.classList.remove("verbose-log");
  }
  messageContainer.style.display = "";

  // Store metadata
  const msgNo = id;
  const msgSequence = sequence_id || kvps?.sequence_id || msgNo || 0;
  const msgHash = hash || kvps?.hash || "";

  messageContainer.dataset.msgNo = msgNo;
  messageContainer.dataset.msgIndex = msgNo;
  messageContainer.dataset.msgType = type;
  messageContainer.dataset.msgHeading = heading || "";
  messageContainer.dataset.msgTemp = temp ? "true" : "false";
  messageContainer.dataset.msgTimestamp = timestamp || "";
  messageContainer.dataset.msgIcon = icon || "";
  messageContainer.dataset.msgContent = content || "";
  messageContainer.dataset.msgKvps = kvps ? JSON.stringify(kvps) : "";
  messageContainer.dataset.msgVerbose = verbose ? "true" : "false";
  messageContainer.dataset.msgSequence = msgSequence;
  messageContainer.dataset.msgHash = msgHash;

  // Fix #859: Propagate updated sequence to parent group
  if (!isNewMessage && msgSequence > 0) {
    const parentGroup = messageContainer.closest('.message-group');
    if (parentGroup && parseInt(parentGroup.dataset.msgSequence) !== msgSequence) {
      parentGroup.dataset.msgSequence = msgSequence;
      parentGroup.dataset.msgIndex = msgNo;
    }
  }

  // Populate global messageMap
  if (globalThis.messageMap && messageContainer.dataset.msgHash) {
    if (oldHash && oldHash !== messageContainer.dataset.msgHash) {
      globalThis.messageMap.delete(oldHash);
    }
    globalThis.messageMap.set(messageContainer.dataset.msgHash, messageContainer);
  }

  const handler = getHandler(type);
  handler(messageContainer, id, type, heading, content, temp, icon, kvps, timestamp, isSummary);

  // If this is a new message, handle DOM insertion
  if (!document.getElementById(`message-${id}`)) {
    const groupTypeMap = {
      user: "right",
      info: "mid",
      warning: "mid",
      error: "mid",
      rate_limit: "mid",
      util: "mid",
      hint: "mid",
    };
    const groupStart = {
      agent: true,
      user: true,
    };

    const groupType = groupTypeMap[type] || "left";
    const groupElementId = `message-group-${id}`;

    let currentGroupInDom = messageGroup && document.getElementById(messageGroup.id);
    let currentGroupInFragment = fragment && fragment.lastElementChild && fragment.lastElementChild.classList.contains('message-group') ? fragment.lastElementChild : null;

    if (
      (!currentGroupInDom && !currentGroupInFragment) ||
      groupStart[type] ||
      (currentGroupInFragment && groupType != currentGroupInFragment.getAttribute("data-group-type")) ||
      (!currentGroupInFragment && currentGroupInDom && groupType != currentGroupInDom.getAttribute("data-group-type"))
    ) {
      messageGroup = document.createElement("div");
      messageGroup.id = groupElementId;
      messageGroup.classList.add(`message-group`, `message-group-${groupType}`);
      messageGroup.setAttribute("data-group-type", groupType);

      if (fragment) {
        fragment.appendChild(messageGroup);
      } else {
        messageGroup.dataset.msgSequence = msgSequence;
        messageGroup.dataset.msgIndex = msgNo;

        if (!msgHash) {
          const chatHistoryEl = document.getElementById('chat-history');
          if (chatHistoryEl) chatHistoryEl.appendChild(messageGroup);
        } else {
          insertContentToChatHistory(messageGroup, msgSequence);
        }
      }
    } else if (currentGroupInFragment) {
      messageGroup = currentGroupInFragment;
    }
  }

  // Append the message container to the group - ONLY for new messages
  if (isNewMessage && messageGroup) {
    const settingsStore = globalThis.Alpine?.store("settings");
    if (settingsStore && settingsStore.hide_sub_agent_tiles && (type === "agent" || type === "agent-delegation") && !isSummary) {
      messageContainer.style.display = "none";
      messageContainer.classList.add("hidden-feature-flag");
    }
    messageGroup.appendChild(messageContainer);
  }

  return messageContainer;
}

// ─── Core: getHandler ──────────────────────────────────────────────

export function getHandler(type) {
  switch (type) {
    case "user":
      return drawMessageUser;
    case "agent":
      return drawMessageAgent;
    case "response":
      return drawMessageResponse;
    case "tool":
      return drawMessageTool;
    case "code_exe":
      return drawMessageCodeExe;
    case "browser":
      return drawMessageBrowser;
    case "warning":
      return drawMessageWarning;
    case "rate_limit":
      return drawMessageWarning;
    case "error":
      return drawMessageError;
    case "info":
      return drawMessageInfo;
    case "util":
      return drawMessageUtil;
    case "hint":
      return drawMessageInfo;
    default:
      return drawMessageDefault;
  }
}

// ─── Core: _drawMessage ────────────────────────────────────────────

export function _drawMessage(
  messageContainer,
  heading,
  content,
  temp,
  followUp,
  mainClass = "",
  icon = null,
  kvps = null,
  messageClasses = [],
  contentClasses = [],
  latex = false,
  markdown = false,
  resizeBtns = true,
  timestamp = null
) {
  let messageDiv = messageContainer.querySelector(".message");
  if (!messageDiv) {
    messageDiv = document.createElement("div");
    messageDiv.classList.add("message");
    messageContainer.appendChild(messageDiv);
  }

  messageDiv.className = `message ${mainClass} ${messageClasses.join(" ")}`;

  const preferencesStore = globalThis.Alpine?.store("preferences");
  const messageResizeStore = globalThis.Alpine?.store("messageResize");
  const isIntermediate =
    mainClass === "message-agent" ||
    mainClass === "message-tool" ||
    mainClass === "message-code-exe" ||
    mainClass === "message-browser" ||
    mainClass === "message-util" ||
    mainClass === "message-info" ||
    mainClass === "message-warning" ||
    mainClass === "message-agent-delegation" ||
    mainClass === "message-default";

  // Handle heading
  if (heading) {
    let headingElement = messageDiv.querySelector(".msg-heading");
    if (!headingElement) {
      headingElement = document.createElement("div");
      headingElement.classList.add("msg-heading");
      messageDiv.insertBefore(headingElement, messageDiv.firstChild);
    }

    let headingH4 = headingElement.querySelector("h4");
    if (!headingH4) {
      headingH4 = document.createElement("h4");
      headingElement.appendChild(headingH4);
    }
    let iconHtml = "";
    if (icon && typeof icon === 'string' && !heading.includes(`icon://${icon}`)) {
      iconHtml = convertIcons(`icon://${icon}`);
    }
    headingH4.innerHTML = iconHtml + convertIcons(escapeHTML(heading));

    // Add collapsed thought 1-liner
    let thoughtText = "";
    if (kvps) {
      const thoughts = kvps["thoughts"] || kvps["reasoning"];
      if (thoughts) {
        thoughtText = Array.isArray(thoughts) ? thoughts.join(" ") : thoughts;
        thoughtText = thoughtText.replace(/\s+/g, " ").trim();
        if (thoughtText.length > 100) thoughtText = thoughtText.substring(0, 100) + "...";
      }
    }

    let thoughtSpan = headingH4.querySelector(".collapsed-thought");
    if (thoughtText) {
      if (!thoughtSpan) {
        thoughtSpan = document.createElement("span");
        thoughtSpan.classList.add("collapsed-thought");
        headingH4.appendChild(thoughtSpan);
      }
      thoughtSpan.textContent = thoughtText;
    } else if (thoughtSpan) {
      thoughtSpan.remove();
    }

    if (resizeBtns) {
      let minMaxBtn = headingElement.querySelector(".msg-min-max-btns");
      if (!minMaxBtn) {
        minMaxBtn = document.createElement("div");
        minMaxBtn.classList.add("msg-min-max-btns");
        minMaxBtn.innerHTML = `
          <a href="#" class="msg-min-max-btn" @click.prevent="$store.messageResize.toggleMessageClass('${mainClass}', $event)"><span class="material-symbols-outlined" x-text="$store.messageResize.getSetting('${mainClass}').collapsed ? 'expand_content' : 'minimize'"></span></a>
        `;
        headingElement.appendChild(minMaxBtn);
      }
    }
  }

  // Standardized Empty Check
  if (isMessageEmpty(messageContainer.dataset.msgType, heading, content, kvps)) {
    messageContainer.style.display = "none";
    messageContainer.classList.add("hidden-empty-message");
    return;
  } else {
    messageContainer.classList.remove("hidden-empty-message");
    if (!messageContainer.classList.contains("hidden-feature-flag")) {
      messageContainer.style.display = "";
    }
  }

  let timestampElement = messageDiv.querySelector(".msg-timestamp");
  if (timestamp) {
    if (!timestampElement) {
      timestampElement = document.createElement("div");
      timestampElement.classList.add("msg-timestamp");
      const headingEl = messageDiv.querySelector(".msg-heading");
      if (headingEl) {
        messageDiv.insertBefore(timestampElement, headingEl.nextSibling);
      } else {
        messageDiv.insertBefore(timestampElement, messageDiv.firstChild);
      }
    }
    const date = new Date(timestamp * 1000);
    timestampElement.textContent = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } else if (timestampElement) {
    timestampElement.remove();
  }

  // Find existing body div or create new one
  let bodyDiv = messageDiv.querySelector(".message-body");
  if (!bodyDiv) {
    bodyDiv = document.createElement("div");
    bodyDiv.classList.add("message-body");
    messageDiv.appendChild(bodyDiv);

    if (
      isIntermediate &&
      preferencesStore &&
      messageResizeStore
    ) {
      if (!preferencesStore.expandTiles) {
        const setting = messageResizeStore.getSetting(mainClass);
        if (!setting.collapsed) {
          setting.collapsed = true;
          messageResizeStore._setSetting(mainClass, setting);
        }
      }
    }
  }

  const scroller = new Scroller(bodyDiv);
  if (bodyDiv.isConnected) scroller.capture();

  // Filter model/provider info from KVPs
  let filteredKvps = kvps;
  if (kvps) {
    const showDebug = preferencesStore?.showDebugInfo;
    const itemsToFilter = ["actual_model", "actual_provider"];
    if (!showDebug) {
      itemsToFilter.push("sequence_id", "hash");
    }

    const hasItemsToFilter = itemsToFilter.some(key => kvps[key] !== undefined);
    if (hasItemsToFilter) {
      filteredKvps = { ...kvps };
      itemsToFilter.forEach(key => delete filteredKvps[key]);
      if (Object.keys(filteredKvps).length === 0) filteredKvps = null;
    }
  }

  // Handle KVPs incrementally
  drawKvpsIncremental(bodyDiv, filteredKvps, false);

  // Handle content
  if (content && content.trim().length > 0) {
    if (markdown) {
      let contentDiv = bodyDiv.querySelector(".msg-content");
      if (!contentDiv) {
        contentDiv = document.createElement("div");
        bodyDiv.appendChild(contentDiv);
      }
      contentDiv.className = `msg-content ${contentClasses.join(" ")}`;

      let spanElement = contentDiv.querySelector("span");
      if (!spanElement) {
        spanElement = document.createElement("span");
        contentDiv.appendChild(spanElement);
      }

      if (spanElement.getAttribute("data-raw-markdown") !== content) {
        let processedContent = content;
        processedContent = convertImageTags(processedContent);
        processedContent = convertImgFilePaths(processedContent);
        processedContent = marked.parse(processedContent);
        processedContent = convertPathsToLinks(processedContent);

        spanElement.innerHTML = processedContent;
        spanElement.setAttribute("data-raw-markdown", content);

        if (latex) {
          spanElement.querySelectorAll("latex").forEach((element) => {
            katex.render(element.innerHTML, element, {
              throwOnError: false,
            });
          });
        }

        addActionButtonsToElement(bodyDiv);
        adjustMarkdownRender(contentDiv);
        maskSecretsInElement(spanElement);
      }

    } else {
      let preElement = bodyDiv.querySelector(".msg-content");
      if (!preElement) {
        preElement = document.createElement("pre");
        preElement.classList.add("msg-content", ...contentClasses);
        preElement.style.whiteSpace = "pre-wrap";
        preElement.style.wordBreak = "break-word";
        bodyDiv.appendChild(preElement);
      } else {
        preElement.className = `msg-content ${contentClasses.join(" ")}`;
      }

      let spanElement = preElement.querySelector("span");
      if (!spanElement) {
        spanElement = document.createElement("span");
        preElement.appendChild(spanElement);
      }

      if (spanElement.getAttribute("data-raw-markdown") !== content) {
        spanElement.innerHTML = convertHTML(content);
        spanElement.setAttribute("data-raw-markdown", content);
        addActionButtonsToElement(bodyDiv);
        maskSecretsInElement(spanElement);
      }
    }
  } else {
    const existingContent = bodyDiv.querySelector(".msg-content");
    if (existingContent) {
      existingContent.remove();
    }
  }

  // Handle model info
  if (kvps && (kvps["actual_model"] || kvps["actual_provider"])) {
    let modelInfoElement = messageDiv.querySelector(".msg-model-info");
    if (!modelInfoElement) {
      modelInfoElement = document.createElement("div");
      modelInfoElement.classList.add("msg-model-info");
      messageDiv.appendChild(modelInfoElement);
    }
    const model = kvps["actual_model"] || "";
    const provider = kvps["actual_provider"] || "";
    modelInfoElement.textContent = `${provider}${provider && model ? "/" : ""}${model}`;
  } else {
    const existingModelInfo = messageDiv.querySelector(".msg-model-info");
    if (existingModelInfo) existingModelInfo.remove();
  }

  scroller.reApplyScroll();

  if (followUp) {
    messageContainer.classList.add("message-followup");
  }

  return messageDiv;
}

// Wire up the renderers with _drawMessage reference
setDrawMessageFn(_drawMessage);
