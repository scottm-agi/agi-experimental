/**
 * Message Helpers Module
 * Shared utilities: regex constants, escaping, formatting, KVP rendering,
 * path conversion, secret masking, Mermaid handling, and Scroller.
 * Extracted from messages.js for modularization (P2.1).
 */

import { store as imageViewerStore } from "../components/modals/image-viewer/image-viewer-store.js";
import { addActionButtonsToElement } from "../components/messages/action-buttons/simple-action-buttons.js";

export function getApiBase() {
  const pathname = globalThis.location.pathname;
  return pathname.startsWith('/agi/') || pathname === '/agi' ? '/agi' : '';
}


// Persistent regexes and configurations for performance
export const imageTagRegex = /<image>(.*?)<\/image>/g;
export const iconRegex = /icon:\/\/([a-zA-Z0-9_]+)/g;
const pathPrefix = `(?:^|[> \`'"\\n:,;()\\[\\]]|\&#39;|\&quot;)`;
const folderCharset = `[a-zA-Z0-9_\\/.\\-]`;
const fileCharset = `[a-zA-Z0-9_\\-\\/]`;
const pathSuffix = `(?<!\\.)`;
// pathRegex optimized: removed lookbehind to make it compatible with more environments and potentially faster in OR groups
const pathRegexMain = `\\/${folderCharset}*${fileCharset}`;
export const pathRegex = new RegExp(`(${pathPrefix})(${pathRegexMain})`, "g");
export const tagRegex = /(?:<(?:[^<>"']+|"[^"]*"|'[^']*')*>)/g;

// Combined regex for single-pass processing: (Tag) | (Prefix)(Path)
export const combinedPathTagRegex = new RegExp(`(${tagRegex.source})|(${pathPrefix})(${pathRegexMain})`, "g");

// Secret masking patterns — detects common token/key/password formats
// Matches: sk-xxx, ghp_xxx, gho_xxx, xoxb-xxx, eyJ (JWT), long hex strings (40+), etc.
export const secretPatterns = [
  // API key prefixes: prioritized longer ones (sk-or-v1 before sk)
  /\b(sk-or-v1|sk|pk|ak|rk)-[A-Za-z0-9_\-]{16,}\b/g,
  // GitHub tokens: ghp_, gho_, ghs_, ghr_, github_pat_
  /\b(ghp_|gho_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{20,}\b/g,
  // Slack tokens: xoxb-, xoxp-, xoxe-, xoxa-
  /\bxox[bpea]-[A-Za-z0-9\-]{20,}\b/g,
  // AWS keys: AKIA followed by 16 chars
  /\bAKIA[A-Z0-9]{16}\b/g,
  // JWT tokens: eyJ...
  /\beyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b/g,
  // Bearer tokens in key=value context
  /\b(Bearer\s+)[A-Za-z0-9_\-\.]{20,}\b/g,
  // Generic long hex strings (40+ chars, like SHA tokens)
  /\b[0-9a-f]{40,}\b/gi,
  // key=value pairs where key contains password/secret/token/api_key
  /\b((?:password|passwd|secret|token|api[_-]?key|access[_-]?key|auth[_-]?token|client[_-]?secret)\s*[:=]\s*)([^\s,;'"]{8,})/gi,
];

/**
 * Global helper to toggle secret visibility in chat history.
 * Bound to window to be accessible from inline onclick.
 */
export function toggleChatSecret(container) {
  const isRevealed = container.dataset.revealed === "true";
  const nextState = !isRevealed;

  container.dataset.revealed = nextState ? "true" : "false";

  const maskedVal = container.querySelector(".masked-value");
  const revealedVal = container.querySelector(".revealed-value");

  if (maskedVal) maskedVal.style.display = nextState ? "none" : "";
  if (revealedVal) revealedVal.style.display = nextState ? "" : "none";
};

/**
 * Masks detected secrets in a rendered DOM element.
 * Called after HTML content is set on message elements.
 * Walks text nodes and wraps secret matches in masked spans.
 */
export function maskSecretsInElement(element) {
  if (!element) return;

  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, null);
  const textNodes = [];
  let node;
  while ((node = walker.nextNode())) {
    // Skip nodes inside code blocks or already-masked spans.
    // NOTE: We no longer skip 'pre' tags as they are commonly used for plain text wraps.
    if (node.parentElement?.closest('code, .masked-secret')) continue;
    textNodes.push(node);
  }

  for (const textNode of textNodes) {
    const text = textNode.textContent;
    let hasMatch = false;
    let resultHtml = text;

    for (const pattern of secretPatterns) {
      pattern.lastIndex = 0; // Reset regex
      if (pattern.test(resultHtml)) {
        hasMatch = true;
        pattern.lastIndex = 0;

        resultHtml = resultHtml.replace(pattern, (...args) => {
          let prefix = "";
          let value = args[0]; // Full match by default

          // Regex callback args: match, [p1, p2, ...], offset, string
          // We count groups by checking how many args precede the offset (number)
          const offsetIdx = args.findIndex(arg => typeof arg === 'number');
          const numGroups = offsetIdx - 1;

          // If we have at least 2 groups, we treat p1 as prefix and p2 as value
          // (e.g. for "password=SECRET" or "Bearer SECRET")
          if (numGroups >= 2) {
            prefix = args[1];
            value = args[2];
          }

          const masked = '•'.repeat(Math.min(value.length, 24));
          return `${escapeHTMLSafe(prefix)}<span class="masked-secret" data-revealed="false" onclick="toggleChatSecret(this)" title="Click to show/hide">` +
            `<span class="masked-value">${escapeHTMLSafe(masked)}</span>` +
            `<span class="revealed-value" style="display:none">${escapeHTMLSafe(value)}</span></span>`;
        });
      }
    }

    if (hasMatch) {
      const wrapper = document.createElement('span');
      wrapper.innerHTML = resultHtml;
      textNode.replaceWith(wrapper);
    }
  }
}

export function escapeHTMLSafe(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}


/**
 * Standard set of technical metadata keys that should be hidden from the UI
 * used to determine if a message is "empty" and for KVP filtering.
 */
export const standardSkipKeys = new Set([
  'sequence_id', 'hash', 'id',
  'finished', 'Finished',
  'Sequence Id', 'completion',
  'actual_model', 'actual_provider',
  'duration', 'tokens', 'cost'
]);

/**
 * Determines if a message object should be hidden from the user.
 * Skips [TRACE] diagnostic logs and messages with only technical metadata.
 */
export function isMessageEmpty(type, heading, content, kvps) {
  if (type === 'user') return false;

  const trimmedHeading = heading ? heading.trim() : "";
  const trimmedContent = content ? content.trim() : "";

  // TRACE messages and Tool Performance metrics are diagnostic noise and should be hidden
  if (trimmedHeading.startsWith('[TRACE]') || trimmedContent.startsWith('Tool Performance:')) {
    return true;
  }

  const hasHeading = trimmedHeading.length > 0;
  const hasContent = trimmedContent.length > 0;

  let hasVisibleKvps = false;
  if (kvps) {
    try {
      const activeKvps = typeof kvps === 'string' ? JSON.parse(kvps) : kvps;
      hasVisibleKvps = Object.keys(activeKvps).some(key => !standardSkipKeys.has(key));
    } catch (e) {
      // If parsing fails, fall back to assuming it's visible if we have keys
      hasVisibleKvps = true;
    }
  }

  // For response/agent types: heading renders as group header, not bubble content.
  // A heading-only message with no body content produces an empty bubble with just a timestamp.
  // Treat these as empty to prevent "ghost bubbles" in the chat UI.
  const headingOnlyTypes = new Set(['response', 'agent']);
  if (headingOnlyTypes.has(type) && !hasContent && !hasVisibleKvps) {
    return true;
  }

  return !hasHeading && !hasContent && !hasVisibleKvps;
}


// marked is configured in messages.js core

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


export function drawKvps(container, kvps, latex) {
  if (kvps) {
    // Skip internal metadata keys that shouldn't be displayed
    const filteredEntries = Object.entries(kvps).filter(([key]) => !standardSkipKeys.has(key));
    if (filteredEntries.length === 0) return; // Nothing to render

    const table = document.createElement("table");
    table.classList.add("msg-kvps");
    for (let [key, value] of filteredEntries) {
      const row = table.insertRow();
      row.classList.add("kvps-row");
      if (key === "thoughts" || key === "reasoning" || key === "technical_details")
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
          imgElement.src = value.replace("img://", `${getApiBase()}/image_get?path=`);
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

export function drawKvpsIncremental(container, kvps, latex) {
  if (kvps) {
    // Skip internal metadata keys that shouldn't be displayed
    const kvpEntries = Object.entries(kvps).filter(([key]) => !standardSkipKeys.has(key));
    if (kvpEntries.length === 0) return; // Nothing to render

    // Find existing table or create new one
    let table = container.querySelector(".msg-kvps");
    if (!table) {
      table = document.createElement("table");
      table.classList.add("msg-kvps");
      container.appendChild(table);
    }

    // Create a single scroller for the container - optimized to avoid reflow if possible
    const scroller = new Scroller(container);
    if (container.isConnected) scroller.capture();

    // Get all current rows for comparison
    let existingRows = table.querySelectorAll(".kvps-row");

    // Update or create rows as needed
    kvpEntries.forEach(([key, value], index) => {
      let row = existingRows[index];

      if (!row) {
        // Create new row if it doesn't exist
        row = table.insertRow();
        row.classList.add("kvps-row");
      }

      // Update row classes
      const targetClassName = "kvps-row" + (key === "thoughts" || key === "reasoning" || key === "technical_details" ? " msg-thoughts" : "");
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
        imgElement.src = value.replace("img://", `${getApiBase()}/image_get?path=`);
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
        maskSecretsInElement(span);
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

export function convertToTitleCase(str) {
  return str
    .replace(/_/g, " ") // Replace underscores with spaces
    .toLowerCase() // Convert the entire string to lowercase
    .replace(/\b\w/g, function (match) {
      return match.toUpperCase(); // Capitalize the first letter of each word
    });
}


export function convertImageTags(content) {
  // Replace <image> tags with <img> tags with base64 source - using pre-defined regex
  return content.replace(
    imageTagRegex,
    (match, base64Content) => {
      return `<img src="data:image/jpeg;base64,${base64Content}" alt="Image Attachment" />`;
    }
  );
}

export function convertHTML(str) {
  if (typeof str !== "string") str = JSON.stringify(str, null, 2);

  let result = escapeHTML(str);
  result = convertImageTags(result);
  result = convertPathsToLinks(result);
  return result;
}


export function convertImgFilePaths(str) {
  return str.replace(/img:\/\//g, `${getApiBase()}/image_get?path=`);
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


export function escapeHTML(str) {
  const escapeChars = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  };
  return str.replace(/[&<>'"]/g, (char) => escapeChars[char]);
}


export function convertPathsToLinks(str) {
  if (typeof str !== "string") return str;

  // Phase 1: Linkify http/https URLs that aren't already inside <a> or <img> tags.
  // This handles raw URLs in the content that marked.parse didn't auto-link.
  const urlRegex = /(?:<[^>]+>)|((?:https?:\/\/)[^\s<>"'`\)\]]+)/gi;
  str = str.replace(urlRegex, (match, url) => {
    if (!url) return match; // It was an HTML tag — leave it alone
    // Don't linkify if already inside an href (covered by the tag skip above)
    const cleanUrl = url.replace(/[.,;:!?)]+$/, ''); // Strip trailing punctuation
    const trailing = url.slice(cleanUrl.length);
    return `<a href="${cleanUrl}" target="_blank" rel="noopener noreferrer" class="url-link">${cleanUrl}</a>${trailing}`;
  });

  // Phase 2: Convert file system paths to file card icons
  if (!str.includes("/")) return str; // Fast path if no paths likely

  // Use pre-defined pathRegex for performance
  if (!pathRegex.test(str)) return str;
  pathRegex.lastIndex = 0; // Reset after test

  // Map file extensions to icon type names (matching public/*.svg)
  function getFileIconType(filename) {
    const ext = filename.split('.').pop().toLowerCase();
    const iconMap = {
      py: 'python', js: 'javascript', ts: 'typescript', jsx: 'javascript', tsx: 'typescript',
      html: 'html', css: 'css', json: 'json', xml: 'xml', yaml: 'yaml', yml: 'yaml',
      md: 'markdown', txt: 'text', log: 'text', csv: 'text',
      sh: 'shell', bash: 'shell', zsh: 'shell',
      png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', svg: 'image', webp: 'image',
      mp4: 'video', webm: 'video', mov: 'video',
      mp3: 'audio', wav: 'audio', ogg: 'audio',
      pdf: 'pdf', doc: 'document', docx: 'document', pptx: 'document',
      zip: 'archive', tar: 'archive', gz: 'archive', rar: 'archive',
      sql: 'database', db: 'database', sqlite: 'database',
      env: 'config', toml: 'config', ini: 'config', cfg: 'config',
      rs: 'rust', go: 'go', java: 'java', rb: 'ruby', php: 'php', c: 'c', cpp: 'cpp', h: 'c',
    };
    return iconMap[ext] || 'file';
  }

  // Issue #1093: IP address pattern — skip file card rendering for IP addresses
  // Matches patterns like 0.0.0.0, 127.0.0.1, 192.168.1.1, etc.
  const ipPattern = /^\d+\.\d+\.\d+\.\d+/;

  // Issue #1152: Extensions that are NOT deliverable files — they are TLDs, web formats,
  // or domain suffixes that should never render as file "pill" cards in chat.
  const nonDeliverableExtensions = new Set([
    'html', 'htm',                                          // web pages, not files
    'ai', 'com', 'net', 'org', 'io', 'space', 'dev',       // TLDs
    'app', 'co', 'me', 'us', 'uk', 'xyz', 'info', 'biz',   // TLDs
    'gg', 'cc', 'tv', 'fm', 'so', 'sh', 'ly', 'gl',        // short TLDs
    'cloud', 'tech', 'site', 'online', 'store', 'blog',     // new TLDs
  ]);

  function generateFileCard(fullPath) {
    const escapedPath = fullPath.replace(/'/g, "\\'");
    const filename = fullPath.split('/').pop();

    // Issue #1152: Skip paths starting with '//' — these are URL scheme separators
    // (e.g., the `://domain.tld/path` from an already-linkified URL), never valid file paths.
    if (fullPath.startsWith('//')) {
      return `<span class="path-text">${fullPath}</span>`;
    }

    // Issue #1093: Skip file card for IP addresses detected as file paths
    // e.g., /0.0.0.0:5100/api/discovery or paths whose last segment looks like an IP
    if (ipPattern.test(filename) || ipPattern.test(fullPath.replace(/^\//, ''))) {
      return `<span class="path-text">${fullPath}</span>`;
    }

    const hasExtension = filename.includes('.') && filename.lastIndexOf('.') > 0;

    // Also skip purely numeric dotted segments (e.g., version numbers like 3.0.2)
    if (hasExtension && /^\d+(\.\d+)+$/.test(filename)) {
      return `<span class="path-text">${fullPath}</span>`;
    }

    if (!hasExtension) {
      // Directory or extensionless — render as simple inline link
      return `<a href="#" class="path-link" onclick="openFileLink('${escapedPath}'); return false;" title="${fullPath}">${fullPath}</a>`;
    }

    const ext = filename.split('.').pop().toLowerCase();

    // Issue #1152: Only render file pills for actual deliverable file extensions.
    // TLDs, web page extensions, and domain suffixes should NOT get file cards.
    if (nonDeliverableExtensions.has(ext)) {
      return `<span class="path-text">${fullPath}</span>`;
    }

    const extUpper = ext.toUpperCase();
    const iconType = getFileIconType(filename);
    const basePath = (typeof getApiBase === 'function' ? getApiBase() : '');
    const iconSrc = basePath ? `${basePath}/public/${iconType}.svg` : `public/${iconType}.svg`;
    const fallbackSrc = basePath ? `${basePath}/public/file.svg` : `public/file.svg`;

    return `<a href="#" class="file-card-link" onclick="openFileLink('${escapedPath}'); return false;" title="${fullPath}">` +
      `<span class="file-card-icon"><img src="${iconSrc}" alt="${iconType}" onerror="this.src='${fallbackSrc}'"></span>` +
      `<span class="file-card-info">` +
        `<span class="file-card-name">${filename}</span>` +
        `<span class="file-card-ext">${extUpper}</span>` +
      `</span>` +
    `</a>`;
  }

  // Single-pass replacement using combined regex is MUCH faster than split().map().join()
  // especially for large strings/logs
  return str.replace(combinedPathTagRegex, (match, tag, prefix, path, offset) => {
    // If we matched a tag, return it unchanged
    if (tag) return tag;
    // Skip paths that are already inside an href (the URL linkification already handled them)
    // This check prevents double-linkification
    if (prefix && prefix.includes('"') && match.includes('href')) return match;

    // Issue #1152: Skip paths that are inside <a> tag content.
    // After Phase 1 URL linkification, paths like /fair_use.html inside
    // <a>https://suchir.net/fair_use.html</a> would still match.
    // Check if the match position is between an <a and </a>.
    const before = str.substring(Math.max(0, offset - 500), offset);
    const lastAnchorOpen = before.lastIndexOf('<a ');
    const lastAnchorClose = before.lastIndexOf('</a>');
    if (lastAnchorOpen > lastAnchorClose) {
      // We're inside an <a> tag — don't convert to file card
      return match;
    }

    // Otherwise it's a prefix + path, return prefix + linkified path
    return (prefix || "") + generateFileCard(path);
  });
}

export function adjustMarkdownRender(element) {
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
      const mermaidId = `mermaid-${Math.random().toString(36).substr(2, 9)}`;
      const rawContent = block.textContent;

      const container = document.createElement("div");
      container.className = "mermaid-container";
      container.id = `${mermaidId}-container`;

      const mermaidDiv = document.createElement("div");
      mermaidDiv.className = "mermaid";
      mermaidDiv.id = mermaidId;
      mermaidDiv.textContent = rawContent;
      container.appendChild(mermaidDiv);

      pre.replaceWith(container);

      try {
        // For Mermaid v10+, run() is preferred for dynamic rendering
        mermaid.run({
          nodes: [mermaidDiv],
          suppressErrors: false // We want to catch errors ourself
        }).catch(err => {
          console.debug("Mermaid.run sub-error:", err);
          handleMermaidError(container, rawContent, err);
        });
      } catch (e) {
        // Fallback for older versions or initialization issues
        if (typeof mermaid.init === 'function') {
          try {
            mermaid.init(undefined, [mermaidDiv]);
          } catch (initErr) {
            handleMermaidError(container, rawContent, initErr);
          }
        } else {
          console.error("Mermaid rendering failed and no fallback available:", e);
          handleMermaidError(container, rawContent, e);
        }
      }
    });
  }
}

export function handleMermaidError(container, rawContent, error) {
  // Clear the container but keep the original ID if possible
  const mermaidDiv = container.querySelector(".mermaid");
  if (mermaidDiv) mermaidDiv.style.display = "none";

  // Check if error box already exists
  if (container.querySelector(".mermaid-error-box")) return;

  const errorBox = document.createElement("div");
  errorBox.className = "mermaid-error-box";
  errorBox.innerHTML = `
    <div class="mermaid-error-header">
      <span class="material-symbols-outlined">warning</span>
      <span>Mermaid Syntax Error</span>
      <button class="mermaid-raw-toggle btn btn-xs btn-ghost">Show Raw Code</button>
    </div>
    <div class="mermaid-raw-content" style="display: none;">
      <pre><code>${escapeHTML(rawContent)}</code></pre>
    </div>
  `;

  const toggleBtn = errorBox.querySelector(".mermaid-raw-toggle");
  const rawDiv = errorBox.querySelector(".mermaid-raw-content");

  toggleBtn.addEventListener("click", () => {
    const isHidden = rawDiv.style.display === "none";
    rawDiv.style.display = isHidden ? "block" : "none";
    toggleBtn.textContent = isHidden ? "Hide Raw Code" : "Show Raw Code";
  });

  container.appendChild(errorBox);
}

export class Scroller {
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

// Backward-compatible global binding
globalThis.toggleChatSecret = toggleChatSecret;
