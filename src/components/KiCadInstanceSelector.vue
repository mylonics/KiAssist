<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue';
import type { KiCadInstance, RecentProject } from '../types/pywebview';
import RequirementsWizard from './RequirementsWizard.vue';

// Configuration
const MAX_VISIBLE_RECENT = 5;
const REFRESH_INTERVAL_MS = 1000; // 3 seconds
const MESSAGE_DISPLAY_DURATION_MS = 5000; // 5 seconds for success messages

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

// Inject test state
const injectingTest = ref(false);
const injectTestMessage = ref<string>('');

// Refs for click-outside detection
const switcherRef = ref<HTMLElement | null>(null);

async function waitForAPI(maxAttempts = 20, delayMs = 100): Promise<boolean> {
  for (let i = 0; i < maxAttempts; i++) {
    if (window.pywebview?.api) {
      return true;
    }
    await new Promise(resolve => setTimeout(resolve, delayMs));
  }
  return false;
}

async function refreshProjectsList() {
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
          }
        }
      }
      
      // Auto-select if only one open project
      if (result.open_projects.length === 1 && !selectedProject.value) {
        selectedProject.value = result.open_projects[0];
      }
    } else {
      error.value = result.error || 'Failed to get projects list';
    }
  } catch (err) {
    error.value = `Error getting projects: ${err}`;
    console.error('Error getting projects:', err);
  } finally {
    loading.value = false;
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

// Inject test note function
async function injectTestNote() {
  if (!selectedProjectInfo.value?.projectPath) {
    error.value = 'No project selected. Please select a KiCad project first.';
    return;
  }
  
  injectingTest.value = true;
  injectTestMessage.value = '';
  error.value = '';
  
  try {
    const apiAvailable = await waitForAPI();
    if (!apiAvailable) {
      error.value = 'pywebview API not available';
      return;
    }
    
    const result = await window.pywebview!.api.inject_schematic_test_note(
      selectedProjectInfo.value.projectPath
    );
    
    if (result.success) {
      injectTestMessage.value = result.message || 'Test note injected successfully!';
      setTimeout(() => {
        injectTestMessage.value = '';
      }, MESSAGE_DISPLAY_DURATION_MS);
    } else {
      error.value = result.error || 'Failed to inject test note';
    }
  } catch (err) {
    error.value = `Error injecting test note: ${err}`;
    console.error('Error injecting test note:', err);
  } finally {
    injectingTest.value = false;
  }
}

// Watch for project changes to update requirements status
watch(selectedProject, () => {
  checkRequirementsFile();
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

    <!-- Inject Test Section -->
    <div v-if="selectedProjectInfo" class="inject-test-section">
      <button 
        @click="injectTestNote" 
        class="inject-test-btn"
        :disabled="injectingTest || !selectedProjectInfo.projectPath"
        title="Add a test note to the schematic"
      >
        <span class="material-icons" :class="{ spinning: injectingTest }">
          {{ injectingTest ? 'sync' : 'science' }}
        </span>
        {{ injectingTest ? 'Injecting...' : 'Inject Test' }}
      </button>
      <div v-if="injectTestMessage" class="inject-test-message success">
        <span class="material-icons">check_circle</span>
        {{ injectTestMessage }}
      </div>
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
  padding: 1rem;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.selector-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--border-color);
}

.selector-header h3 {
  margin: 0;
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--text-primary);
}

.refresh-btn {
  padding: 0.375rem;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--text-secondary);
  transition: all 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.refresh-btn .material-icons {
  font-size: 1.375rem;
}

.refresh-btn .material-icons.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.refresh-btn:hover:not(:disabled) {
  background-color: var(--bg-tertiary);
  color: var(--accent-color);
}

.refresh-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.error-message {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.75rem;
  background-color: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: var(--radius-md);
  color: #dc2626;
  font-size: 0.8125rem;
}

.error-icon {
  font-size: 1.25rem;
  flex-shrink: 0;
  color: #dc2626;
}

.error-text {
  flex: 1;
  line-height: 1.4;
}

.copy-btn {
  padding: 0.25rem;
  background-color: rgba(220, 38, 38, 0.1);
  border: 1px solid rgba(220, 38, 38, 0.2);
  border-radius: var(--radius-sm);
  cursor: pointer;
  flex-shrink: 0;
  color: #dc2626;
  transition: background 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.copy-btn .material-icons {
  font-size: 1rem;
}

.copy-btn:hover {
  background-color: rgba(220, 38, 38, 0.15);
}

.projects-container {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.section {
  display: flex;
  flex-direction: column;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 0;
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.section-icon {
  font-size: 1rem;
}

.project-list {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.project-item {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  padding: 0.5rem 0.625rem;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  text-align: left;
  transition: all 0.15s ease;
  width: 100%;
}

.project-item:hover {
  background-color: var(--bg-tertiary);
  border-color: var(--border-color);
}

.project-item.selected {
  background: linear-gradient(135deg, rgba(88, 101, 242, 0.08) 0%, rgba(71, 82, 196, 0.08) 100%);
  border-color: var(--accent-color);
}

.status-icon {
  font-size: 1.125rem;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.status-icon.open {
  color: #22c55e;
}

.project-info {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.project-name {
  font-weight: 500;
  font-size: 0.8125rem;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.project-path {
  font-size: 0.6875rem;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 0.125rem;
}

.show-more-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.25rem;
  padding: 0.375rem;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--accent-color);
  font-size: 0.75rem;
  font-weight: 500;
  transition: background 0.15s ease;
}

.show-more-btn:hover {
  background-color: var(--bg-tertiary);
}

.show-more-btn .material-icons {
  font-size: 1.125rem;
}

.no-projects {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 1.5rem;
  color: var(--text-secondary);
  text-align: center;
}

.no-projects .material-icons {
  font-size: 2.5rem;
  margin-bottom: 0.75rem;
  opacity: 0.5;
}

.no-projects p {
  margin: 0;
  font-size: 0.875rem;
}

.no-projects .hint {
  font-size: 0.75rem;
  margin-top: 0.25rem;
  opacity: 0.8;
}

.browse-section {
  margin-top: auto;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border-color);
}

.browse-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  width: 100%;
  padding: 0.625rem;
  background-color: var(--bg-secondary);
  border: 1px dashed var(--border-color);
  border-radius: var(--radius-md);
  cursor: pointer;
  color: var(--text-primary);
  font-size: 0.8125rem;
  font-weight: 500;
  transition: all 0.15s ease;
}

.browse-btn:hover {
  background-color: var(--bg-tertiary);
  border-color: var(--accent-color);
  color: var(--accent-color);
}

.browse-btn .material-icons {
  font-size: 1.125rem;
}

.selected-info {
  margin-top: 0.75rem;
  padding: 0.75rem;
  background-color: var(--bg-secondary);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color);
}

.connection-status {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 0.625rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--border-color);
}

.connection-status.connected {
  color: #22c55e;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: var(--text-secondary);
}

.connection-status.connected .status-dot {
  background-color: #22c55e;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.info-row {
  display: flex;
  flex-direction: column;
  padding: 0.375rem 0;
}

.info-row:not(:last-child) {
  border-bottom: 1px solid var(--border-color);
}

.info-row .label {
  font-weight: 500;
  color: var(--text-secondary);
  font-size: 0.6875rem;
  margin-bottom: 0.125rem;
  text-transform: uppercase;
  letter-spacing: 0.025em;
}

.info-row .value {
  color: var(--text-primary);
  font-weight: 400;
  word-break: break-all;
  font-size: 0.75rem;
  font-family: 'SF Mono', 'Consolas', 'Monaco', 'Courier New', monospace;
  line-height: 1.4;
}

/* Requirements Section */
.requirements-section {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 0.75rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border-color);
}

.requirements-status {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.requirements-status .status-icon {
  font-size: 1rem;
  color: #f59e0b;
}

.requirements-status.exists .status-icon {
  color: #22c55e;
}

.requirements-status .status-text {
  font-weight: 500;
}

.requirements-btn {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.375rem 0.625rem;
  background: linear-gradient(135deg, var(--accent-color) 0%, var(--accent-hover) 100%);
  color: white;
  border: none;
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.requirements-btn:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}

.requirements-btn .material-icons {
  font-size: 0.875rem;
}

/* Inject Test Section */
.inject-test-section {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-top: 0.75rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border-color);
}

.inject-test-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  width: 100%;
  padding: 0.625rem;
  background: linear-gradient(135deg, #10b981 0%, #059669 100%);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 0.8125rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.inject-test-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}

.inject-test-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  transform: none;
}

.inject-test-btn .material-icons {
  font-size: 1.125rem;
}

.inject-test-btn .material-icons.spinning {
  animation: spin 1s linear infinite;
}

.inject-test-message {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.5rem 0.625rem;
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
  font-weight: 500;
}

.inject-test-message.success {
  background-color: rgba(16, 185, 129, 0.1);
  color: #10b981;
  border: 1px solid rgba(16, 185, 129, 0.2);
}

.inject-test-message .material-icons {
  font-size: 1rem;
}
</style>
