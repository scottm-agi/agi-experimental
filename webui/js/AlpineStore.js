// Track all created stores
const stores = new Map();

/**
 * Creates a store that can be used to share state between components.
 * Uses initial state object and returns a proxy to it that uses Alpine when initialized
 * @template T
 * @param {string} name
 * @param {T} initialState
 * @returns {T}
 */
export function createStore(name, initialState) {
  const proxy = new Proxy(initialState, {
    set(target, prop, value) {
      const store = globalThis.Alpine?.store(name);
      if (store) store[prop] = value;
      else target[prop] = value;
      return true;
    },
    get(target, prop) {
      const store = globalThis.Alpine?.store(name);
      if (store) return store[prop];
      return target[prop];
    }
  });

  if (globalThis.Alpine) {
    globalThis.Alpine.store(name, initialState);
  } else {
    const initStore = () => {
      if (globalThis.Alpine && !globalThis.Alpine.store(name)) {
        globalThis.Alpine.store(name, initialState);
        return true;
      }
      return false;
    };

    document.addEventListener("alpine:init", initStore);

    // Fallback in case alpine:init was already fired or missed
    const interval = setInterval(() => {
      if (initStore()) clearInterval(interval);
    }, 50);
    // Stop checking after 2 seconds
    setTimeout(() => clearInterval(interval), 2000);
  }

  // Store the proxy
  stores.set(name, proxy);

  return /** @type {T} */ (proxy); // explicitly cast for linter support
}

/**
 * Get an existing store by name
 * @template T
 * @param {string} name
 * @returns {T | undefined}
 */
export function getStore(name) {
  return /** @type {T | undefined} */ (stores.get(name));
}