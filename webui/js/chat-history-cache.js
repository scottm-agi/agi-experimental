/**
 * Chat history cache management
 * Handles persisting and restoring chat messages from localStorage
 */

import { insertContentToChatHistory } from "./dom-utils.js";
import { isMessageEmpty } from "./messages.js";

const CACHE_PREFIX = 'chat_history_v2_';
const CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours
const MAX_CACHED_CONTEXTS = 50;
const MAX_MESSAGES_PER_CONTEXT = 200;

let currentRenderId = 0;

/**
 * Check if a context has any cached messages
 * @param {string} contextId
 * @returns {boolean}
 */
export function hasCachedMessages(contextId) {
    if (!contextId) return false;
    const key = CACHE_PREFIX + contextId;
    const cached = localStorage.getItem(key);
    return !!cached;
}

/**

 * Extract essential message data for caching
 * @param {HTMLElement} element - The message DOM element
 * @returns {Object|null} Cached message object or null if invalid
 */
function extractMessageData(element) {
    if (!element || !element.id) return null;

    try {
        const id = element.id.replace('message-', '');
        const type = element.getAttribute('data-type');
        const headingEl = element.querySelector('.message-heading-text');
        const contentEl = element.querySelector('.message-content');

        // Extract timestamp from dataset if available
        const timestamp = element.dataset.timestamp || null;
        const sequence_id = parseInt(element.dataset.sequenceId || "0", 10);
        const hash = element.dataset.hash || "";

        return {
            id,
            type,
            heading: headingEl ? headingEl.innerText : '',
            content: contentEl ? contentEl.innerHTML : '',
            timestamp,
            sequence_id,
            hash,
            temp: element.classList.contains('message-temp')
        };
    } catch (e) {
        console.warn('[ChatCache] Error extracting message data:', e);
        return null;
    }
}

/**
 * Save chat history to cache
 * @param {string} contextId - The current context ID
 * @param {string} guid - The current log GUID
 * @param {number} version - The current log version
 */
export function saveChatHistoryToCache(contextId, guid = null, version = null) {
    if (!contextId) return;

    try {
        const chatHistory = document.getElementById('chat-history');
        if (!chatHistory) return;

        // Get all individual messages (not containers or groups)
        const messages = Array.from(chatHistory.querySelectorAll('.message-container'))
            .map(extractMessageData)
            .filter(msg => msg !== null);

        if (messages.length === 0) return;

        const key = CACHE_PREFIX + contextId;

        // Try to merge with existing cache if metadata is missing
        let existingCache = null;
        try {
            const cached = localStorage.getItem(key);
            if (cached) existingCache = JSON.parse(cached);
        } catch (e) { }

        const cacheData = {
            messages: messages.slice(-MAX_MESSAGES_PER_CONTEXT), // Keep last N messages
            timestamp: Date.now(),
            guid: guid || (existingCache ? existingCache.guid : null),
            version: version !== null ? version : (existingCache ? existingCache.version : 0)
        };

        localStorage.setItem(key, JSON.stringify(cacheData));
        // console.log(`[ChatCache] Saved ${cacheData.messages.length} messages for context ${contextId}`);
    } catch (e) {
        if (e.name === 'QuotaExceededError') {
            console.warn('[ChatCache] LocalStorage quota exceeded, cleaning up oldest entries');
            cleanupOldCacheEntries();
            // Try one more time with fewer messages
            try {
                const key = CACHE_PREFIX + contextId;
                const cached = localStorage.getItem(key);
                const data = cached ? JSON.parse(cached) : { messages: [] };
                data.messages = data.messages.slice(-50);
                localStorage.setItem(key, JSON.stringify(data));
            } catch (retryError) { }
        } else {
            console.warn('[ChatCache] Failed to save cache:', e);
        }
    }
}

/**
 * Load chat history from cache
 * @param {string} contextId - The context ID to load for
 * @param {boolean} returnRaw - Whether to return the raw cache object (with metadata)
 * @returns {Array|Object|null} Array of messages, raw cache object, or null
 */
export function loadChatHistoryFromCache(contextId, returnRaw = false) {
    if (!contextId) return null;

    try {
        const key = CACHE_PREFIX + contextId;
        const cached = localStorage.getItem(key);
        if (!cached) return null;

        const data = JSON.parse(cached);

        // Check TTL
        if (Date.now() - data.timestamp > CACHE_TTL_MS) {
            localStorage.removeItem(key);
            console.log(`[ChatCache] Cache expired for context ${contextId}`);
            return null;
        }

        console.log(`[ChatCache] Loaded ${data.messages.length} messages from cache for context ${contextId}`);
        return returnRaw ? data : data.messages;
    } catch (e) {
        console.warn('[ChatCache] Failed to load cache:', e);
        return null;
    }
}

/**
 * Render cached messages to the DOM
 * @param {Array} messages - Array of message objects
 * @param {Function} setMessageFn - The function used to render messages
 */
export function renderCachedMessages(messages, setMessageFn) {
    if (!messages || messages.length === 0) return;

    // Sort messages by sequence_id to ensure correct order, then filter empty
    const sortedMessages = [...(messages || [])].sort((a, b) =>
        (a.sequence_id || 0) - (b.sequence_id || 0)
    );
    const filteredMessages = sortedMessages.filter(msg => {
        return !isMessageEmpty(msg.type, msg.heading, msg.content, msg.kvps);
    });

    if (filteredMessages.length === 0) return;

    const renderId = ++currentRenderId;
    console.log(`[DEBUG_CACHE] Rendering ${filteredMessages.length} cached messages (ID: ${renderId}) for current context`);

    // Optimization: Render messages in chunks to avoid UI hang
    const MAX_FRAME_TIME = 16; // ms
    let index = 0;

    function renderNextChunk() {
        const startTime = performance.now();
        // Check if this rendering task has been cancelled
        if (renderId !== currentRenderId) {
            console.log(`[ChatCache] Rendering cancelled (ID: ${renderId})`);
            return;
        }

        const fragment = document.createDocumentFragment();

        // Time-budgeted loop: Process messages until we hit the time limit
        while (index < filteredMessages.length && (performance.now() - startTime) < MAX_FRAME_TIME) {
            const msg = filteredMessages[index];
            if (msg && msg.id) {
                setMessageFn(
                    msg.id,
                    msg.type,
                    msg.heading || '',
                    msg.content || '', // This line was 'msg.content || '','
                    msg.temp || false,
                    msg.icon || null,
                    msg.kvps || null,
                    msg.timestamp || null,
                    fragment,
                    false, // isSummary
                    false, // verbose
                    msg.sequence_id || 0,
                    msg.hash || ""
                );
            }
            index++;

            // If a single message took more than our budget, we break and handle it in the next frame
            if ((performance.now() - startTime) >= MAX_FRAME_TIME) break;
        }

        // Batch append the fragment to the DOM
        const chatHistory = document.getElementById('chat-history');
        if (chatHistory && fragment.childNodes.length > 0) {
            chatHistory.appendChild(fragment);
        }

        if (index < filteredMessages.length) {
            // Use requestAnimationFrame for more consistent rendering
            requestAnimationFrame(renderNextChunk);
        }
    }

    renderNextChunk();
}

/**
 * Clear cache for a specific context
 * @param {string} contextId - The context ID to clear cache for
 */
export function clearCacheForContext(contextId) {
    if (!contextId) return;

    try {
        const key = CACHE_PREFIX + contextId;
        localStorage.removeItem(key);
        console.log(`[ChatCache] Cleared cache for context ${contextId}`);
    } catch (e) {
        console.warn('[ChatCache] Failed to clear cache:', e);
    }
}

/**
 * Clean up old/expired cache entries
 */
export function cleanupOldCacheEntries() {
    try {
        const keysToRemove = [];
        const allCacheEntries = [];

        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (!key || !key.startsWith(CACHE_PREFIX)) continue;

            try {
                const data = JSON.parse(localStorage.getItem(key));

                // Remove expired entries
                if (Date.now() - data.timestamp > CACHE_TTL_MS) {
                    keysToRemove.push(key);
                } else {
                    allCacheEntries.push({ key, timestamp: data.timestamp });
                }
            } catch (e) {
                // Invalid JSON, remove it
                keysToRemove.push(key);
            }
        }

        // Remove expired/invalid entries
        keysToRemove.forEach(key => localStorage.removeItem(key));

        // If we have too many entries, remove oldest ones
        if (allCacheEntries.length > MAX_CACHED_CONTEXTS) {
            allCacheEntries.sort((a, b) => a.timestamp - b.timestamp);
            const toRemove = allCacheEntries.slice(0, allCacheEntries.length - MAX_CACHED_CONTEXTS);
            toRemove.forEach(entry => localStorage.removeItem(entry.key));
            console.log(`[ChatCache] Cleaned up ${toRemove.length} old cache entries`);
        }

        if (keysToRemove.length > 0) {
            console.log(`[ChatCache] Removed ${keysToRemove.length} expired/invalid cache entries`);
        }
    } catch (e) {
        console.warn('[ChatCache] Failed to cleanup cache:', e);
    }
}
