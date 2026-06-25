/**
 * Universal File Preview Module
 *
 * Standalone, self-contained file preview overlay that can be used
 * from any component (chat, file browser, etc.) without pulling in
 * the full file-browser modal.
 *
 * Usage:
 *   import { showFilePreview } from './file-preview.js';
 *   showFilePreview('/agix/requirements.txt');
 *
 * Or via the global:
 *   window.showFilePreview('/path/to/file');
 */

import { fetchApi } from "./api.js";

// ── Preview-type detection ──────────────────────────────────────────────

const PREVIEWABLE_EXT = {
  // Text / code
  txt: "text", md: "text", log: "text", csv: "text", tsv: "text",
  py: "text", js: "text", ts: "text", jsx: "text", tsx: "text",
  html: "text", css: "text", scss: "text", less: "text",
  json: "text", yaml: "text", yml: "text", xml: "text", toml: "text",
  sh: "text", bash: "text", zsh: "text", fish: "text",
  rs: "text", go: "text", java: "text", rb: "text", php: "text",
  c: "text", cpp: "text", h: "text", hpp: "text",
  sql: "text", env: "text", ini: "text", cfg: "text",
  dockerfile: "text", makefile: "text", gitignore: "text",
  // Images
  png: "image", jpg: "image", jpeg: "image", gif: "image",
  svg: "image", webp: "image", bmp: "image", ico: "image",
};

const MAX_PREVIEW_SIZE = 2 * 1024 * 1024; // 2 MB

/**
 * Determine if a file is previewable.
 * @param {string} filename
 * @returns {string|false} — "text" | "image" | false
 */
export function isPreviewable(filename) {
  if (!filename) return false;
  const ext = filename.split(".").pop().toLowerCase();
  return PREVIEWABLE_EXT[ext] || false;
}

// ── Format helpers ──────────────────────────────────────────────────────

function formatFileSize(bytes) {
  if (bytes == null || bytes < 0) return "";
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, i);
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[i]}`;
}

// ── Overlay DOM management ──────────────────────────────────────────────

let overlayEl = null;
let currentObjectURL = null;

function ensureOverlay() {
  if (overlayEl) return overlayEl;

  overlayEl = document.createElement("div");
  overlayEl.id = "universal-file-preview";
  overlayEl.className = "ufp-overlay";
  overlayEl.style.display = "none";
  overlayEl.innerHTML = `
    <div class="ufp-panel">
      <div class="ufp-header">
        <div class="ufp-title">
          <span class="ufp-filename"></span>
          <span class="ufp-size"></span>
        </div>
        <div class="ufp-actions">
          <button class="ufp-btn ufp-download-btn" title="Download">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 19.5 19.5" width="16" height="16">
              <path d="m.75,14.25v2.25c0,1.24,1.01,2.25,2.25,2.25h13.5c1.24,0,2.25-1.01,2.25-2.25v-2.25m-4.5-4.5l-4.5,4.5m0,0l-4.5-4.5m4.5,4.5V.75"
                fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"/>
            </svg>
            Download
          </button>
          <button class="ufp-btn ufp-close-btn" title="Close">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="16" height="16">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
      </div>
      <div class="ufp-body">
        <div class="ufp-loading" style="display:none;">
          <div class="loading-spinner"></div>
          <p>Loading preview…</p>
        </div>
        <div class="ufp-image-container" style="display:none;">
          <img class="ufp-image" alt="" />
        </div>
        <pre class="ufp-content" style="display:none;"><code></code></pre>
      </div>
    </div>`;

  // Backdrop click → close
  overlayEl.addEventListener("click", (e) => {
    if (e.target === overlayEl) closePreview();
  });

  // Close button
  overlayEl.querySelector(".ufp-close-btn").addEventListener("click", closePreview);

  // Escape key
  overlayEl._keyHandler = (e) => {
    if (e.key === "Escape") closePreview();
  };

  document.body.appendChild(overlayEl);
  return overlayEl;
}

// ── Core API ────────────────────────────────────────────────────────────

/**
 * Show a file preview overlay.
 * @param {string} filePath — absolute path on the agent filesystem
 * @param {object} [opts] — optional { fileName, fileSize }
 */
export async function showFilePreview(filePath, opts = {}) {
  const overlay = ensureOverlay();
  const fileName = opts.fileName || filePath.split("/").pop();
  const previewType = isPreviewable(fileName);

  if (!previewType) {
    // Not previewable — trigger download directly
    downloadFile(filePath, fileName);
    return;
  }

  // Show overlay
  overlay.style.display = "";
  document.addEventListener("keydown", overlay._keyHandler);

  // Set header
  overlay.querySelector(".ufp-filename").textContent = fileName;
  overlay.querySelector(".ufp-size").textContent = opts.fileSize != null ? formatFileSize(opts.fileSize) : "";

  // Wire download button
  const dlBtn = overlay.querySelector(".ufp-download-btn");
  dlBtn.onclick = () => downloadFile(filePath, fileName);

  // Show loading
  const loadingEl = overlay.querySelector(".ufp-loading");
  const imageContainer = overlay.querySelector(".ufp-image-container");
  const contentEl = overlay.querySelector(".ufp-content");
  const imgEl = overlay.querySelector(".ufp-image");
  const codeEl = contentEl.querySelector("code");

  loadingEl.style.display = "";
  imageContainer.style.display = "none";
  contentEl.style.display = "none";

  // Revoke previous object URL
  if (currentObjectURL) {
    URL.revokeObjectURL(currentObjectURL);
    currentObjectURL = null;
  }

  try {
    const url = `/download_work_dir_file?path=${encodeURIComponent(filePath)}`;
    const response = await fetchApi(url);

    if (!response.ok) {
      codeEl.textContent = `Error loading file: HTTP ${response.status}`;
      contentEl.style.display = "";
      return;
    }

    if (previewType === "image") {
      const blob = await response.blob();
      currentObjectURL = URL.createObjectURL(blob);
      imgEl.src = currentObjectURL;
      imgEl.alt = fileName;
      imageContainer.style.display = "";
    } else {
      codeEl.textContent = await response.text();
      contentEl.style.display = "";
    }

    // Update size from response if not provided
    if (opts.fileSize == null) {
      const cl = response.headers.get("content-length");
      if (cl) overlay.querySelector(".ufp-size").textContent = formatFileSize(parseInt(cl, 10));
    }
  } catch (err) {
    codeEl.textContent = `Error loading file: ${err.message}`;
    contentEl.style.display = "";
  } finally {
    loadingEl.style.display = "none";
  }
}

/**
 * Close the preview overlay.
 */
export function closePreview() {
  if (!overlayEl) return;
  overlayEl.style.display = "none";
  document.removeEventListener("keydown", overlayEl._keyHandler);

  if (currentObjectURL) {
    URL.revokeObjectURL(currentObjectURL);
    currentObjectURL = null;
  }
}

/**
 * Download a file by path.
 */
async function downloadFile(filePath, fileName) {
  try {
    const url = `/download_work_dir_file?path=${encodeURIComponent(filePath)}`;
    const response = await fetchApi(url);
    if (!response.ok) {
      window.toastFrontendError?.(`Download failed: ${response.status}`, "Download Error");
      return;
    }
    const blob = await response.blob();
    const dlUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = dlUrl;
    a.download = fileName || filePath.split("/").pop();
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(dlUrl);
  } catch (err) {
    window.toastFrontendError?.(`Download error: ${err.message}`, "Download Error");
  }
}

// ── Global registration ─────────────────────────────────────────────────

window.showFilePreview = showFilePreview;
window.closeFilePreview = closePreview;
window.isFilePreviewable = isPreviewable;
