<script setup lang="ts">
import { ref, onMounted } from 'vue';
import type { KiCadInstance } from '../types/pywebview';

const instances = ref<KiCadInstance[]>([]);
const selectedInstance = ref<KiCadInstance | null>(null);
const loading = ref(false);
const error = ref<string>('');

async function detectInstances() {
  loading.value = true;
  error.value = '';
  
  try {
    if (window.pywebview?.api) {
      const detected = await window.pywebview.api.detect_kicad_instances();
      instances.value = detected;
      
      // Auto-select if only one instance is found
      if (detected.length === 1) {
        selectedInstance.value = detected[0];
      } else if (detected.length === 0) {
        error.value = 'No KiCAD instances detected. Please make sure KiCAD is running.';
      }
    } else {
      error.value = 'pywebview API not available';
    }
  } catch (err) {
    error.value = `Error detecting KiCAD instances: ${err}`;
    console.error('Error detecting KiCAD instances:', err);
  } finally {
    loading.value = false;
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
        {{ loading ? 'Detecting...' : '🔄 Refresh' }}
      </button>
    </div>

    <div v-if="error" class="error-message">
      <span class="error-icon">⚠️</span>
      <span>{{ error }}</span>
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
        <div class="instance-icon">✓</div>
        <div class="instance-info">
          <div class="instance-name">{{ instances[0].display_name }}</div>
          <div class="instance-project">{{ instances[0].project_name }}</div>
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
        <div class="info-row">
          <span class="label">Project:</span>
          <span class="value">{{ selectedInstance.project_name }}</span>
        </div>
        <div class="info-row">
          <span class="label">Version:</span>
          <span class="value">{{ selectedInstance.version }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.kicad-selector {
  padding: 1rem;
  background-color: var(--bg-tertiary);
  border-radius: 8px;
  margin-bottom: 1rem;
}

.selector-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}

.selector-header h3 {
  margin: 0;
  font-size: 1.1rem;
  color: var(--text-primary);
}

.refresh-btn {
  padding: 0.5rem 1rem;
  background-color: var(--bg-input);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.9rem;
  color: var(--text-primary);
  transition: background-color 0.2s;
}

.refresh-btn:hover:not(:disabled) {
  background-color: var(--bg-secondary);
}

.refresh-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.error-message {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem;
  background-color: #fee;
  border: 1px solid #fcc;
  border-radius: 6px;
  color: #c33;
}

.error-icon {
  font-size: 1.2rem;
}

.loading-message {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem;
  justify-content: center;
  color: var(--text-secondary);
}

.spinner {
  width: 20px;
  height: 20px;
  border: 3px solid var(--border-color);
  border-top-color: #667eea;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.no-instances {
  text-align: center;
  padding: 2rem 1rem;
  color: var(--text-secondary);
}

.no-instances p:first-child {
  font-weight: 500;
  margin-bottom: 0.5rem;
}

.hint {
  font-size: 0.9rem;
  opacity: 0.8;
}

.single-instance {
  text-align: center;
}

.instance-card {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1rem;
  background-color: var(--bg-primary);
  border: 2px solid var(--border-color);
  border-radius: 8px;
  margin-bottom: 0.5rem;
}

.instance-card.selected {
  border-color: #667eea;
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.1) 0%, rgba(118, 75, 162, 0.1) 100%);
}

.instance-icon {
  width: 40px;
  height: 40px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.5rem;
  flex-shrink: 0;
}

.instance-info {
  flex: 1;
  text-align: left;
}

.instance-name {
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 0.25rem;
}

.instance-project {
  font-size: 0.85rem;
  color: var(--text-secondary);
}

.connection-status {
  color: #5a5;
  font-weight: 500;
  margin-top: 0.5rem;
}

.multiple-instances label {
  display: block;
  margin-bottom: 0.5rem;
  font-weight: 500;
  color: var(--text-primary);
}

.instance-dropdown {
  width: 100%;
  padding: 0.75rem;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background-color: var(--bg-input);
  color: var(--text-primary);
  font-size: 1rem;
  cursor: pointer;
  transition: border-color 0.2s;
}

.instance-dropdown:focus {
  outline: none;
  border-color: #667eea;
}

.selected-info {
  margin-top: 1rem;
  padding: 0.75rem;
  background-color: var(--bg-primary);
  border-radius: 6px;
  border: 1px solid var(--border-color);
}

.info-row {
  display: flex;
  justify-content: space-between;
  padding: 0.5rem 0;
}

.info-row:not(:last-child) {
  border-bottom: 1px solid var(--border-color);
}

.info-row .label {
  font-weight: 500;
  color: var(--text-secondary);
}

.info-row .value {
  color: var(--text-primary);
  font-weight: 600;
}
</style>
