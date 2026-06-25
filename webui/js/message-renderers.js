/**
 * Message Renderers Module
 * All drawMessage* handler functions for different message types.
 * Extracted from messages.js for modularization (P2.1).
 */

import { store as imageViewerStore } from "../components/modals/image-viewer/image-viewer-store.js";
import { renderA2UITile } from "../components/chat/a2ui-tile/a2ui-tile-renderer.js";
import { store as attachmentsStore } from "../components/chat/attachments/attachmentsStore.js";
import { addActionButtonsToElement } from "../components/messages/action-buttons/simple-action-buttons.js";
import {
    escapeHTML, convertIcons, convertHTML, maskSecretsInElement
} from './message-helpers.js';

// _drawMessage is imported from core messages.js to avoid circular deps
// Instead, we accept it as a parameter or import from messages.js
// We'll use a late-binding approach
let _drawMessageFn = null;

export function setDrawMessageFn(fn) {
    _drawMessageFn = fn;
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
  _drawMessageFn(
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

  _drawMessageFn(
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
  // PRIMARY PATH: Check kvps for structured A2UI payload from tool response
  let cleanContent = content;
  let a2uiPayload = null;

  if (kvps && kvps.type === "a2ui" && kvps.payload) {
    a2uiPayload = kvps.payload;
    cleanContent = ""; // Hide raw content when we have structured payload
  }

  // FALLBACK: Intercept A2UI: blocks from agent responses (content string parsing)
  if (!a2uiPayload && content && (content.trim().startsWith("{") || content.includes("A2UI:"))) {
    try {
      let jsonStr = "";
      let startIndex = -1;

      if (content.trim().startsWith("{")) {
        startIndex = content.indexOf("{");
      } else {
        startIndex = content.indexOf("A2UI:");
      }

      if (startIndex !== -1) {
        const rest = content.substring(startIndex + (content.includes("A2UI:") ? 5 : 0));
        const firstBrace = rest.indexOf("{");
        if (firstBrace !== -1) {
          // Robust iterative JSON parsing to handle nested braces and trailing text
          let bestParsed = null;
          let bestEndIndex = -1;
          for (let i = firstBrace; i < rest.length; i++) {
            if (rest[i] === "}") {
              const testStr = rest.substring(firstBrace, i + 1);
              try {
                const parsed = JSON.parse(testStr);
                // Basic validation of A2UI schema
                if (parsed.messages || parsed.components || parsed.root || parsed.a2ui_v09 || parsed.component_type) {
                  bestParsed = parsed;
                  bestEndIndex = i;
                }
              } catch (e) {
                // Not valid JSON yet, keep trying
              }
            }
          }

          if (bestParsed) {
            a2uiPayload = bestParsed;
            const fullBlock = content.substring(startIndex, startIndex + (content.includes("A2UI:") ? 5 : 0) + bestEndIndex + 1);
            cleanContent = content.replace(fullBlock, "").trim();
          }
        }
      }
    } catch (e) {
      // Not valid A2UI JSON, keep original content
    }
  }

  _drawMessageFn(
    messageContainer,
    heading,
    cleanContent || (a2uiPayload ? "" : content), // Show nothing if purely A2UI
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

  // Render A2UI tile as standalone sibling after the message container
  // so it stays visible even when the message is collapsed
  if (a2uiPayload) {
    const tileId = `tile-${id}`;
    if (!document.getElementById(tileId)) {
      requestAnimationFrame(() => {
        if (document.getElementById(tileId)) return;
        try {
          const tile = renderA2UITile(a2uiPayload);
          tile.id = tileId;
          tile.classList.add('a2ui-tile--inline');
          const parentGroup = messageContainer.closest(".message-group");
          if (parentGroup) {
            messageContainer.after(tile);
          } else {
            const chatHistory = document.getElementById("chat-history");
            if (chatHistory) chatHistory.appendChild(tile);
          }
        } catch (err) {
          console.error("[UI] Failed to render tile:", err);
        }
      });
    }
  }
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
  _drawMessageFn(
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
    const date = new Date(timestamp * 1000);
    const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    userHeadingHtml = `<span class="msg-timestamp">${timeStr}</span> ` + userHeadingHtml;
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
    spanElement.setAttribute("data-raw-markdown", content);
    addActionButtonsToElement(textDiv);
    maskSecretsInElement(spanElement);
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
  // PRIMARY PATH: Check kvps for structured A2UI payload from tool response
  let cleanContent = content;
  let a2uiPayload = null;

  if (kvps && kvps.type === "a2ui" && kvps.payload) {
    a2uiPayload = kvps.payload;
    cleanContent = ""; // Hide raw content when we have structured payload
  }

  // FALLBACK: Intercept A2UI: blocks from tool output strings (content string parsing)
  if (!a2uiPayload && content && (content.trim().startsWith("{") || content.includes("A2UI:"))) {
    try {
      let jsonStr = "";
      let startIndex = -1;

      if (content.trim().startsWith("{")) {
        startIndex = content.indexOf("{");
      } else {
        startIndex = content.indexOf("A2UI:");
      }

      if (startIndex !== -1) {
        const rest = content.substring(startIndex + (content.includes("A2UI:") ? 5 : 0));
        const firstBrace = rest.indexOf("{");
        if (firstBrace !== -1) {
          // Robust iterative JSON parsing to handle nested braces and trailing text
          let bestParsed = null;
          let bestEndIndex = -1;
          for (let i = firstBrace; i < rest.length; i++) {
            if (rest[i] === "}") {
              const testStr = rest.substring(firstBrace, i + 1);
              try {
                const parsed = JSON.parse(testStr);
                // Basic validation of A2UI schema
                if (parsed.messages || parsed.components || parsed.root || parsed.a2ui_v09 || parsed.component_type) {
                  bestParsed = parsed;
                  bestEndIndex = i;
                }
              } catch (e) {
                // Not valid JSON yet, keep trying
              }
            }
          }

          if (bestParsed) {
            a2uiPayload = bestParsed;
            const fullBlock = content.substring(startIndex, startIndex + (content.includes("A2UI:") ? 5 : 0) + bestEndIndex + 1);
            cleanContent = content.replace(fullBlock, "").trim();
          }
        }
      }
    } catch (e) {
      // Not valid A2UI JSON, keep original content
    }
  }

  _drawMessageFn(
    messageContainer,
    heading,
    cleanContent || (a2uiPayload ? "" : content), // Show nothing if purely A2UI
    temp,
    true,
    "message-tool",
    icon,
    kvps,
    ["message-ai"],
    [],
    true,
    false,
    true,
    timestamp
  );

  // Render A2UI tile as standalone sibling after the tool message container
  // so it stays visible even when the tool call is collapsed.
  // Also handles kvps-based payload from the tool transport layer.
  if (kvps && kvps.type === "a2ui" && kvps.payload && !a2uiPayload) {
    a2uiPayload = kvps.payload;
  }
  if (a2uiPayload) {
    const tileId = `tile-${id}`;
    if (!document.getElementById(tileId)) {
      requestAnimationFrame(() => {
        if (document.getElementById(tileId)) return;
        try {
          const tile = renderA2UITile(a2uiPayload);
          tile.id = tileId;
          tile.classList.add('a2ui-tile--inline');
          const parentGroup = messageContainer.closest(".message-group");
          if (parentGroup) {
            messageContainer.after(tile);
          } else {
            const chatHistory = document.getElementById("chat-history");
            if (chatHistory) chatHistory.appendChild(tile);
          }
        } catch (err) {
          console.error("[UI] Failed to render tile:", err);
        }
      });
    }
  }
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
  _drawMessageFn(
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
  _drawMessageFn(
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
  _drawMessageFn(
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
  _drawMessageFn(
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
