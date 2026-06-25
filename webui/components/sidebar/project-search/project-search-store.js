import { createStore } from "../../../js/AlpineStore.js";
import * as api from "../../../js/api.js";
import { store as projectsStore } from "../../projects/projects-store.js";

const model = {
  // State
  projectList: [],
  selectedProjectFilter: null, // null = "All Projects", otherwise project name
  searchQuery: "",
  isDropdownOpen: false,
  loading: false,

  // Initialize from localStorage
  init() {
    const savedFilter = localStorage.getItem("projectFilter");
    if (savedFilter) {
      this.selectedProjectFilter = savedFilter === "null" ? null : savedFilter;
    }
    this.loadProjectsList();
  },

  // Load projects list from backend
  async loadProjectsList() {
    this.loading = true;
    try {
      const response = await api.callJsonApi("projects", {
        action: "list",
      });
      this.projectList = response.data || [];
    } catch (error) {
      console.error("Error loading projects list:", error);
    } finally {
      this.loading = false;
    }
  },

  // Get filtered projects based on search query
  getFilteredProjects() {
    if (!this.searchQuery.trim()) {
      return this.projectList;
    }
    const query = this.searchQuery.toLowerCase();
    return this.projectList.filter(
      (project) =>
        project.title?.toLowerCase().includes(query) ||
        project.name?.toLowerCase().includes(query)
    );
  },

  // Get display name for current selection
  getSelectedDisplayName() {
    if (!this.selectedProjectFilter) {
      return "All Projects";
    }
    const project = this.projectList.find(
      (p) => p.name === this.selectedProjectFilter
    );
    return project?.title || this.selectedProjectFilter;
  },

  // Get color for current selection
  getSelectedColor() {
    if (!this.selectedProjectFilter) {
      return null;
    }
    const project = this.projectList.find(
      (p) => p.name === this.selectedProjectFilter
    );
    return project?.color || null;
  },

  // Select a project filter
  selectProject(projectName) {
    this.selectedProjectFilter = projectName;
    this.isDropdownOpen = false;
    this.searchQuery = "";

    // Persist to localStorage
    localStorage.setItem(
      "projectFilter",
      projectName === null ? "null" : projectName
    );

    // Dispatch event for chats store to react
    window.dispatchEvent(
      new CustomEvent("project-filter-changed", {
        detail: { projectName },
      })
    );
  },

  // Clear filter (show all projects)
  clearFilter() {
    this.selectProject(null);
  },

  // Toggle dropdown
  toggleDropdown() {
    this.isDropdownOpen = !this.isDropdownOpen;
    if (this.isDropdownOpen) {
      this.loadProjectsList();
    }
  },

  // Close dropdown
  closeDropdown() {
    this.isDropdownOpen = false;
    this.searchQuery = "";
  },

  // Open create project modal
  openCreateProjectModal() {
    this.closeDropdown();
    if (projectsStore && typeof projectsStore.openCreateModal === 'function') {
      projectsStore.openCreateModal();
    } else if (window.Alpine && window.Alpine.store('projects')) {
      window.Alpine.store('projects').openCreateModal();
    }
  },
};

const store = createStore("projectSearch", model);

export { store };
