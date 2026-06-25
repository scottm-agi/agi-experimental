/**
 * DOM Utilities for Chat UI
 */

/**
 * Insert a fragment or element into chat history at the correct sequence-based position.
 * @param {HTMLElement|DocumentFragment} content - The content to insert (fragment or group)
 * @param {number} sequenceId - The sequence ID used for ordering
 * @returns {HTMLElement|null} The next message element if one was found for insertion
 */
export function insertContentToChatHistory(content, sequenceId) {
    const chatHistory = document.getElementById('chat-history');
    if (!chatHistory) return null;

    // Standardize sequenceId for comparison
    const targetSeq = parseInt(sequenceId) || 0;

    // Find all siblings (message groups)
    const siblings = Array.from(chatHistory.children).filter(el => el.classList.contains('message-group'));

    // Find the first group that should come AFTER our target content
    const nextSibling = siblings.find(el => {
        const sSeq = parseInt(el.dataset.msgSequence) || parseInt(el.dataset.msgIndex) || 0;
        // Strictly after if sequence is higher
        return sSeq > targetSeq;
    });

    if (nextSibling) {
        chatHistory.insertBefore(content, nextSibling);
    } else {
        // If no group is after, append to the end
        chatHistory.appendChild(content);
    }

    return nextSibling;
}
