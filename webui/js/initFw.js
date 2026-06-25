import * as initializer from "./initializer.js";
import * as _modals from "./modals.js";
import * as _components from "./components.js";

// initialize required elements
await initializer.initialize();

// Import settings module BEFORE Alpine so event listeners are registered
// This ensures settingsModal and historySettings components are available when Alpine starts
import { registerSettings } from "./settings.js";

// Use the globally loaded Alpine
const Alpine = window.Alpine;

// add x-destroy directive to alpine
Alpine.directive(
  "destroy",
  (el, { expression }, { evaluateLater, cleanup }) => {
    const onDestroy = evaluateLater(expression);
    cleanup(() => onDestroy());
  }
);

// add x-create directive to alpine
Alpine.directive("create", (_el, { expression }, { evaluateLater }) => {
  const onCreate = evaluateLater(expression);
  onCreate();
});

// initialize global tooltip store
import { store as tooltipsStore } from "./tooltips-store.js";

// Explicitly register settings components before Alpine starts
// This guarantees the settingsModal data is available when Alpine processes the DOM
registerSettings();

// Manually start Alpine - use the real start() saved by our auto-start prevention patch
// in index.html. If the patch didn't run, fall back to Alpine.start().
const realStart = window._alpineRealStart || Alpine.start.bind(Alpine);
realStart();
console.log('[initFw.js] Alpine started after component registration');
