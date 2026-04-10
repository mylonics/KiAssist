<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { getApi } from '../composables/useApi';

const activeTab = ref<'raw' | 'synthesized'>('raw');
const rawContext = ref<string>('');
const synthesizedContext = ref<string>('');
const loadingRaw = ref(false);
const loadingSynthesized = ref(false);
const errorRaw = ref('');
const errorSynthesized = ref('');

async function loadRawContext() {
  loadingRaw.value = true;
  errorRaw.value = '';
  try {
    const api = getApi();
    if (!api) {
      errorRaw.value = 'Backend not available';
      return;
    }
    const result = await api.get_raw_project_context();
    if (result.success) {
      rawContext.value = result.context ?? '';
    } else {
      errorRaw.value = result.error || 'Failed to load raw context';
    }
  } catch (e: any) {
    errorRaw.value = e.message || 'Unknown error';
  } finally {
    loadingRaw.value = false;
  }
}

async function loadSynthesizedContext() {
  loadingSynthesized.value = true;
  errorSynthesized.value = '';
  try {
    const api = getApi();
    if (!api) {
      errorSynthesized.value = 'Backend not available';
      return;
    }
    const result = await api.get_synthesized_project_context();
    if (result.success) {
      synthesizedContext.value = result.context ?? '';
    } else {
      errorSynthesized.value = result.error || 'Failed to synthesize context';
    }
  } catch (e: any) {
    errorSynthesized.value = e.message || 'Unknown error';
  } finally {
    loadingSynthesized.value = false;
  }
}

async function loadCached() {
  try {
    const api = getApi();
    if (!api) return;
    const result = await api.get_cached_project_context();
    if (result.success) {
      if (result.raw) rawContext.value = result.raw;
      if (result.synthesized) synthesizedContext.value = result.synthesized;
    }
  } catch (err) {
    console.error('[ProjectContextPanel] Failed to load cached context:', err);
  }
}

function copyToClipboard(text: string) {
  navigator.clipboard.writeText(text).catch(() => {});
}

onMounted(() => {
  loadCached();
});
</script>

<template>
  <div class="context-panel">
    <div class="context-tabs">
      <button
        :class="['ctx-tab-btn', { active: activeTab === 'raw' }]"
        @click="activeTab = 'raw'"
        title="Raw project context extracted from schematic/board files"
      >
        <span class="material-icons tab-icon">description</span>
        Raw
      </button>
      <button
        :class="['ctx-tab-btn', { active: activeTab === 'synthesized' }]"
        @click="activeTab = 'synthesized'"
        title="LLM-synthesized summary of project context"
      >
        <span class="material-icons tab-icon">auto_awesome</span>
        Synthesized
      </button>
    </div>

    <!-- Raw Context Tab -->
    <div v-show="activeTab === 'raw'" class="context-content">
      <div class="context-toolbar">
        <button class="ctx-action-btn" @click="loadRawContext" :disabled="loadingRaw">
          <span class="material-icons">refresh</span>
          {{ loadingRaw ? 'Loading...' : 'Build Raw Context' }}
        </button>
        <button
          v-if="rawContext"
          class="ctx-action-btn secondary"
          @click="copyToClipboard(rawContext)"
          title="Copy to clipboard"
        >
          <span class="material-icons">content_copy</span>
        </button>
      </div>
      <div v-if="errorRaw" class="context-error">{{ errorRaw }}</div>
      <div v-if="loadingRaw" class="context-loading">
        <span class="material-icons spinning">sync</span>
        Extracting project context from KiCad files...
      </div>
      <pre v-else-if="rawContext" class="context-text">{{ rawContext }}</pre>
      <div v-else class="context-empty">
        <span class="material-icons">info</span>
        Click "Build Raw Context" to extract schematic hierarchy, components, and netlist from the active project.
      </div>
    </div>

    <!-- Synthesized Context Tab -->
    <div v-show="activeTab === 'synthesized'" class="context-content">
      <div class="context-toolbar">
        <button class="ctx-action-btn" @click="loadSynthesizedContext" :disabled="loadingSynthesized">
          <span class="material-icons">auto_awesome</span>
          {{ loadingSynthesized ? 'Synthesizing...' : 'Synthesize Context' }}
        </button>
        <button
          v-if="synthesizedContext"
          class="ctx-action-btn secondary"
          @click="copyToClipboard(synthesizedContext)"
          title="Copy to clipboard"
        >
          <span class="material-icons">content_copy</span>
        </button>
      </div>
      <div v-if="errorSynthesized" class="context-error">{{ errorSynthesized }}</div>
      <div v-if="loadingSynthesized" class="context-loading">
        <span class="material-icons spinning">sync</span>
        LLM is analyzing and summarizing the project context...
      </div>
      <pre v-else-if="synthesizedContext" class="context-text">{{ synthesizedContext }}</pre>
      <div v-else class="context-empty">
        <span class="material-icons">info</span>
        Click "Synthesize Context" to have the LLM produce a structured summary of the project.
        {{ !rawContext ? 'Raw context will be built first.' : '' }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.context-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.context-tabs {
  display: flex;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.ctx-tab-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.25rem;
  padding: 0.4rem 0.5rem;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.7rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  border-bottom: 2px solid transparent;
}

.ctx-tab-btn:hover {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}

.ctx-tab-btn.active {
  color: var(--accent-color);
  border-bottom-color: var(--accent-color);
  background-color: var(--bg-secondary);
}

.context-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
}

.context-toolbar {
  display: flex;
  gap: 0.25rem;
  padding: 0.4rem;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
  align-items: center;
}

.ctx-action-btn {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.3rem 0.6rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background-color: var(--accent-color);
  color: #fff;
  font-size: 0.7rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}

.ctx-action-btn:hover:not(:disabled) {
  background-color: var(--accent-hover);
}

.ctx-action-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.ctx-action-btn.secondary {
  background-color: transparent;
  color: var(--text-secondary);
  border-color: var(--border-color);
  padding: 0.3rem;
}

.ctx-action-btn.secondary:hover {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}

.ctx-action-btn .material-icons {
  font-size: 0.85rem;
}

.context-error {
  padding: 0.5rem;
  color: #e53e3e;
  font-size: 0.75rem;
  background-color: rgba(229, 62, 62, 0.1);
  border-bottom: 1px solid rgba(229, 62, 62, 0.2);
}

.context-loading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 1rem;
  color: var(--text-secondary);
  font-size: 0.8rem;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.context-text {
  flex: 1;
  overflow: auto;
  padding: 0.5rem;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 0.65rem;
  line-height: 1.4;
  color: var(--text-primary);
  background-color: var(--bg-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}

.context-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 1.5rem 1rem;
  color: var(--text-secondary);
  font-size: 0.75rem;
  text-align: center;
}

.context-empty .material-icons {
  font-size: 1.5rem;
  opacity: 0.5;
}

.tab-icon {
  font-size: 0.9rem;
}
</style>
