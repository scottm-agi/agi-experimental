document.addEventListener('alpine:init', () => {
    Alpine.data('kvpEditor', (config) => ({
        // config: { field: fieldObject, format: 'json'|'env', isSecret: boolean }
        field: config.field,
        format: config.format || 'json',
        isSecret: config.isSecret || false,
        view: 'list', // 'list' or 'raw'
        items: [],
        showSecrets: {}, // toggle visibility for individual secrets
        sensitivePatterns: ["KEY", "TOKEN", "PASSWORD", "PWD", "SECRET", "AUTH", "CREDENTIAL", "APIKEY", "ACCESS", "PRIVATE", "CERT", "JSON", "SESSION", "PAT", "SID", "JWT"],

        init() {
            this.parseRaw();
            // Watch for changes in the underlying field value (from raw view or external)
            this.$watch('field.value', (val) => {
                if (this.view === 'raw') {
                    this.parseRaw();
                }
            });
        },

        parseRaw() {
            const rawValue = this.field.value || '';
            if (this.format === 'json') {
                try {
                    const parsed = JSON.parse(rawValue);
                    this.items = Object.entries(parsed).map(([key, value]) => ({ key, value }));
                } catch (e) {
                    // If invalid JSON, we might be in the middle of editing or it's empty
                    if (rawValue.trim()) {
                        console.warn("KVP Editor: Invalid JSON, staying in raw view if possible");
                    }
                    this.items = [];
                }
            } else {
                // Parse .env format
                const lines = rawValue.split('\n');
                this.items = lines
                    .map(line => line.trim())
                    .filter(line => line && !line.startsWith('#'))
                    .map(line => {
                        const idx = line.indexOf('=');
                        if (idx === -1) return null;
                        const key = line.substring(0, idx).trim();
                        let value = line.substring(idx + 1).trim();
                        // remove quotes
                        if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
                            value = value.substring(1, value.length - 1);
                        }
                        return { key, value };
                    })
                    .filter(Boolean);
            }
        },

        serializeItems() {
            // Only serialize if we have actual items with keys
            // This prevents overwriting existing saved data with empty values
            const hasValidItems = this.items.some(item => item.key && item.key.trim());

            if (!hasValidItems) {
                // Don't overwrite field.value if items are empty
                // The existing value should be preserved
                return;
            }

            // FIX: Settings polling may have replaced the field object.
            // Look up the current field from settings by ID to ensure we write to the correct object.
            let targetField = this.field;
            const fieldId = this.field?.id;
            if (fieldId && window.Alpine) {
                const settingsEl = document.getElementById('settingsModal');
                if (settingsEl) {
                    const settingsData = Alpine.$data(settingsEl);
                    if (settingsData?.settings?.sections) {
                        for (const section of settingsData.settings.sections) {
                            for (const f of section.fields || []) {
                                if (f.id === fieldId) {
                                    targetField = f;
                                    // Also update our cached reference
                                    this.field = f;
                                    break;
                                }
                            }
                        }
                    }
                }
            }

            if (this.format === 'json') {
                const obj = {};
                this.items.forEach(item => {
                    if (item.key.trim()) {
                        obj[item.key.trim()] = item.value;
                    }
                });
                targetField.value = JSON.stringify(obj, null, 4);
            } else {
                targetField.value = this.items
                    .filter(item => item.key.trim())
                    .map(item => `${item.key.trim()}="${item.value}"`)
                    .join('\n');
            }
        },


        toggleView() {
            if (this.view === 'list') {
                this.serializeItems();
                this.view = 'raw';
            } else {
                this.parseRaw();
                this.view = 'list';
            }
        },

        addItem() {
            this.items.push({ key: '', value: '' });
        },

        removeItem(index) {
            this.items.splice(index, 1);
            this.serializeItems();
        },

        onItemChange() {
            if (this.view === 'list') {
                // Use a small delay to ensure Alpine has updated the item value
                // and to debounce rapid typing
                if (this._serializeTimeout) clearTimeout(this._serializeTimeout);
                this._serializeTimeout = setTimeout(() => {
                    this.serializeItems();
                }, 100);
            }
        },

        toggleSecretVisibility(index) {
            this.showSecrets[index] = !this.showSecrets[index];
        },

        isSensitiveKey(key) {
            if (!key) return false;
            const k = key.toUpperCase().trim();
            return this.sensitivePatterns.some(p => k.includes(p));
        }
    }));
});
