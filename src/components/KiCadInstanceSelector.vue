<script setup lang="ts">
import { ref, onMounted } from 'vue';
import type { KiCadInstance } from '../types/pywebview';

const instances = ref<KiCadInstance[]>([]);
const selectedInstance = ref<KiCadInstance | null>(null);
const loading = ref(false);
const error = ref<string>('');

async function waitForAPI(maxAttempts = 20, delayMs = 100): Promise<boolean> {
  for (let i = 0; i < maxAttempts; i++) {
    if (window.pywebview?.api) {
      return true;
    }
    await new Promise(resolve => setTimeout(resolve, delayMs));
  }
  return false;
}

async function detectInstances() {
  loading.value = true;
  error.value = '';
  
  try {
    // Wait for API to be available (up to 2 seconds)
    const apiAvailable = await waitForAPI();
    
    if (!apiAvailable) {
      error.value = 'pywebview API not available';
      return;
    }
    
    const detected = await window.pywebview!.api.detect_kicad_instances();
    instances.value = detected;
    
    // Auto-select if only one instance is found
    if (detected.length === 1) {
      selectedInstance.value = detected[0];
    } else if (detected.length === 0) {
      error.value = 'No KiCAD instances detected. Please make sure KiCAD is running.';
    }
  } catch (err) {
    error.value = `Error detecting KiCAD instances: ${err}`;
    console.error('Error detecting KiCAD instances:', err);
  } finally {
    loading.value = false;
  }
}

function copyError() {
  if (error.value) {
    navigator.clipboard.writeText(error.value);
  }
}

// Auto-detect on component mount
onMounted(() => {
  detectInstances();
});
</script>

<template>
  <div class="kicad-selector">
    <div class="selector-header">
      <h3>KiCAD Connection</h3>
      <button @click="detectInstances" :disabled="loading" class="refresh-btn">
        <span class="material-icons">refresh</span>
        {{ loading ? 'Detecting...' : 'Refresh' }}
      </button>
    </div>

    <div v-if="error" class="error-message">
      <span class="material-icons error-icon">warning</span>
      <span class="error-text">{{ error }}</span>
      <button @click="copyError" class="copy-btn" title="Copy error message">
        <span class="material-icons">content_copy</span>
      </button>
    </div>

    <div v-else-if="loading" class="loading-message">
      <div class="spinner"></div>
      <span>Detecting KiCAD instances...</span>
    </div>

    <div v-else-if="instances.length === 0" class="no-instances">
      <p>No KiCAD instances found.</p>
      <p class="hint">Please start KiCAD and click Refresh.</p>
    </div>

    <div v-else-if="instances.length === 1" class="single-instance">
      <div class="instance-card selected">
        <div class="instance-icon">
          <span class="material-icons">check</span>
        </div>
        <div class="instance-info">
          <div class="instance-name">{{ instances[0].display_name }}</div>
          <div class="instance-project">{{ instances[0].project_name }}</div>
        </div>
      </div>
      <div class="selected-info">
        <div class="info-row" v-if="instances[0].project_path">
          <span class="label">Project:</span>
          <span class="value" :title="instances[0].project_path">{{ instances[0].project_path }}</span>
        </div>
        <div class="info-row" v-if="instances[0].pcb_path">
          <span class="label">PCB:</span>
          <span class="value" :title="instances[0].pcb_path">{{ instances[0].pcb_path }}</span>
        </div>
        <div class="info-row" v-if="instances[0].schematic_path">
          <span class="label">Schematic:</span>
          <span class="value" :title="instances[0].schematic_path">{{ instances[0].schematic_path }}</span>
        </div>
      </div>
      <p class="connection-status">Connected to KiCAD</p>
    </div>

    <div v-else class="multiple-instances">
      <label for="instance-select">Select KiCAD Instance:</label>
      <select 
        id="instance-select" 
        v-model="selectedInstance" 
        class="instance-dropdown"
      >
        <option :value="null" disabled>-- Choose an instance --</option>
        <option 
          v-for="instance in instances" 
          :key="instance.socket_path" 
          :value="instance"
        >
          {{ instance.display_name }}
        </option>
      </select>

      <div v-if="selectedInstance" class="selected-info">
        <div class="info-row" v-if="selectedInstance.project_path">
          <span class="label">Project:</span>
          <span class="value" :title="selectedInstance.project_path">{{ selectedInstance.project_path }}</span>
        </div>
        <div class="info-row" v-if="selectedInstance.pcb_path">
          <span class="label">PCB:</span>
          <span class="value" :title="selectedInstance.pcb_path">{{ selectedInstance.pcb_path }}</span>
        </div>
        <div class="info-row" v-if="selectedInstance.schematic_path">
          <span class="label">Schematic:</span>
          <span class="value" :title="selectedInstance.schematic_path">{{ selectedInstance.schematic_path }}</span>
        </div>
      </div>
    </div>
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
  padding: 0.375rem 0.75rem;
  background-color: var(--bg-input);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 0.8125rem;
  color: var(--text-primary);
  transition: all 0.15s ease;
  display: flex;
  align-items: center;
  gap: 0.375rem;
}

.refresh-btn .material-icons {
  font-size: 1.125rem;
}

.refresh-btn:hover:not(:disabled) {
  background-color: var(--bg-tertiary);
  border-color: var(--accent-color);
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

.loading-message {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  padding: 1rem;
  justify-content: center;
  color: var(--text-secondary);
  font-size: 0.875rem;
}

.spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--border-color);
  border-top-color: var(--accent-color);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.no-instances {
  text-align: center;
  padding: 1.5rem 1rem;
  color: var(--text-secondary);
}

.no-instances p:first-child {
  font-weight: 500;
  margin-bottom: 0.375rem;
  font-size: 0.9375rem;
}

.hint {
  font-size: 0.8125rem;
  opacity: 0.85;
}

.single-instance {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.instance-card {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem;
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  margin-bottom: 0.75rem;
  transition: all 0.15s ease;
}

.instance-card.selected {
  border-color: var(--accent-color);
  background: linear-gradient(135deg, rgba(88, 101, 242, 0.08) 0%, rgba(71, 82, 196, 0.08) 100%);
  box-shadow: 0 0 0 2px rgba(88, 101, 242, 0.1);
}

.instance-icon {
  width: 32px;
  height: 32px;
  background: linear-gradient(135deg, var(--accent-color) 0%, var(--accent-hover) 100%);
  color: white;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: var(--shadow-sm);
}

.instance-icon .material-icons {
  font-size: 1.25rem;
}

.instance-info {
  flex: 1;
  min-width: 0;
}

.instance-name {
  font-weight: 500;
  color: var(--text-primary);
  font-size: 0.875rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.instance-project {
  font-size: 0.75rem;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 0.125rem;
}

.connection-status {
  color: #22c55e;
  font-weight: 500;
  font-size: 0.8125rem;
  margin-top: 0.5rem;
  text-align: center;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.375rem;
}

.connection-status::before {
  content: '';
  width: 6px;
  height: 6px;
  background-color: #22c55e;
  border-radius: 50%;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.multiple-instances label {
  display: block;
  margin-bottom: 0.5rem;
  font-weight: 500;
  color: var(--text-primary);
  font-size: 0.875rem;
}

.instance-dropdown {
  width: 100%;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background-color: var(--bg-input);
  color: var(--text-primary);
  font-size: 0.875rem;
  cursor: pointer;
  transition: all 0.15s ease;
}

.instance-dropdown:hover {
  border-color: var(--accent-color);
}

.instance-dropdown:focus {
  outline: none;
  border-color: var(--accent-color);
  box-shadow: 0 0 0 2px rgba(88, 101, 242, 0.15);
}

.selected-info {
  margin-top: 0.75rem;
  padding: 0.75rem;
  background-color: var(--bg-secondary);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color);
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.info-row {
  display: flex;
  flex-direction: column;
  padding: 0.5rem 0;
}

.info-row:not(:last-child) {
  border-bottom: 1px solid var(--border-color);
}

.info-row .label {
  font-weight: 500;
  color: var(--text-secondary);
  font-size: 0.75rem;
  margin-bottom: 0.25rem;
  text-transform: uppercase;
  letter-spacing: 0.025em;
}

.info-row .value {
  color: var(--text-primary);
  font-weight: 400;
  word-break: break-all;
  font-size: 0.8125rem;
  font-family: 'SF Mono', 'Consolas', 'Monaco', 'Courier New', monospace;
  line-height: 1.4;
}
</style>
