// Simplified Message Action Buttons - Keeping the Great Look & Feel
import { store as speechStore } from "../../chat/speech/speech-store.js";
import { createStore } from "../../../js/AlpineStore.js";
import { callJsonApi } from "../../../js/api.js";

// Extract text content from different message types in Markdown format
function getTextContent(element) {
  let markdown = "";

  // 1. Get Heading
  const heading = element.querySelector(".msg-heading h4");
  if (heading) {
    // Clone to remove icon and collapsed-thought before getting text
    const headingClone = heading.cloneNode(true);
    headingClone.querySelectorAll(".icon, .collapsed-thought, .material-symbols-outlined").forEach(el => el.remove());
    const headingText = headingClone.innerText.trim();
    if (headingText) {
      markdown += `### ${headingText}\n\n`;
    }
  }

  // 2. Get KVPs (Metadata)
  const kvpRows = element.querySelectorAll(".kvps-row");
  if (kvpRows.length > 0) {
    kvpRows.forEach(row => {
      const key = row.querySelector(".kvps-key")?.innerText.trim();
      const valDiv = row.querySelector(".kvps-val");
      if (key && valDiv) {
        const val = valDiv.getAttribute("data-raw-value") || valDiv.innerText.trim();
        // Format as markdown list item
        markdown += `**${key}**: ${val}\n`;
      }
    });
    markdown += "\n";
  }

  // 3. Get Main Content
  const rawElement = element.querySelector("[data-raw-markdown]");
  if (rawElement) {
    markdown += rawElement.getAttribute("data-raw-markdown");
  } else {
    // Fallback: Clone element to avoid modifying the original and remove unwanted UI elements
    const clone = element.cloneNode(true);
    clone.querySelectorAll(".action-buttons, .msg-min-max-btns, .msg-kvps, .msg-model-info, .msg-timestamp, .msg-heading").forEach(el => el.remove());

    // Check for images if no text
    const images = clone.querySelectorAll("img:not(.agent-icon):not(.file-icon)");
    if (images.length > 0 && clone.innerText.trim() === "") {
      const urls = Array.from(images).map(img => img.src).filter(Boolean);
      if (urls.length > 0) markdown += urls.join("\n\n");
    } else {
      markdown += clone.innerText.trim();
    }
  }

  return markdown.trim();
}


// Create and add action buttons to element
export function addActionButtonsToElement(element) {
  // Skip if buttons already exist
  if (element.querySelector(".action-buttons")) return;

  // Create container with same styling as original
  const container = document.createElement("div");
  container.className = "action-buttons";

  // Copy button - matches original design
  const copyBtn = document.createElement("button");
  copyBtn.className = "action-button copy-action";
  copyBtn.setAttribute("aria-label", "Copy text");
  copyBtn.innerHTML =
    '<span class="material-symbols-outlined">content_copy</span>';

  copyBtn.onclick = async (e) => {
    e.stopPropagation();

    // Check if the button container is still fading in (opacity < 0.5)
    if (parseFloat(window.getComputedStyle(container).opacity) < 0.5) return; // Don't proceed if still fading in

    const text = getTextContent(element);
    const icon = copyBtn.querySelector(".material-symbols-outlined");

    try {
      // Try modern clipboard API
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        // Fallback for local dev
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.style.position = "fixed";
        textarea.style.left = "-999999px";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
      }

      // Visual feedback
      icon.textContent = "check";
      copyBtn.classList.add("success");
      setTimeout(() => {
        icon.textContent = "content_copy";
        copyBtn.classList.remove("success");
      }, 2000);
    } catch (err) {
      console.error("Copy failed:", err);
      icon.textContent = "error";
      copyBtn.classList.add("error");
      setTimeout(() => {
        icon.textContent = "content_copy";
        copyBtn.classList.remove("error");
      }, 2000);
    }
  };

  // Speak button - matches original design
  const speakBtn = document.createElement("button");
  speakBtn.className = "action-button speak-action";
  speakBtn.setAttribute("aria-label", "Speak text");
  speakBtn.innerHTML =
    '<span class="material-symbols-outlined">volume_up</span>';

  speakBtn.onclick = async (e) => {
    e.stopPropagation();

    // Check if the button container is still fading in (opacity < 0.5)
    if (parseFloat(window.getComputedStyle(container).opacity) < 0.5) return; // Don't proceed if still fading in

    const text = getTextContent(element);
    const icon = speakBtn.querySelector(".material-symbols-outlined");

    if (!text || text.trim().length === 0) return;

    try {
      // Visual feedback
      icon.textContent = "check";
      speakBtn.classList.add("success");
      setTimeout(() => {
        icon.textContent = "volume_up";
        speakBtn.classList.remove("success");
      }, 2000);

      // Use speech store
      await speechStore.speak(text);
    } catch (err) {
      console.error("Speech failed:", err);
      icon.textContent = "error";
      speakBtn.classList.add("error");
      setTimeout(() => {
        icon.textContent = "volume_up";
        speakBtn.classList.remove("error");
      }, 2000);
    }
  };

  // Thumbs Up button
  const thumbsUpBtn = document.createElement("button");
  thumbsUpBtn.className = "action-button thumbs-up-action";
  thumbsUpBtn.setAttribute("aria-label", "Thumbs up");
  thumbsUpBtn.innerHTML = '<span class="material-symbols-outlined">thumb_up</span>';

  thumbsUpBtn.onclick = async (e) => {
    e.stopPropagation();
    if (parseFloat(window.getComputedStyle(container).opacity) < 0.5) return;

    const icon = thumbsUpBtn.querySelector(".material-symbols-outlined");
    try {
      const response = await fetchApi("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "up",
          content: getTextContent(element),
          message_id: element.closest('.message-container')?.id?.replace('message-', '')
        })
      });

      if (response && response.status === 200) {
        icon.textContent = "check";
        thumbsUpBtn.classList.add("success");
        setTimeout(() => {
          icon.textContent = "thumb_up";
          thumbsUpBtn.classList.remove("success");
        }, 2000);
      }
    } catch (err) {
      console.error("Feedback failed:", err);
    }
  };

  // Thumbs Down button
  const thumbsDownBtn = document.createElement("button");
  thumbsDownBtn.className = "action-button thumbs-down-action";
  thumbsDownBtn.setAttribute("aria-label", "Thumbs down");
  thumbsDownBtn.innerHTML = '<span class="material-symbols-outlined">thumb_down</span>';

  thumbsDownBtn.onclick = async (e) => {
    e.stopPropagation();
    if (parseFloat(window.getComputedStyle(container).opacity) < 0.5) return;

    const icon = thumbsDownBtn.querySelector(".material-symbols-outlined");
    try {
      const response = await fetchApi("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "down",
          content: getTextContent(element),
          message_id: element.closest('.message-container')?.id?.replace('message-', '')
        })
      });

      if (response && response.status === 200) {
        icon.textContent = "check";
        thumbsDownBtn.classList.add("success");
        setTimeout(() => {
          icon.textContent = "thumb_down";
          thumbsDownBtn.classList.remove("success");
        }, 2000);
      }
    } catch (err) {
      console.error("Feedback failed:", err);
    }
  };

  // Delete button
  const deleteBtn = document.createElement("button");
  deleteBtn.className = "action-button delete-action";
  deleteBtn.setAttribute("aria-label", "Delete message");
  deleteBtn.innerHTML = '<span class="material-symbols-outlined">delete</span>';

  deleteBtn.onclick = async (e) => {
    e.stopPropagation();
    if (parseFloat(window.getComputedStyle(container).opacity) < 0.5) return;

    const confirmed = await Alpine.store('confirmation').confirm(
      'Are you sure you want to delete this message?',
      { target: deleteBtn }
    );
    if (!confirmed) return;

    const icon = deleteBtn.querySelector(".material-symbols-outlined");
    const msgContainer = element.closest('.message-container');
    const messageId = msgContainer?.id?.replace('message-', '');
    const context = getContext();

    try {
      const response = await fetchApi("/message_delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message_id: messageId,
          context: context
        })
      });

      if (response && response.status === 200) {
        // Find the group this message belongs to
        const group = msgContainer.closest('.message-group');
        msgContainer.remove();

        // If the group is now empty, remove it too
        if (group && group.querySelectorAll('.message-container').length === 0) {
          group.remove();
        }

        // Use a notification if available
        if (globalThis.toast) globalThis.toast("Message deleted", "success");
      }
    } catch (err) {
      console.error("Delete failed:", err);
      if (globalThis.toast) globalThis.toast("Delete failed", "error");
    }
  };

  // Golden Prompt button
  const goldenBtn = document.createElement("button");
  goldenBtn.className = "action-button golden-action";
  goldenBtn.setAttribute("aria-label", "Save as Golden Prompt");
  goldenBtn.innerHTML = '<span class="material-symbols-outlined">star</span>';

  goldenBtn.onclick = async (e) => {
    e.stopPropagation();
    if (parseFloat(window.getComputedStyle(container).opacity) < 0.5) return;

    const icon = goldenBtn.querySelector(".material-symbols-outlined");
    const text = getTextContent(element);

    try {
      const response = await fetchApi("/api/prompts/golden/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: text
        })
      });

      const data = await response.json();
      if (data && data.success) {
        icon.textContent = "check";
        goldenBtn.classList.add("success");
        setTimeout(() => {
          icon.textContent = "star";
          goldenBtn.classList.remove("success");
        }, 2000);
        if (globalThis.toast) globalThis.toast("Saved as Golden Prompt", "success");
      } else {
        throw new Error(data.error || "Failed to save");
      }
    } catch (err) {
      console.error("Golden prompt save failed:", err);
      icon.textContent = "error";
      goldenBtn.classList.add("error");
      setTimeout(() => {
        icon.textContent = "star";
        goldenBtn.classList.remove("error");
      }, 2000);
      if (globalThis.toast) globalThis.toast("Save failed: " + err.message, "error");
    }
  };

  // Reply button
  const replyBtn = document.createElement("button");
  replyBtn.className = "action-button reply-action";
  replyBtn.setAttribute("aria-label", "Reply to message");
  replyBtn.innerHTML = '<span class="material-symbols-outlined">reply</span>';

  replyBtn.onclick = (e) => {
    e.stopPropagation();
    if (parseFloat(window.getComputedStyle(container).opacity) < 0.5) return;

    const text = getTextContent(element);
    const msgContainer = element.closest('.message-container');
    const messageId = msgContainer?.id?.replace('message-', '');

    const inputStore = globalThis.Alpine?.store("chatInput");
    if (inputStore) {
      inputStore.setQuote(text, messageId);
    }
  };

  container.append(replyBtn, copyBtn, speakBtn, thumbsUpBtn, thumbsDownBtn, goldenBtn, deleteBtn);
  element.appendChild(container);
}
