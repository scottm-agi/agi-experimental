import { createStore } from "../../js/AlpineStore.js";

// This store manages the visibility and state of the main sidebar panel.
const model = {
  isOpen: true,
  preferencesOpen: true, // Controlled by Cog/Preferences toggle
  _initialized: false,

  // Centralized collapse state for all sidebar sections (persisted in localStorage)
  sectionStates: {
    tasks: false,       // default: collapsed (Chat Queue)
    scheduled: false,   // default: collapsed (Scheduled Tasks)
    preferences: false, // default: collapsed
    actions: true,      // default: expanded for visibility
    chats: true         // default: expanded
  },

  // Initialize the store by setting up a resize listener
  // Guard ensures this runs only once, even if called from multiple components
  init() {
    if (this._initialized) return;
    this._initialized = true;

    this.loadSectionStates();
    this.loadWidth(); // Load persisted width
    this.handleResize();
    this.resizeHandler = () => this.handleResize();
    window.addEventListener("resize", this.resizeHandler);

    // Bind resize handlers ensuring 'this' context
    this.resizeHandlerMouseMove = (e) => this.handleResizeMove(e);
    this.resizeHandlerMouseUp = () => this.stopResize();
  },

  // Load section collapse states from localStorage
  loadSectionStates() {
    try {
      const stored = localStorage.getItem('sidebarSections');
      if (stored) {
        this.sectionStates = { ...this.sectionStates, ...JSON.parse(stored) };
      }
    } catch (e) {
      console.error('Failed to load sidebar section states', e);
    }
  },

  // Persist section states to localStorage
  persistSectionStates() {
    try {
      localStorage.setItem('sidebarSections', JSON.stringify(this.sectionStates));
    } catch (e) {
      console.error('Failed to persist section states', e);
    }
  },

  // Check if a section should be open (used by x-init in templates)
  isSectionOpen(name) {
    return this.sectionStates[name] === true;
  },

  // Toggle and persist a section's open state (drives Bootstrap programmatically via components)
  toggleSection(name) {
    if (!(name in this.sectionStates)) return;
    this.sectionStates[name] = !this.sectionStates[name];
    this.persistSectionStates();
  },

  // Cleanup method for lifecycle management
  destroy() {
    if (this.resizeHandler) {
      window.removeEventListener("resize", this.resizeHandler);
      this.resizeHandler = null;
    }
    this._initialized = false;
  },

  // Toggle the sidebar's visibility
  toggle() {
    this.isOpen = !this.isOpen;
  },

  // Close the sidebar, e.g., on overlay click on mobile
  close() {
    if (this.isMobile()) {
      this.isOpen = false;
    }
  },

  // Handle browser resize to show/hide sidebar based on viewport width
  handleResize() {
    this.isOpen = !this.isMobile();
  },

  // Check if the current viewport is mobile
  isMobile() {
    return window.innerWidth <= 768;
  },

  // --- Resizable Sidebar Logic ---
  width: 260, // Default width
  minWidth: 200,
  maxWidth: 600,
  isResizing: false,

  startResize(event) {
    this.isResizing = true;
    // Prevent text selection during resize
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';

    // Attach listeners to window to handle drag outside the handle
    window.addEventListener('mousemove', this.resizeHandlerMouseMove);
    window.addEventListener('mouseup', this.resizeHandlerMouseUp);
  },

  handleResizeMove(event) {
    if (!this.isResizing) return;

    // Calculate new width based on mouse position
    let newWidth = event.clientX;

    // Constrain width
    if (newWidth < this.minWidth) newWidth = this.minWidth;
    if (newWidth > this.maxWidth) newWidth = this.maxWidth;

    this.width = newWidth;
  },

  stopResize() {
    if (!this.isResizing) return;
    this.isResizing = false;
    document.body.style.userSelect = '';
    document.body.style.cursor = '';

    // Remove listeners
    window.removeEventListener('mousemove', this.resizeHandlerMouseMove);
    window.removeEventListener('mouseup', this.resizeHandlerMouseUp);

    // Persist new width
    this.persistWidth();
  },

  // Load width from localStorage
  loadWidth() {
    const stored = localStorage.getItem('sidebarWidth');
    if (stored) {
      const parsed = parseInt(stored);
      if (!isNaN(parsed) && parsed >= this.minWidth && parsed <= this.maxWidth) {
        this.width = parsed;
      }
    }
  },

  persistWidth() {
    localStorage.setItem('sidebarWidth', this.width);
  },

  // Bound handlers for add/removeEventListener
  resizeHandlerMouseMove: null,
  resizeHandlerMouseUp: null,
};

// Initialize handlers outside to access 'model' scope if needed, 
// but here we can bind 'this' in init() if the object structure allows, 
// or simpler: assign them in init.


export const store = createStore("sidebar", model);
