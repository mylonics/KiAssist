<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue';
import type { KiCadInstance, RecentProject } from '../types/pywebview';
import RequirementsWizard from './RequirementsWizard.vue';

// Configuration
const MAX_VISIBLE_RECENT = 5;
const REFRESH_INTERVAL_MS = 3000; // 3 seconds

const openProjects = ref<KiCadInstance[]>([]);
const recentProjects = ref<RecentProject[]>([]);
const selectedProject = ref<KiCadInstance | RecentProject | null>(null);
const loading = ref(false);
const error = ref<string>('');
const switcherOpen = ref(false);
const showAllRecent = ref(false);
let refreshTimer: number | null = null;

// Requirements wizard state
const showRequirementsWizard = ref(false);
const requirementsExists = ref(false);
const todoExists = ref(false);
let refreshInFlight = false;

// Refs for click-outside detection
const switcherRef = ref<HTMLElement | null>(null);

async function waitForAPI(maxAttempts = 20, delayMs = 100): Promise<boolean> {
  if (window.pywebview?.api) return true;
  return new Promise((resolve) => {
    const onReady = () => resolve(!!window.pywebview?.api);
    window.addEventListener('pywebviewready', onReady, { once: true });
    // Fallback timeout in case the event was already fired before we listened
    setTimeout(() => {
      window.removeEventListener('pywebviewready', onReady);
      resolve(!!window.pywebview?.api);
    }, maxAttempts * delayMs);
  });
}

async function refreshProjectsList() {
  if (refreshInFlight) return; // Skip if a previous call is still in-flight
  refreshInFlight = true;
  loading.value = true;
  error.value = '';
  
  try {
    const apiAvailable = await waitForAPI();
    
    if (!apiAvailable) {
      error.value = 'pywebview API not available';
      return;
    }
    
    const result = await window.pywebview!.api.get_projects_list();
    
    if (result.success) {
      openProjects.value = result.open_projects;
      recentProjects.value = result.recent_projects;
      
      // Sync selected project with refreshed data so updated paths are reflected
      if (selectedProject.value && 'socket_path' in selectedProject.value) {
        const currentSocket = selectedProject.value.socket_path;
        const updated = result.open_projects.find(
          (p: KiCadInstance) => p.socket_path === currentSocket
        );
        if (updated) {
          selectedProject.value = updated;
        }
      } else if ('path' in (selectedProject.value || {})) {
        // If a recent project was selected, check if it's now open in KiCad
        const currentPath = (selectedProject.value as RecentProject)?.path?.toLowerCase();
        if (currentPath) {
          const nowOpen = result.open_projects.find(
            (p: KiCadInstance) => p.project_path.toLowerCase() === currentPath
          );
          if (nowOpen) {
            selectedProject.value = nowOpen;
          } else {
            // Sync selected recent project with refreshed data (e.g. enriched pcb/schematic paths)
            const updatedRecent = result.recent_projects.find(
              (p: RecentProject) => p.path.toLowerCase() === currentPath
            );
            if (updatedRecent) {
              selectedProject.value = updatedRecent;
            }
          }
        }
      }
      
      // Auto-select if only one open project
      if (result.open_projects.length === 1 && !selectedProject.value) {
        selectedProject.value = result.open_projects[0];
      }
      
      // If no KiCad instance is detected and nothing selected, open the last recent project
      if (result.open_projects.length === 0 && !selectedProject.value && result.recent_projects.length > 0) {
        selectedProject.value = result.recent_projects[0];
      }
    } else {
      error.value = result.error || 'Failed to get projects list';
    }
  } catch (err) {
    // Ignore stale callback errors from pywebview (e.g. during HMR reload)
    const errStr = String(err);
    if (errStr.includes('_returnValuesCallbacks')) {
      console.warn('[KiCadInstanceSelector] Stale pywebview callback (likely HMR reload), ignoring.');
    } else {
      error.value = `Error getting projects: ${err}`;
      console.error('Error getting projects:', err);
    }
  } finally {
    loading.value = false;
    refreshInFlight = false;
  }
}

async function browseForProject() {
  switcherOpen.value = false;
  try {
    const apiAvailable = await waitForAPI();
    if (!apiAvailable) {
      error.value = 'pywebview API not available';
      return;
    }
    
    const result = await window.pywebview!.api.browse_for_project();
    
    if (result.success && result.path) {
      await window.pywebview!.api.add_recent_project(result.path);
      await refreshProjectsList();
      const newProject = recentProjects.value.find(p => p.path === result.path);
      if (newProject) {
        selectProject(newProject, false);
      }
    } else if (!result.cancelled && result.error) {
      error.value = result.error;
    }
  } catch (err) {
    error.value = `Error browsing for project: ${err}`;
    console.error('Error browsing for project:', err);
  }
}

function selectProject(project: KiCadInstance | RecentProject, isOpen: boolean) {
  selectedProject.value = project;
  switcherOpen.value = false;
  
  if (!isOpen && 'path' in project) {
    window.pywebview?.api.add_recent_project(project.path);
  }
}

function toggleSwitcher() {
  switcherOpen.value = !switcherOpen.value;
}

function handleClickOutside(event: MouseEvent) {
  if (switcherRef.value && !switcherRef.value.contains(event.target as Node)) {
    switcherOpen.value = false;
  }
}

function isProjectOpen(project: RecentProject): boolean {
  const normalizedPath = project.path.toLowerCase();
  return openProjects.value.some(
    op => op.project_path.toLowerCase() === normalizedPath
  );
}

function copyError() {
  if (error.value) {
    navigator.clipboard.writeText(error.value);
  }
}

function startRefreshTimer() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = window.setInterval(() => {
    refreshProjectsList();
  }, REFRESH_INTERVAL_MS);
}

function stopRefreshTimer() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

// Helpers
function fileName(fullPath: string): string {
  if (!fullPath) return '';
  const sep = Math.max(fullPath.lastIndexOf('/'), fullPath.lastIndexOf('\\'));
  return sep >= 0 ? fullPath.substring(sep + 1) : fullPath;
}

function getProjectName(project: KiCadInstance | RecentProject): string {
  if ('display_name' in project) {
    return project.display_name || project.project_name;
  }
  return project.name;
}

// Computed properties
const selectedProjectInfo = computed(() => {
  if (!selectedProject.value) return null;
  
  if ('socket_path' in selectedProject.value) {
    const inst = selectedProject.value;
    return {
      name: inst.display_name || inst.project_name,
      projectPath: inst.project_path,
      pcbPath: inst.pcb_path,
      schematicPath: inst.schematic_path,
      isOpen: true,
      pcbOpen: inst.pcb_open ?? false,
      schematicOpen: inst.schematic_open ?? false,
    };
  }
  
  // It's a RecentProject
  const rp = selectedProject.value;
  return {
    name: rp.name,
    projectPath: rp.path,
    pcbPath: rp.pcb_path || '',
    schematicPath: rp.schematic_path || '',
    isOpen: isProjectOpen(rp),
    pcbOpen: false,
    schematicOpen: false,
  };
});

const selectedProjectDir = computed(() => {
  if (!selectedProjectInfo.value) return '';
  const path = selectedProjectInfo.value.projectPath;
  const lastSep = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
  return lastSep > 0 ? path.substring(0, lastSep) : path;
});

const selectedProjectName = computed(() => {
  return selectedProjectInfo.value?.name || 'Project';
});

const switcherLabel = computed(() => {
  if (!selectedProjectInfo.value) return 'Select Project';
  return selectedProjectInfo.value.name;
});

// Dropdown items: open projects not currently selected, then recents
const otherOpenProjects = computed(() => {
  return openProjects.value.filter(p => {
    if (!selectedProject.value) return true;
    if ('socket_path' in selectedProject.value) {
      return p.socket_path !== selectedProject.value.socket_path;
    }
    return true;
  });
});

const switcherRecentProjects = computed(() => {
  // Exclude any recent project that matches a currently open one
  const openPaths = new Set(openProjects.value.map(p => p.project_path.toLowerCase()));
  const sel = selectedProject.value;
  const selPath = sel && 'path' in sel ? (sel as RecentProject).path.toLowerCase() : '';
  return recentProjects.value.filter(p => {
    const norm = p.path.toLowerCase();
    return !openPaths.has(norm) && norm !== selPath;
  });
});

// Check requirements file when project changes
async function checkRequirementsFile() {
  if (!selectedProjectDir.value) {
    requirementsExists.value = false;
    todoExists.value = false;
    return;
  }
  
  try {
    if (window.pywebview?.api) {
      const result = await window.pywebview.api.check_requirements_file(selectedProjectDir.value);
      if (result.success) {
        requirementsExists.value = result.requirements_exists || false;
        todoExists.value = result.todo_exists || false;
      }
    }
  } catch (err) {
    console.error('Error checking requirements file:', err);
  }
}

function openRequirementsWizard() {
  showRequirementsWizard.value = true;
}

function closeRequirementsWizard() {
  showRequirementsWizard.value = false;
}

function onRequirementsSaved(files: string[]) {
  console.log('Requirements saved:', files);
  showRequirementsWizard.value = false;
  checkRequirementsFile();
}

// Watch for project changes to update requirements status and notify backend
watch(selectedProject, async (newProject) => {
  checkRequirementsFile();

  // Notify the backend of the selected project so board/schematic context
  // is included in the system prompt when the user sends a message.
  if (newProject && window.pywebview?.api) {
    const projectPath = 'socket_path' in newProject
      ? newProject.project_path
      : newProject.path;
    if (projectPath) {
      try {
        await window.pywebview.api.set_project_path(projectPath);
      } catch (err) {
        console.error('Failed to set project path on backend:', err);
      }
    }
  }
}, { immediate: true });

onMounted(() => {
  refreshProjectsList();
  startRefreshTimer();
  document.addEventListener('click', handleClickOutside);
});

onUnmounted(() => {
  stopRefreshTimer();
  document.removeEventListener('click', handleClickOutside);
});
</script>

<template>
  <div class="kicad-selector">
    <!-- App Header -->
    <div class="app-header">
      <span class="app-title">KiAssist</span>
      <span v-if="loading" class="loading-dot" title="Refreshing..."></span>
    </div>

    <!-- Project Switcher -->
    <div class="switcher-wrapper" ref="switcherRef">
      <button class="switcher-btn" @click="toggleSwitcher">
        <span class="switcher-label">{{ switcherLabel }}</span>
        <span class="material-icons switcher-arrow">{{ switcherOpen ? 'expand_less' : 'expand_more' }}</span>
      </button>

      <div v-if="switcherOpen" class="switcher-dropdown">
        <!-- Open in KiCad -->
        <div v-if="otherOpenProjects.length > 0" class="dropdown-section">
          <div class="dropdown-section-label">Open in KiCAD</div>
          <button
            v-for="project in otherOpenProjects"
            :key="project.socket_path"
            class="dropdown-item"
            @click="selectProject(project, true)"
          >
            <span class="material-icons dropdown-item-icon open">memory</span>
            <span class="dropdown-item-text">{{ getProjectName(project) }}</span>
          </button>
        </div>

        <!-- Recent Projects -->
        <div v-if="switcherRecentProjects.length > 0" class="dropdown-section">
          <div class="dropdown-section-label">Recent</div>
          <button
            v-for="project in (showAllRecent ? switcherRecentProjects : switcherRecentProjects.slice(0, MAX_VISIBLE_RECENT))"
            :key="project.path"
            class="dropdown-item"
            @click="selectProject(project, false)"
          >
            <span class="material-icons dropdown-item-icon">folder</span>
            <span class="dropdown-item-text">{{ project.name }}</span>
          </button>
          <button
            v-if="switcherRecentProjects.length > MAX_VISIBLE_RECENT"
            class="dropdown-item show-more"
            @click.stop="showAllRecent = !showAllRecent"
          >
            <span class="material-icons dropdown-item-icon">{{ showAllRecent ? 'expand_less' : 'expand_more' }}</span>
            <span class="dropdown-item-text">{{ showAllRecent ? 'Show less' : `${switcherRecentProjects.length - MAX_VISIBLE_RECENT} more...` }}</span>
          </button>
        </div>

        <!-- Empty state -->
        <div v-if="otherOpenProjects.length === 0 && switcherRecentProjects.length === 0" class="dropdown-empty">
          No other projects
        </div>

        <!-- Browse -->
        <div class="dropdown-section dropdown-browse">
          <button class="dropdown-item browse" @click="browseForProject">
            <span class="material-icons dropdown-item-icon">folder_open</span>
            <span class="dropdown-item-text">Browse for Project...</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Error -->
    <div v-if="error" class="error-message">
      <span class="material-icons error-icon">warning</span>
      <span class="error-text">{{ error }}</span>
      <button @click="copyError" class="copy-btn" title="Copy error">
        <span class="material-icons">content_copy</span>
      </button>
    </div>

    <!-- Project Status Rows -->
    <div v-if="selectedProjectInfo" class="status-panel">
      <!-- Project row -->
      <div class="status-row" :title="selectedProjectInfo.projectPath">
        <span class="status-dot" :class="{ active: selectedProjectInfo.isOpen }"></span>
        <span class="status-label">Project</span>
        <span class="status-value">{{ selectedProjectInfo.projectPath ? fileName(selectedProjectInfo.projectPath) : '—' }}</span>
      </div>

      <!-- PCB row -->
      <div class="status-row" :title="selectedProjectInfo.pcbPath">
        <span class="status-dot" :class="{ active: selectedProjectInfo.pcbOpen }"></span>
        <span class="status-label">PCB</span>
        <span class="status-value">{{ selectedProjectInfo.pcbPath ? fileName(selectedProjectInfo.pcbPath) : '—' }}</span>
      </div>

      <!-- Schematic row -->
      <div class="status-row" :title="selectedProjectInfo.schematicPath">
        <span class="status-dot" :class="{ active: selectedProjectInfo.schematicOpen }"></span>
        <span class="status-label">Schematic</span>
        <span class="status-value">{{ selectedProjectInfo.schematicPath ? fileName(selectedProjectInfo.schematicPath) : '—' }}</span>
      </div>
    </div>

    <!-- No project selected -->
    <div v-else-if="!loading" class="no-project">
      <span class="material-icons">folder_off</span>
      <p>No project selected</p>
      <p class="hint">Open KiCAD or browse for a project</p>
    </div>

    <!-- Requirements Status -->
    <div v-if="selectedProjectInfo" class="requirements-section">
      <div class="requirements-status" :class="{ exists: requirementsExists }">
        <span class="material-icons req-icon">{{ requirementsExists ? 'check_circle' : 'error_outline' }}</span>
        <span class="status-text">{{ requirementsExists ? 'requirements.md' : 'No requirements.md' }}</span>
      </div>
      <button 
        @click="openRequirementsWizard" 
        class="requirements-btn"
        :title="requirementsExists ? 'Update requirements' : 'Create requirements'"
      >
        <span class="material-icons">{{ requirementsExists ? 'edit' : 'add' }}</span>
        {{ requirementsExists ? 'Edit' : 'Create' }}
      </button>
    </div>

    <!-- Requirements Wizard Modal -->
    <RequirementsWizard
      :project-dir="selectedProjectDir"
      :project-name="selectedProjectName"
      :visible="showRequirementsWizard"
      @close="closeRequirementsWizard"
      @saved="onRequirementsSaved"
    />
  </div>
</template>

<style scoped>
.kicad-selector {
  padding: 0.625rem 0.75rem 0.75rem;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

/* ── App Header ── */
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.125rem 0 0.25rem;
  border-bottom: 1px solid var(--border-color);
  margin-bottom: 0.125rem;
}

.app-title {
  font-size: 1rem;
  font-weight: 700;
  color: var(--accent-color);
  letter-spacing: -0.01em;
}

.loading-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: var(--accent-color);
  animation: pulse-dot 1.2s ease-in-out infinite;
}

@keyframes pulse-dot {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1); }
}

/* ── Project Switcher ── */
.switcher-wrapper {
  position: relative;
}

.switcher-btn {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 0.5rem 0.625rem;
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  cursor: pointer;
  color: var(--text-primary);
  font-size: 0.8125rem;
  font-weight: 600;
  transition: all 0.15s ease;
}

.switcher-btn:hover {
  border-color: var(--accent-color);
}

.switcher-label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.switcher-arrow {
  font-size: 1.25rem;
  flex-shrink: 0;
  color: var(--text-secondary);
}

.switcher-dropdown {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  background-color: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
  z-index: 100;
  max-height: 300px;
  overflow-y: auto;
}

.dropdown-section {
  padding: 0.25rem 0;
}

.dropdown-section + .dropdown-section {
  border-top: 1px solid var(--border-color);
}

.dropdown-section-label {
  padding: 0.25rem 0.625rem;
  font-size: 0.625rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-secondary);
}

.dropdown-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  padding: 0.375rem 0.625rem;
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
  color: var(--text-primary);
  font-size: 0.8125rem;
  transition: background 0.1s ease;
}

.dropdown-item:hover {
  background-color: var(--bg-tertiary);
}

.dropdown-item-icon {
  font-size: 1rem;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.dropdown-item-icon.open {
  color: #22c55e;
}

.dropdown-item-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.dropdown-item.show-more {
  color: var(--accent-color);
  font-size: 0.75rem;
}

.dropdown-item.browse {
  color: var(--accent-color);
}

.dropdown-browse {
  border-top: 1px solid var(--border-color);
}

.dropdown-empty {
  padding: 0.5rem 0.625rem;
  font-size: 0.75rem;
  color: var(--text-secondary);
  text-align: center;
}

/* ── Error ── */
.error-message {
  display: flex;
  align-items: flex-start;
  gap: 0.375rem;
  padding: 0.5rem;
  background-color: rgba(220, 38, 38, 0.08);
  border: 1px solid rgba(220, 38, 38, 0.2);
  border-radius: var(--radius-sm);
  color: #ef4444;
  font-size: 0.75rem;
}

.error-icon {
  font-size: 1rem;
  flex-shrink: 0;
}

.error-text {
  flex: 1;
  line-height: 1.4;
}

.copy-btn {
  padding: 0.125rem;
  background: transparent;
  border: none;
  cursor: pointer;
  color: #dc2626;
  display: flex;
  align-items: center;
}

.copy-btn .material-icons {
  font-size: 0.875rem;
}

/* ── Status Panel (Project / PCB / Schematic) ── */
.status-panel {
  display: flex;
  flex-direction: column;
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.status-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 0.625rem;
  cursor: default;
}

.status-row + .status-row {
  border-top: 1px solid var(--border-color);
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  background-color: var(--text-secondary);
  opacity: 0.35;
  transition: all 0.2s ease;
}

.status-dot.active {
  background-color: #22c55e;
  opacity: 1;
  box-shadow: 0 0 4px rgba(34, 197, 94, 0.4);
}

.status-label {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--text-secondary);
  width: 62px;
  flex-shrink: 0;
}

.status-value {
  font-size: 0.75rem;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
  font-family: 'SF Mono', 'Consolas', 'Monaco', 'Courier New', monospace;
}

/* ── No project ── */
.no-project {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 1.25rem 0.5rem;
  color: var(--text-secondary);
  text-align: center;
}

.no-project .material-icons {
  font-size: 2rem;
  margin-bottom: 0.5rem;
  opacity: 0.4;
}

.no-project p {
  margin: 0;
  font-size: 0.8125rem;
}

.no-project .hint {
  font-size: 0.6875rem;
  margin-top: 0.125rem;
  opacity: 0.7;
}

/* ── Requirements Section ── */
.requirements-section {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.375rem 0;
}

.requirements-status {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.requirements-status .req-icon {
  font-size: 0.875rem;
  color: #f59e0b;
}

.requirements-status.exists .req-icon {
  color: #22c55e;
}

.requirements-status .status-text {
  font-weight: 500;
}

.requirements-btn {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.25rem 0.5rem;
  background: linear-gradient(135deg, var(--accent-color) 0%, var(--accent-hover) 100%);
  color: white;
  border: none;
  border-radius: var(--radius-sm);
  font-size: 0.6875rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.requirements-btn:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}

.requirements-btn .material-icons {
  font-size: 0.8125rem;
}


</style>
