import { callJsonApi } from '../../../js/api.js';

export default function commonPromptsSettings() {
    return {
        prompts: [],
        loading: false,
        searchQuery: '',
        selectedPrompts: new Set(),
        pollingInterval: null,

        get filteredPrompts() {
            if (!this.searchQuery) return this.prompts;
            const query = this.searchQuery.toLowerCase();
            return this.prompts.filter(p =>
                p.prompt.toLowerCase().includes(query) ||
                (p.filename && p.filename.toLowerCase().includes(query))
            );
        },

        get allSelected() {
            return this.filteredPrompts.length > 0 && this.filteredPrompts.every(p => this.selectedPrompts.has(p.filename));
        },

        async init() {
            await this.fetchPrompts();
            this.startPolling();
        },

        async fetchPrompts() {
            const showLoading = !this.pollingInterval || this.prompts.length === 0;
            if (showLoading) this.loading = true;
            try {
                const response = await callJsonApi('/api/prompts/golden/list', {});
                if (response.success) {
                    this.prompts = response.prompts;
                }
            } catch (error) {
                console.error('Failed to fetch common prompts:', error);
            } finally {
                if (showLoading) this.loading = false;
            }
        },

        startPolling() {
            if (this.pollingInterval) return;
            console.log('[common-prompts-store.js] Starting prompts polling...');
            this.pollingInterval = setInterval(() => {
                this.fetchPrompts();
            }, 30000); // 30 seconds
        },

        stopPolling() {
            if (this.pollingInterval) {
                console.log('[common-prompts-store.js] Stopping prompts polling...');
                clearInterval(this.pollingInterval);
                this.pollingInterval = null;
            }
        },

        toggleSelect(filename) {
            if (this.selectedPrompts.has(filename)) {
                this.selectedPrompts.delete(filename);
            } else {
                this.selectedPrompts.add(filename);
            }
        },

        selectAll() {
            if (this.allSelected) {
                this.filteredPrompts.forEach(p => this.selectedPrompts.delete(p.filename));
            } else {
                this.filteredPrompts.forEach(p => this.selectedPrompts.add(p.filename));
            }
        },

        async deleteSingle(prompt_text) {
            const confirmed = await Alpine.store('confirmation').confirm(
                'Are you sure you want to delete this common prompt? This action cannot be undone.',
                { target: document.activeElement }
            );
            if (!confirmed) return;

            try {
                const promptObj = this.prompts.find(p => p.prompt === prompt_text);
                const response = await callJsonApi('/api/prompts/common/delete', { 
                    prompt: prompt_text,
                    filename: promptObj?.filename || ''
                });
                if (response.success) {
                    showToast(response.message, "success");
                    await this.fetchPrompts();
                }
            } catch (error) {
                console.error('Failed to delete prompt:', error);
                showToast("Failed to delete prompt", "error");
            }
        },

        async deleteSelected() {
            const count = this.selectedPrompts.size;
            const confirmed = await Alpine.store('confirmation').confirm(
                `Are you sure you want to delete ${count} selected common prompts? This action cannot be undone.`,
                { target: document.activeElement }
            );
            if (!confirmed) return;

            try {
                for (const filename of this.selectedPrompts) {
                    const promptObj = this.prompts.find(p => p.filename === filename);
                    if (promptObj) {
                        await callJsonApi('/api/prompts/common/delete', { 
                            prompt: promptObj.prompt,
                            filename: promptObj.filename || ''
                        });
                    }
                }
                showToast(`Deleted ${count} prompts`, "success");
                this.selectedPrompts.clear();
                await this.fetchPrompts();
            } catch (error) {
                console.error('Failed to delete selected prompts:', error);
                showToast("Failed to delete selected prompts", "error");
            }
        },

        formatDate(timestamp) {
            if (!timestamp) return 'Unknown';
            const date = new Date(timestamp * 1000);
            return date.toLocaleString();
        },

        truncate(text, len = 100) {
            if (!text) return "";
            if (text.length <= len) return text;
            return text.substring(0, len) + "...";
        }
    };
}

