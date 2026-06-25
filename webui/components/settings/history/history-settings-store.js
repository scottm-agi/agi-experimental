import { callJsonApi } from '../../../js/api.js';

export default function historySettings() {
    return {
        chats: [],
        loading: false,
        deleting: false,
        searchQuery: '',
        selectedChats: new Set(),
        pollingInterval: null,
        sortField: 'updated_at',
        sortDirection: 'desc',

        get filteredChats() {
            let result = this.chats;
            if (this.searchQuery) {
                const query = this.searchQuery.toLowerCase();
                result = result.filter(chat =>
                    (chat.name && chat.name.toLowerCase().includes(query)) ||
                    (chat.ctxid && chat.ctxid.toLowerCase().includes(query))
                );
            }
            // Apply sorting
            const field = this.sortField;
            const dir = this.sortDirection === 'asc' ? 1 : -1;
            return [...result].sort((a, b) => {
                let valA = a[field] || '';
                let valB = b[field] || '';
                if (field === 'updated_at') {
                    valA = new Date(valA || 0).getTime();
                    valB = new Date(valB || 0).getTime();
                    return (valA - valB) * dir;
                }
                return String(valA).localeCompare(String(valB)) * dir;
            });
        },

        sortBy(field) {
            if (this.sortField === field) {
                this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                this.sortField = field;
                this.sortDirection = field === 'updated_at' ? 'desc' : 'asc';
            }
        },

        get allSelected() {
            return this.filteredChats.length > 0 && this.filteredChats.every(chat => this.selectedChats.has(chat.ctxid));
        },

        async init() {
            await this.fetchChats();
            this.startPolling();
        },

        async fetchChats() {
            const showLoading = !this.pollingInterval || this.chats.length === 0;
            if (showLoading) this.loading = true;
            try {
                // We use chats/list if available, or fetch from /chats relative to sidebar
                // In this system, we can use the root store's contexts if they are loaded,
                // but for settings we might want a fresh list from disk.
                // Assuming we have an api to list all chats.
                const response = await callJsonApi('/chat_list', {}); // Need to verify if this exists or create it
                if (response.success) {
                    this.chats = response.chats.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
                }
            } catch (error) {
                console.error('Failed to fetch chats:', error);
            } finally {
                if (showLoading) this.loading = false;
            }
        },

        startPolling() {
            if (this.pollingInterval) return;
            console.log('[history-settings-store.js] Starting history polling...');
            this.pollingInterval = setInterval(() => {
                this.fetchChats();
            }, 30000); // 30 seconds
        },

        stopPolling() {
            if (this.pollingInterval) {
                console.log('[history-settings-store.js] Stopping history polling...');
                clearInterval(this.pollingInterval);
                this.pollingInterval = null;
            }
        },

        toggleSelect(ctxid) {
            if (this.selectedChats.has(ctxid)) {
                this.selectedChats.delete(ctxid);
            } else {
                this.selectedChats.add(ctxid);
            }
        },

        selectAll() {
            if (this.allSelected) {
                this.filteredChats.forEach(chat => this.selectedChats.delete(chat.ctxid));
            } else {
                this.filteredChats.forEach(chat => this.selectedChats.add(chat.ctxid));
            }
        },

        async deleteSingle(ctxid) {
            if (this.deleting) return;
            const confirmed = await Alpine.store('confirmation').confirm(
                'Are you sure you want to delete this chat? This action cannot be undone.',
                { target: document.activeElement }
            );
            if (!confirmed) return;

            this.deleting = true;
            try {
                const response = await callJsonApi('/chat_remove', { context: ctxid });
                if (response.success) {
                    this.chats = this.chats.filter(c => c.ctxid !== ctxid);
                    this.selectedChats.delete(ctxid);
                }
            } catch (error) {
                console.error('Failed to delete chat:', error);
            } finally {
                this.deleting = false;
            }
        },

        async deleteSelected() {
            if (this.deleting) return;
            const count = this.selectedChats.size;
            const confirmed = await Alpine.store('confirmation').confirm(
                `Are you sure you want to delete ${count} selected chats? This action cannot be undone.`,
                { target: document.activeElement }
            );
            if (!confirmed) return;

            this.deleting = true;
            try {
                const response = await callJsonApi('/chat_remove_bulk', { contexts: Array.from(this.selectedChats) });
                if (response.success) {
                    const deletedIds = new Set(response.removed);
                    this.chats = this.chats.filter(c => !deletedIds.has(c.ctxid));
                    this.selectedChats.clear();
                }
            } catch (error) {
                console.error('Failed to delete selected chats:', error);
            } finally {
                this.deleting = false;
            }
        },

        async deleteAll() {
            if (this.deleting) return;
            const confirmed = await Alpine.store('confirmation').confirm(
                'Are you sure you want to delete ALL chats? This is highly destructive and permanent.',
                { title: 'Delete All Chats', target: document.activeElement }
            );
            if (!confirmed) return;

            this.deleting = true;
            try {
                const allIds = this.chats.map(c => c.ctxid);
                const response = await callJsonApi('/chat_remove_bulk', { contexts: allIds });
                if (response.success) {
                    this.chats = [];
                    this.selectedChats.clear();
                }
            } catch (error) {
                console.error('Failed to delete all chats:', error);
            } finally {
                this.deleting = false;
            }
        },

        formatDate(dateStr) {
            if (!dateStr) return 'Unknown';
            const date = new Date(dateStr);
            return date.toLocaleString();
        }
    };
}
