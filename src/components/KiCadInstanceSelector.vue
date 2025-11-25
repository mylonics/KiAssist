<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue';
import type { KiCadInstance, RecentProject } from '../types/pywebview';
import RequirementsWizard from './RequirementsWizard.vue';

// Configuration
const MAX_VISIBLE_RECENT = 5;
const REFRESH_INTERVAL_MS = 10000; // 10 seconds

const openProjects = ref<KiCadInstance[]>([]);
const recentProjects = ref<RecentProject[]>([]);
const selectedProject = ref<KiCadInstance | RecentProject | null>(null);
const loading = ref(false);
const error = ref<string>('');
const showAllRecent = ref(false);
let refreshTimer: number | null = null;

// Requirements wizard state
const showRequirementsWizard = ref(false);
const requirementsExists = ref(false);
const todoExists = ref(false);

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
  try {
    const apiAvailable = await waitForAPI();
    if (!apiAvailable) {
      error.value = 'pywebview API not available';
      return;
    }
    
    const result = await window.pywebview!.api.browse_for_project();
    
    if (result.success && result.path) {
      // Add to recent projects
      await window.pywebview!.api.add_recent_project(result.path);
      
      // Refresh the list
      await refreshProjectsList();
      
      // Select the newly added project
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
  
  // Add to recent projects if selecting from recent list
  if (!isOpen && 'path' in project) {
    window.pywebview?.api.add_recent_project(project.path);
  }
}

function isProjectOpen(project: RecentProject): boolean {
  const normalizedPath = project.path.toLowerCase();
  return openProjects.value.some(
    op => op.project_path.toLowerCase() === normalizedPath
  );
}

function getProjectName(project: KiCadInstance | RecentProject): string {
  if ('display_name' in project) {
    return project.display_name || project.project_name;
  }
  return project.name;
}

function getProjectPath(project: KiCadInstance | RecentProject): string {
  if ('project_path' in project) {
    return project.project_path;
  }
  return project.path;
}

function copyError() {
  if (error.value) {
    navigator.clipboard.writeText(error.value);
  }
}

function startRefreshTimer() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
  }
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

// Computed properties
const visibleRecentProjects = computed(() => {
  if (showAllRecent.value) {
    return recentProjects.value;
  }
  return recentProjects.value.slice(0, MAX_VISIBLE_RECENT);
});

const hasMoreRecentProjects = computed(() => {
  return recentProjects.value.length > MAX_VISIBLE_RECENT;
});

const selectedProjectInfo = computed(() => {
  if (!selectedProject.value) return null;
  
  // Check if it's a KiCadInstance (has socket_path)
  if ('socket_path' in selectedProject.value) {
    return {
      name: selectedProject.value.display_name || selectedProject.value.project_name,
      projectPath: selectedProject.value.project_path,
      pcbPath: selectedProject.value.pcb_path,
      schematicPath: selectedProject.value.schematic_path,
      isOpen: true
    };
  }
  
  // It's a RecentProject
  return {
    name: selectedProject.value.name,
    projectPath: selectedProject.value.path,
    pcbPath: '',
    schematicPath: '',
    isOpen: isProjectOpen(selectedProject.value)
  };
});

// Get project directory from project path
const selectedProjectDir = computed(() => {
  if (!selectedProjectInfo.value) return '';
  const path = selectedProjectInfo.value.projectPath;
  // Remove filename to get directory
  const lastSep = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
  return lastSep > 0 ? path.substring(0, lastSep) : path;
});

const selectedProjectName = computed(() => {
  return selectedProjectInfo.value?.name || 'Project';
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

// Watch for project changes to update requirements status
watch(selectedProject, () => {
  checkRequirementsFile();
}, { immediate: true });

onMounted(() => {
  refreshProjectsList();
  startRefreshTimer();
});

onUnmounted(() => {
  stopRefreshTimer();
});
</script>

<template>
  <div class="kicad-selector">
    <div class="selector-header">
      <h3>KiCAD Connection</h3>
      <button @click="refreshProjectsList" :disabled="loading" class="refresh-btn" :title="loading ? 'Refreshing...' : 'Refresh'">
        <span class="material-icons" :class="{ spinning: loading }">{{ loading ? 'sync' : 'refresh' }}</span>
      </button>
    </div>

    <div v-if="error" class="error-message">
      <span class="material-icons error-icon">warning</span>
      <span class="error-text">{{ error }}</span>
      <button @click="copyError" class="copy-btn" title="Copy error message">
        <span class="material-icons">content_copy</span>
      </button>
    </div>

    <div v-else class="projects-container">
      <!-- Open Projects Section -->
      <div v-if="openProjects.length > 0" class="section">
        <div class="section-header">
          <span class="material-icons section-icon">desktop_windows</span>
          <span class="section-title">Open in KiCAD</span>
        </div>
        <div class="project-list">
          <button
            v-for="project in openProjects"
            :key="project.socket_path"
            class="project-item"
            :class="{ selected: selectedProject === project }"
            @click="selectProject(project, true)"
          >
            <span class="material-icons status-icon open">check_circle</span>
            <div class="project-info">
              <div class="project-name">{{ getProjectName(project) }}</div>
              <div class="project-path" :title="getProjectPath(project)">{{ getProjectPath(project) }}</div>
            </div>
          </button>
        </div>
      </div>

      <!-- Recent Projects Section -->
      <div v-if="recentProjects.length > 0" class="section">
        <div class="section-header">
          <span class="material-icons section-icon">history</span>
          <span class="section-title">Recent Projects</span>
        </div>
        <div class="project-list">
          <button
            v-for="project in visibleRecentProjects"
            :key="project.path"
            class="project-item"
            :class="{ selected: selectedProject === project }"
            @click="selectProject(project, false)"
          >
            <span class="material-icons status-icon" :class="{ open: isProjectOpen(project) }">
              {{ isProjectOpen(project) ? 'check_circle' : 'folder' }}
            </span>
            <div class="project-info">
              <div class="project-name">{{ project.name }}</div>
              <div class="project-path" :title="project.path">{{ project.path }}</div>
            </div>
          </button>
        </div>
        <button
          v-if="hasMoreRecentProjects"
          class="show-more-btn"
          @click="showAllRecent = !showAllRecent"
        >
          <span class="material-icons">{{ showAllRecent ? 'expand_less' : 'expand_more' }}</span>
          {{ showAllRecent ? 'Show less' : `Show ${recentProjects.length - MAX_VISIBLE_RECENT} more` }}
        </button>
      </div>

      <!-- No projects message -->
      <div v-if="openProjects.length === 0 && recentProjects.length === 0 && !loading" class="no-projects">
        <span class="material-icons">folder_off</span>
        <p>No projects found</p>
        <p class="hint">Open KiCAD or browse for a project</p>
      </div>

      <!-- Browse Button - Always visible -->
      <div class="browse-section">
        <button class="browse-btn" @click="browseForProject">
          <span class="material-icons">folder_open</span>
          Browse for Project...
        </button>
      </div>
    </div>

    <!-- Selected Project Info -->
    <div v-if="selectedProjectInfo" class="selected-info">
      <div class="connection-status" :class="{ connected: selectedProjectInfo.isOpen }">
        <span class="status-dot"></span>
        {{ selectedProjectInfo.isOpen ? 'Connected to KiCAD' : 'Project Selected' }}
      </div>
      <div class="info-row" v-if="selectedProjectInfo.projectPath">
        <span class="label">Project:</span>
        <span class="value" :title="selectedProjectInfo.projectPath">{{ selectedProjectInfo.projectPath }}</span>
      </div>
      <div class="info-row" v-if="selectedProjectInfo.pcbPath">
        <span class="label">PCB:</span>
        <span class="value" :title="selectedProjectInfo.pcbPath">{{ selectedProjectInfo.pcbPath }}</span>
      </div>
      <div class="info-row" v-if="selectedProjectInfo.schematicPath">
        <span class="label">Schematic:</span>
        <span class="value" :title="selectedProjectInfo.schematicPath">{{ selectedProjectInfo.schematicPath }}</span>
      </div>
      
      <!-- Requirements Status -->
      <div class="requirements-section">
        <div class="requirements-status" :class="{ exists: requirementsExists }">
          <span class="material-icons status-icon">{{ requirementsExists ? 'check_circle' : 'error_outline' }}</span>
          <span class="status-text">{{ requirementsExists ? 'requirements.md exists' : 'No requirements.md' }}</span>
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
</style>
