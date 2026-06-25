// message actions and components
import { store as imageViewerStore } from "../components/modals/image-viewer/image-viewer-store.js";
import { marked } from "../vendor/marked/marked.esm.js";
import { store as _messageResizeStore } from "../components/messages/resize/message-resize-store.js"; // keep here, required in html
import { store as attachmentsStore } from "../components/chat/attachments/attachmentsStore.js";
import { addActionButtonsToElement } from "../components/messages/action-buttons/simple-action-buttons.js";

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

// Persistent regexes and configurations for performance
const imageTagRegex = /<image>(.*?)<\/image>/g;
const iconRegex = /icon:\/\/([a-zA-Z0-9_]+)/g;
const pathPrefix = `(?:^|[> \`'"\\n]|&#39;|&quot;)`;
const folderCharset = `[a-zA-Z0-9_\\/.\\-]`;
const fileCharset = `[a-zA-Z0-9_\\-\\/]`;
const pathSuffix = `(?<!\\.)`;
// pathRegex optimized: removed lookbehind to make it compatible with more environments and potentially faster in OR groups
const pathRegexMain = `\\/${folderCharset}*${fileCharset}`;
const pathRegex = new RegExp(`(${pathPrefix})(${pathRegexMain})`, "g");
const tagRegex = /(<(?:[^<>"']+|"[^"]*"|'[^']*')*>)/g;

// Combined regex for single-pass processing: (Tag) | (Prefix)(Path)
const combinedPathTagRegex = new RegExp(`(${tagRegex.source})|(${pathPrefix})(${pathRegexMain})`, "g");

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

export function setMessage(no, id, type, heading, content, temp, icon = null, kvps = null, timestamp = null, fragment = null, isSummary = false) {
  // Search for the existing message container by global id (GUID)
  // We prefer id for DOM identity to handle optimistic UI reconciliation
  let messageContainer = document.getElementById(`message-${id}`);

  // Fallback for legacy items that might have used 'no' as their DOM id
  if (!messageContainer && no !== id) {
    const legacyContainer = document.getElementById(`message-${no}`);
    if (legacyContainer) {
      messageContainer = legacyContainer;
      messageContainer.id = `message-${id}`; // Upgrade to GUID ID
    }
  }

  let isNewMessage = false;

  if (messageContainer) {
    // Don't clear innerHTML - we'll do incremental updates
  } else {
    // Create a new container if not found
    isNewMessage = true;
    const sender = type === "user" ? "user" : "ai";
    messageContainer = document.createElement("div");
    messageContainer.id = `message-${id}`;
    messageContainer.classList.add("message-container", `${sender}-container`);
  }

  // Store metadata for cache scraping and sequence tracking
  // We strictly use 'no' for ordering logic to prevent GUID-based sorting errors (Issue #238)
  messageContainer.dataset.msgNo = no;
  messageContainer.dataset.msgId = id;
  messageContainer.dataset.msgType = type;
  messageContainer.dataset.msgHeading = heading || "";
  messageContainer.dataset.msgTemp = temp ? "true" : "false";
  messageContainer.dataset.msgTimestamp = timestamp || "";
  messageContainer.dataset.msgIcon = icon || "";
  messageContainer.dataset.msgContent = content || "";
  messageContainer.dataset.msgKvps = kvps ? JSON.stringify(kvps) : "";

  const handler = getHandler(type);
  handler(messageContainer, id, type, heading, content, temp, icon, kvps, timestamp, isSummary);

  // If this is a new message, handle DOM insertion
  if (isNewMessage) {
    // message type visual grouping
    const groupTypeMap = {
      user: "right",
      info: "mid",
      warning: "mid",
      error: "mid",
      rate_limit: "mid",
      util: "mid",
      hint: "mid",
    };

    // Force new group on these types
    const groupStart = {
      agent: true,
    };

    const groupType = groupTypeMap[type] || "left";
    const groupElementId = `message-group-${id}`;

    // Context switch check
    if (messageGroup && !document.getElementById(messageGroup.id))
      messageGroup = null;

    if (
      !messageGroup ||
      groupStart[type] ||
      groupType != messageGroup.getAttribute("data-group-type")
    ) {
      messageGroup = document.createElement("div");
      messageGroup.id = groupElementId;
      messageGroup.classList.add(`message-group`, `message-group-${groupType}`);
      messageGroup.setAttribute("data-group-type", groupType);

      if (fragment) {
        fragment.appendChild(messageGroup);
      } else {
        // Find correct insertion point in chatHistory if not using a fragment
        // We use msgNo (numeric) for absolute ordering (Issue #238)
        const targetNo = parseInt(no);
        const allMessageContainers = Array.from(chatHistory.querySelectorAll('.message-container'));
        const nextContainer = allMessageContainers.find(m => {
          const mNo = parseInt(m.dataset.msgNo);
          return !isNaN(mNo) && !isNaN(targetNo) && mNo > targetNo;
        });

        if (nextContainer) {
          const nextGroup = nextContainer.closest('.message-group');
          chatHistory.insertBefore(messageGroup, nextGroup);
        } else {
          chatHistory.appendChild(messageGroup);
        }
      }
    }
    messageGroup.appendChild(messageContainer);
  }

  return messageContainer;
}

// Legacy copy button functions removed - now using action buttons component

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

// draw a message with a specific type
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
  // Find existing message div or create new one
  let messageDiv = messageContainer.querySelector(".message");
  if (!messageDiv) {
    messageDiv = document.createElement("div");
    messageDiv.classList.add("message");
    messageContainer.appendChild(messageDiv);
  }

  // Update message classes
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

    // Defensive JSON parsing for heading (Issue #254)
    let processedHeading = heading;
    if (typeof heading === 'string' && (heading.trim().startsWith('{') || heading.trim().startsWith('['))) {
      try {
        const parsedHeading = JSON.parse(heading);
        if (parsedHeading.headline) processedHeading = parsedHeading.headline;
        else if (parsedHeading.tool_name) processedHeading = `Using tool: ${parsedHeading.tool_name}`;
        else if (parsedHeading.thoughts && Array.isArray(parsedHeading.thoughts)) processedHeading = parsedHeading.thoughts[0];
      } catch (e) {
        // Not valid JSON after all, or missing expected keys
      }
    }

    let iconHtml = "";
    if (icon && typeof icon === 'string' && !processedHeading.includes(`icon://${icon}`)) {
      iconHtml = convertIcons(`icon://${icon}`);
    }
    headingH4.innerHTML = iconHtml + convertIcons(escapeHTML(processedHeading));

    // Add collapsed thought 1-liner
    let thoughtText = "";
    if (kvps) {
      const thoughts = kvps["thoughts"] || kvps["reasoning"];
      if (thoughts) {
        thoughtText = Array.isArray(thoughts) ? thoughts.join(" ") : thoughts;
        // Basic cleanup and truncation
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
        // Using standard onclick instead of Alpine @click because Alpine doesn't bind to manually injected innerHTML reliably during streaming
        minMaxBtn.innerHTML = `
          <a href="#" class="msg-min-max-btn" onclick="event.preventDefault(); globalThis.messageResizeStore.toggleMessageClass('${mainClass}', event)">
            <span class="material-symbols-outlined">${messageResizeStore.getSetting(mainClass).collapsed ? 'expand_content' : 'minimize'}</span>
          </a>
        `;
        headingElement.appendChild(minMaxBtn);
      } else {
        // Update icon based on current state
        const btn = minMaxBtn.querySelector(".msg-min-max-btn span");
        if (btn) {
          const setting = messageResizeStore.getSetting(mainClass);
          btn.textContent = setting.collapsed ? 'expand_content' : 'minimize';
        }
      }
    }
  }

  // Handle timestamp
  if (timestamp) {
    let timestampElement = messageDiv.querySelector(".msg-timestamp");
    if (!timestampElement) {
      timestampElement = document.createElement("div");
      timestampElement.classList.add("msg-timestamp");
      // Insert after heading or at the top if no heading
      const headingEl = messageDiv.querySelector(".msg-heading");
      if (headingEl) {
        messageDiv.insertBefore(timestampElement, headingEl.nextSibling);
      } else {
        messageDiv.insertBefore(timestampElement, messageDiv.firstChild);
      }
    }
    const date = parseTimestamp(timestamp);
    if (date) {
      timestampElement.textContent = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } else {
      timestampElement.textContent = "";
    }
  } else {
    const existingTimestamp = messageDiv.querySelector(".msg-timestamp");
    if (existingTimestamp) existingTimestamp.remove();
  }

  // Find existing body div or create new one
  let bodyDiv = messageDiv.querySelector(".message-body");
  if (!bodyDiv) {
    bodyDiv = document.createElement("div");
    bodyDiv.classList.add("message-body");
    messageDiv.appendChild(bodyDiv);

    // handle default minimized state
    if (
      isIntermediate &&
      preferencesStore &&
      messageResizeStore
    ) {
      // Ensure the resize store knows it's minimized for this class if preferencesStore.expandTiles is false,
      // and it hasn't been set yet. We rely on global CSS via message-resize-store for actual visibility.
      if (!preferencesStore.expandTiles) {
        const setting = messageResizeStore.getSetting(mainClass);
        if (!setting.collapsed) {
          setting.collapsed = true;
          messageResizeStore._setSetting(mainClass, setting);
        }
      }
    }
  }

  // reapply scroll position or autoscroll - optimized to only reflow if connected
  const scroller = new Scroller(bodyDiv);
  if (bodyDiv.isConnected) scroller.capture();

  // Filter out model/provider info from KVPs as they are handled separately via .msg-model-info
  let filteredKvps = kvps;
  if (kvps && (kvps["actual_model"] || kvps["actual_provider"])) {
    filteredKvps = { ...kvps };
    delete filteredKvps["actual_model"];
    delete filteredKvps["actual_provider"];
    if (Object.keys(filteredKvps).length === 0) filteredKvps = null;
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

      // Optimization: Only parse if content changed
      if (spanElement.getAttribute("data-raw-markdown") !== content) {
        let processedContent = content;
        processedContent = convertImageTags(processedContent);
        processedContent = convertImgFilePaths(processedContent);
        processedContent = marked.parse(processedContent);
        processedContent = convertPathsToLinks(processedContent);

        spanElement.innerHTML = processedContent;
        spanElement.setAttribute("data-raw-markdown", content);

        // KaTeX rendering for markdown
        if (latex) {
          spanElement.querySelectorAll("latex").forEach((element) => {
            katex.render(element.innerHTML, element, {
              throwOnError: false,
            });
          });
        }

        // Ensure action buttons exist
        addActionButtonsToElement(bodyDiv);
        adjustMarkdownRender(contentDiv);
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
        // Update classes
        preElement.className = `msg-content ${contentClasses.join(" ")}`;
      }

      let spanElement = preElement.querySelector("span");
      if (!spanElement) {
        spanElement = document.createElement("span");
        preElement.appendChild(spanElement);
      }

      // Optimization: Only update if content changed
      if (spanElement.getAttribute("data-raw-markdown") !== content) {
        spanElement.innerHTML = convertHTML(content);
        spanElement.setAttribute("data-raw-markdown", content);

        // Ensure action buttons exist
        addActionButtonsToElement(bodyDiv);
      }

    }
  } else {
    // Remove content if it exists but content is empty
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

  // reapply scroll position or autoscroll
  scroller.reApplyScroll();

  if (followUp) {
    messageContainer.classList.add("message-followup");
  }

  return messageDiv;
}

export function addBlankTargetsToLinks(str) {
  // Optimized: use regex instead of DOMParser to find <a> tags and add attributes
  // only if they don't already have them. This is a fallback for non-markdown content.
  return str.replace(/<a\s+(?:[^>]*?\s+)?href=(["'])(.*?)\1([^>]*?)>/gi, (match, quote, href, suffix) => {
    if (href.startsWith("#") || href.trim().toLowerCase().startsWith("javascript")) {
      return match;
    }

    let result = match;
    if (!/target=/i.test(result)) {
      result = result.replace(/>$/, ' target="_blank">');
    }
    if (!/rel=/i.test(result)) {
      result = result.replace(/>$/, ' rel="noopener noreferrer">');
    }
    return result;
  });
}

export function drawMessageDefault(
  messageContainer,
  id,
  type,
  heading,
  content,
  temp,
  icon = null,
  kvps = null,
  timestamp = null
) {
  _drawMessage(
    messageContainer,
    heading,
    content,
    temp,
    false,
    "message-default",
    icon,
    kvps,
    ["message-ai"],
    ["msg-json"],
    false,
    false,
    true,
    timestamp
  );
}

export function drawMessageAgent(
  messageContainer,
  id,
  type,
  heading,
  content,
  temp,
  icon = null,
  kvps = null,
  timestamp = null
) {
  let kvpsFlat = null;
  if (kvps) {
    kvpsFlat = { ...kvps, ...(kvps["tool_args"] || {}) };
    delete kvpsFlat["tool_args"];
  }

  _drawMessage(
    messageContainer,
    heading,
    content,
    temp,
    false,
    "message-agent",
    icon,
    kvpsFlat,
    ["message-ai"],
    ["msg-json"],
    false,
    false,
    true,
    timestamp
  );
}

export function drawMessageResponse(
  messageContainer,
  id,
  type,
  heading,
  content,
  temp,
  icon = null,
  kvps = null,
  timestamp = null
) {
  _drawMessage(
    messageContainer,
    heading,
    content,
    temp,
    true,
    "message-agent-response",
    icon,
    kvps,
    ["message-ai"],
    [],
    true,
    true,
    true,
    timestamp
  );
}

export function drawMessageDelegation(
  messageContainer,
  id,
  type,
  heading,
  content,
  temp,
  icon = null,
  kvps = null,
  timestamp = null
) {
  _drawMessage(
    messageContainer,
    heading,
    content,
    temp,
    true,
    "message-agent-delegation",
    icon,
    kvps,
    ["message-ai", "message-agent"],
    [],
    true,
    false,
    true,
    timestamp
  );
}

export function drawMessageUser(
  messageContainer,
  id,
  type,
  heading,
  content,
  temp,
  icon = null,
  kvps = null,
  timestamp = null,
  latex = false
) {
  // Find existing message div or create new one
  let messageDiv = messageContainer.querySelector(".message");
  if (!messageDiv) {
    messageDiv = document.createElement("div");
    messageDiv.classList.add("message", "message-user");
    messageContainer.appendChild(messageDiv);
  } else {
    // Ensure it has the correct classes if it already exists
    messageDiv.className = "message message-user";
  }

  // Handle heading
  let headingElement = messageDiv.querySelector(".msg-heading");
  if (!headingElement) {
    headingElement = document.createElement("h4");
    headingElement.classList.add("msg-heading");
    messageDiv.insertBefore(headingElement, messageDiv.firstChild);
  }

  let userHeadingHtml = `${heading} <span class='icon material-symbols-outlined'>person</span>`;
  if (timestamp) {
    const date = parseTimestamp(timestamp);
    if (date) {
      const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      userHeadingHtml = `<span class="msg-timestamp">${timeStr}</span> ` + userHeadingHtml;
    }
  }
  headingElement.innerHTML = userHeadingHtml;

  // Handle content
  let textDiv = messageDiv.querySelector(".message-text");
  if (content && content.trim().length > 0) {
    if (!textDiv) {
      textDiv = document.createElement("div");
      textDiv.classList.add("message-text");
      messageDiv.appendChild(textDiv);
    }
    let spanElement = textDiv.querySelector("pre");
    if (!spanElement) {
      spanElement = document.createElement("pre");
      textDiv.appendChild(spanElement);
    }
    spanElement.innerHTML = escapeHTML(content);
    addActionButtonsToElement(textDiv);
  } else {
    if (textDiv) textDiv.remove();
  }

  // Handle attachments
  let attachmentsContainer = messageDiv.querySelector(".attachments-container");
  if (kvps && kvps.attachments && kvps.attachments.length > 0) {
    if (!attachmentsContainer) {
      attachmentsContainer = document.createElement("div");
      attachmentsContainer.classList.add("attachments-container");
      messageDiv.appendChild(attachmentsContainer);
    }
    // Important: Clear existing attachments to re-render, preventing duplicates on update
    attachmentsContainer.innerHTML = "";

    kvps.attachments.forEach((attachment) => {
      const attachmentDiv = document.createElement("div");
      attachmentDiv.classList.add("attachment-item");

      const displayInfo = attachmentsStore.getAttachmentDisplayInfo(attachment);

      if (displayInfo.isImage) {
        attachmentDiv.classList.add("image-type");

        const img = document.createElement("img");
        img.src = displayInfo.previewUrl;
        img.alt = displayInfo.filename;
        img.classList.add("attachment-preview");
        img.style.cursor = "pointer";

        attachmentDiv.appendChild(img);
      } else {
        // Render as file tile with title and icon
        attachmentDiv.classList.add("file-type");

        // File icon
        if (
          displayInfo.previewUrl &&
          displayInfo.previewUrl !== displayInfo.filename
        ) {
          const iconImg = document.createElement("img");
          iconImg.src = displayInfo.previewUrl;
          iconImg.alt = `${displayInfo.extension} file`;
          iconImg.classList.add("file-icon");
          attachmentDiv.appendChild(iconImg);
        }

        // File title
        const fileTitle = document.createElement("div");
        fileTitle.classList.add("file-title");
        fileTitle.textContent = displayInfo.filename;

        attachmentDiv.appendChild(fileTitle);
      }

      attachmentDiv.addEventListener("click", displayInfo.clickHandler);

      attachmentsContainer.appendChild(attachmentDiv);
    });
  } else {
    if (attachmentsContainer) attachmentsContainer.remove();
  }
  // The messageDiv is already appended or updated, no need to append again
}

export function drawMessageTool(
  messageContainer,
  id,
  type,
  heading,
  content,
  temp,
  icon = null,
  kvps = null,
  timestamp = null,
  isSummary = false
) {
  _drawMessage(
    messageContainer,
    heading,
    content,
    temp,
    true,
    "message-tool",
    icon,
    kvps,
    ["message-ai"],
    ["msg-output", "msg-json", ...(isSummary ? ["msg-summary"] : [])],
    false,
    false,
    true,
    timestamp
  );
}

export function drawMessageCodeExe(
  messageContainer,
  id,
  type,
  heading,
  content,
  temp,
  icon = null,
  kvps = null,
  timestamp = null
) {
  _drawMessage(
    messageContainer,
    heading,
    content,
    temp,
    true,
    "message-code-exe",
    icon,
    kvps,
    ["message-ai"],
    [],
    false,
    false,
    true,
    timestamp
  );
}

export function drawMessageBrowser(
  messageContainer,
  id,
  type,
  heading,
  content,
  temp,
  icon = null,
  kvps = null,
  timestamp = null
) {
  _drawMessage(
    messageContainer,
    heading,
    content,
    temp,
    true,
    "message-browser",
    icon,
    kvps,
    ["message-ai"],
    ["msg-json"],
    false,
    false,
    true,
    timestamp
  );
}

export function drawMessageAgentPlain(
  mainClass,
  messageContainer,
  id,
  type,
  heading,
  content,
  temp,
  icon = null,
  kvps = null,
  timestamp = null
) {
  _drawMessage(
    messageContainer,
    heading,
    content,
    temp,
    false,
    mainClass,
    icon,
    kvps,
    [],
    [],
    false,
    false,
    true,
    timestamp
  );
  messageContainer.classList.add("center-container");
}

export function drawMessageInfo(
  messageContainer,
  id,
  type,
  heading,
  content,
  temp,
  icon = null,
  kvps = null,
  timestamp = null
) {
  return drawMessageAgentPlain(
    "message-info",
    messageContainer,
    id,
    type,
    heading,
    content,
    temp,
    icon,
    kvps,
    timestamp
  );
}

export function drawMessageUtil(
  messageContainer,
  id,
  type,
  heading,
  content,
  temp,
  icon = null,
  kvps = null,
  timestamp = null
) {
  _drawMessage(
    messageContainer,
    heading,
    content,
    temp,
    false,
    "message-util",
    icon,
    kvps,
    [],
    ["msg-json"],
    false,
    false,
    true,
    timestamp
  );
  messageContainer.classList.add("center-container");
}

export function drawMessageWarning(
  messageContainer,
  id,
  type,
  heading,
  content,
  temp,
  icon = null,
  kvps = null,
  timestamp = null
) {
  return drawMessageAgentPlain(
    "message-warning",
    messageContainer,
    id,
    type,
    heading,
    content,
    temp,
    icon,
    kvps,
    timestamp
  );
}

export function drawMessageError(
  messageContainer,
  id,
  type,
  heading,
  content,
  temp,
  icon = null,
  kvps = null,
  timestamp = null
) {
  return drawMessageAgentPlain(
    "message-error",
    messageContainer,
    id,
    type,
    heading,
    content,
    temp,
    icon,
    kvps,
    timestamp
  );
}

function drawKvps(container, kvps, latex) {
  if (kvps) {
    const table = document.createElement("table");
    table.classList.add("msg-kvps");
    for (let [key, value] of Object.entries(kvps)) {
      const row = table.insertRow();
      row.classList.add("kvps-row");
      if (key === "thoughts" || key === "reasoning")
        // TODO: find a better way to determine special class assignment
        row.classList.add("msg-thoughts");

      const th = row.insertCell();
      th.textContent = convertToTitleCase(key);
      th.classList.add("kvps-key");

      const td = row.insertCell();
      const tdiv = document.createElement("div");
      tdiv.classList.add("kvps-val");
      td.appendChild(tdiv);

      if (Array.isArray(value)) {
        for (const item of value) {
          addValue(item);
        }
      } else {
        addValue(value);
      }

      addActionButtonsToElement(tdiv);

      // autoscroll the KVP value if needed
      // if (getAutoScroll()) #TODO needs a better redraw system
      setTimeout(() => {
        tdiv.scrollTop = tdiv.scrollHeight;
      }, 0);

      function addValue(value) {
        if (typeof value === "object") value = JSON.stringify(value, null, 2);

        if (typeof value === "string" && value.startsWith("img://")) {
          const imgElement = document.createElement("img");
          imgElement.classList.add("kvps-img");
          imgElement.src = value.replace("img://", "/image_get?path=");
          imgElement.alt = "Image Attachment";
          tdiv.appendChild(imgElement);

          // Add click handler and cursor change
          imgElement.style.cursor = "pointer";
          imgElement.addEventListener("click", () => {
            openImageModal(imgElement.src, 1000);
          });
        } else {
          const pre = document.createElement("pre");
          const span = document.createElement("span");
          span.innerHTML = convertHTML(value);
          pre.appendChild(span);
          tdiv.appendChild(pre);

          // KaTeX rendering for markdown
          if (latex) {
            span.querySelectorAll("latex").forEach((element) => {
              katex.render(element.innerHTML, element, {
                throwOnError: false,
              });
            });
          }
        }
      }
    }
    container.appendChild(table);
  }
}

function drawKvpsIncremental(container, kvps, latex) {
  if (kvps) {
    // Find existing table or create new one
    let table = container.querySelector(".msg-kvps");
    if (!table) {
      table = document.createElement("table");
      table.classList.add("msg-kvps");
      container.appendChild(table);
    }

    // Get all current rows for comparison
    let existingRows = table.querySelectorAll(".kvps-row");
    const kvpEntries = Object.entries(kvps);

    // Create a single scroller for the container - optimized to avoid reflow if possible
    const scroller = new Scroller(container);
    if (container.isConnected) scroller.capture();

    // Update or create rows as needed
    kvpEntries.forEach(([key, value], index) => {
      let row = existingRows[index];

      if (!row) {
        // Create new row if it doesn't exist
        row = table.insertRow();
        row.classList.add("kvps-row");
      }

      // Update row classes
      const targetClassName = "kvps-row" + (key === "thoughts" || key === "reasoning" ? " msg-thoughts" : "");
      if (row.className !== targetClassName) {
        row.className = targetClassName;
      }

      // Handle key cell
      let th = row.querySelector(".kvps-key");
      if (!th) {
        th = row.insertCell(0);
        th.classList.add("kvps-key");
      }
      const titleKey = convertToTitleCase(key);
      if (th.textContent !== titleKey) {
        th.textContent = titleKey;
      }

      // Handle value cell
      let td = row.cells[1];
      if (!td) {
        td = row.insertCell(1);
      }

      let tdiv = td.querySelector(".kvps-val");
      if (!tdiv) {
        tdiv = document.createElement("div");
        tdiv.classList.add("kvps-val");
        td.appendChild(tdiv);
      }

      // Optimization: Only update if value changed
      const valueStr = typeof value === "object" ? JSON.stringify(value) : String(value);
      if (tdiv.getAttribute("data-raw-value") === valueStr) {
        return;
      }
      tdiv.setAttribute("data-raw-value", valueStr);

      // Clear and rebuild content
      tdiv.innerHTML = "";

      if (Array.isArray(value)) {
        for (const item of value) {
          addValue(item, tdiv);
        }
      } else {
        addValue(value, tdiv);
      }
    });

    // reapply scroll position or autoscroll
    scroller.reApplyScroll();

    // Remove extra rows if we have fewer kvps now
    while (existingRows.length > kvpEntries.length) {
      const lastRow = existingRows[existingRows.length - 1];
      lastRow.remove();
      existingRows = table.querySelectorAll(".kvps-row");
    }

    function addValue(value, tdiv) {
      if (typeof value === "object") value = JSON.stringify(value, null, 2);

      if (typeof value === "string" && value.startsWith("img://")) {
        const imgElement = document.createElement("img");
        imgElement.classList.add("kvps-img");
        imgElement.src = value.replace("img://", "/image_get?path=");
        imgElement.alt = "Image Attachment";
        tdiv.appendChild(imgElement);

        // Add click handler and cursor change
        imgElement.style.cursor = "pointer";
        imgElement.addEventListener("click", () => {
          imageViewerStore.open(imgElement.src, { refreshInterval: 1000 });
        });
      } else {
        const pre = document.createElement("pre");
        const span = document.createElement("span");
        span.innerHTML = convertHTML(value);
        pre.appendChild(span);
        tdiv.appendChild(pre);

        // KaTeX rendering for markdown
        if (latex) {
          span.querySelectorAll("latex").forEach((element) => {
            katex.render(element.innerHTML, element, {
              throwOnError: false,
            });
          });
        }
      }
    }
  } else {
    // Remove table if kvps is null/empty
    const existingTable = container.querySelector(".msg-kvps");
    if (existingTable) {
      existingTable.remove();
    }
  }
}

function convertToTitleCase(str) {
  return str
    .replace(/_/g, " ") // Replace underscores with spaces
    .toLowerCase() // Convert the entire string to lowercase
    .replace(/\b\w/g, function (match) {
      return match.toUpperCase(); // Capitalize the first letter of each word
    });
}

function convertImageTags(content) {
  // Replace <image> tags with <img> tags with base64 source - using pre-defined regex
  return content.replace(
    imageTagRegex,
    (match, base64Content) => {
      return `<img src="data:image/jpeg;base64,${base64Content}" alt="Image Attachment" />`;
    }
  );
}

function convertHTML(str) {
  if (typeof str !== "string") str = JSON.stringify(str, null, 2);

  let result = escapeHTML(str);
  result = convertImageTags(result);
  result = convertPathsToLinks(result);
  return result;
}

function convertImgFilePaths(str) {
  return str.replace(/img:\/\//g, "/image_get?path=");
}

export function convertIcons(str) {
  if (!str) return str;
  return str.replace(iconRegex, (match, icon) => {
    // List of known Material Symbols used in the app
    const materialSymbols = [
      "construction", "info", "warning", "error", "person", "smart_toy",
      "schedule", "check_circle", "cancel", "pending", "history", "settings",
      "search", "visibility", "visibility_off", "delete", "edit", "add", "close", "code"
    ];

    // List of known custom SVG icons in /public/
    const customSvgs = ["andy", "agent", "google", "agix_logo", "icon"];

    if (materialSymbols.includes(icon)) {
      return `<span class="icon material-symbols-outlined">${icon}</span>`;
    } else if (customSvgs.includes(icon)) {
      return `<img src="../public/${icon}.svg" class="agent-icon" style="filter: var(--svg-filter); height: 1.2em; width: 1.2em; vertical-align: middle; margin-right: 4px;">`;
    } else {
      // Fallback for unknown icons or profile names without custom SVGs
      // We use 'smart_toy' for agents/bots and 'person' for users as a safe default
      const defaultIcon = icon.includes("user") ? "person" : "smart_toy";
      return `<span class="icon material-symbols-outlined">${defaultIcon}</span>`;
    }
  });
}

function escapeHTML(str) {
  const escapeChars = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  };
  return str.replace(/[&<>'"]/g, (char) => escapeChars[char]);
}

function convertPathsToLinks(str) {
  if (typeof str !== "string" || !str.includes("/")) return str; // Fast path if no paths likely

  // Use pre-defined pathRegex for performance
  if (!pathRegex.test(str)) return str;
  pathRegex.lastIndex = 0; // Reset after test

  function generateLinks(match) {
    const parts = match.split("/");
    if (!parts[0]) parts.shift();
    let conc = "";
    let html = "";
    for (const part of parts) {
      conc += "/" + part;
      const escapedConc = conc.replace(/'/g, "\\'");
      html += `/<a href="#" class="path-link" onclick="openFileLink('${escapedConc}');">${part}</a>`;
    }
    return html;
  }

  // Single-pass replacement using combined regex is MUCH faster than split().map().join()
  // especially for large strings/logs
  return str.replace(combinedPathTagRegex, (match, tag, prefix, path) => {
    // If we matched a tag, return it unchanged
    if (tag) return tag;
    // Otherwise it's a prefix + path, return prefix + linkified path
    return (prefix || "") + generateLinks(path);
  });
}

function adjustMarkdownRender(element) {
  // find all tables in the element
  const elements = element.querySelectorAll("table");

  // wrap each with a div with class message-markdown-table-wrap
  elements.forEach((el) => {
    const wrapper = document.createElement("div");
    wrapper.className = "message-markdown-table-wrap";
    el.parentNode.insertBefore(wrapper, el);
    wrapper.appendChild(el);
  });

  // Handle Mermaid diagrams
  const mermaidBlocks = element.querySelectorAll("pre code.language-mermaid");
  if (mermaidBlocks.length > 0 && globalThis.mermaid) {
    mermaidBlocks.forEach((block) => {
      const pre = block.parentElement;
      const mermaidDiv = document.createElement("div");
      mermaidDiv.className = "mermaid";
      // Use textContent to avoid HTML entity issues
      mermaidDiv.textContent = block.textContent;
      pre.replaceWith(mermaidDiv);
    });

    try {
      // For Mermaid v10+, run() is preferred for dynamic rendering
      mermaid.run({
        nodes: element.querySelectorAll(".mermaid"),
        suppressErrors: true
      }).catch(err => console.debug("Mermaid.run sub-error:", err));
    } catch (e) {
      // Fallback for older versions or initialization issues
      if (typeof mermaid.init === 'function') {
        mermaid.init(undefined, element.querySelectorAll(".mermaid"));
      } else {
        console.error("Mermaid rendering failed and no fallback available:", e);
      }
    }
  }
}

class Scroller {
  constructor(element, immediateCheck = false) {
    this.element = element;
    this.wasAtBottom = false;
    // Only perform the expensive reflow check if immediately requested
    if (immediateCheck && element.isConnected) {
      this.wasAtBottom = this.isAtBottom();
    }
  }

  isAtBottom(tolerance = 10) {
    if (!this.element || !this.element.isConnected) return false;

    // These reads trigger synchronous reflows
    const scrollHeight = this.element.scrollHeight;
    const clientHeight = this.element.clientHeight;
    const scrollTop = this.element.scrollTop;

    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
    return distanceFromBottom <= tolerance;
  }

  // Captures the current scroll state before an update to reapply later
  capture() {
    if (this.element && this.element.isConnected) {
      this.wasAtBottom = this.isAtBottom();
    }
    return this;
  }

  reApplyScroll() {
    if (this.wasAtBottom && this.element && this.element.isConnected) {
      this.element.scrollTop = this.element.scrollHeight;
    }
  }
}

/**
 * Parse a timestamp into a Date object.
 * Handles:
 * - Numeric seconds (e.g. 1736440748.123)
 * - Numeric milliseconds (e.g. 1736440748123)
 * - ISO date strings
 * - Already formatted time strings (fallback)
 * @param {any} ts - The timestamp to parse
 * @returns {Date|null}
 */
export function parseTimestamp(ts) {
  if (!ts) return null;

  // If it's a number or a numeric string
  let n = parseFloat(ts);
  if (!isNaN(n) && isFinite(n)) {
    // Determine if it's seconds or milliseconds
    // 1e12 is ~2001 in ms, 1e10 is ~2286 in seconds
    // Anything above 1e11 is likely ms
    if (n > 100000000000) return new Date(n);
    return new Date(n * 1000);
  }

  // Try parsing as a general date string (e.g. ISO)
  const d = new Date(ts);
  if (!isNaN(d.getTime())) return d;

  return null;
}
