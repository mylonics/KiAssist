<script setup lang="ts">
import { ref, onMounted } from 'vue';
import ChatBox from './components/ChatBox.vue';
import KiCadInstanceSelector from './components/KiCadInstanceSelector.vue';
import ApiActivityPanel from './components/ApiActivityPanel.vue';
import LlmActivityPanel from './components/LlmActivityPanel.vue';
import ProjectContextPanel from './components/ProjectContextPanel.vue';
import SymbolImporter from './components/SymbolImporter.vue';
import LibraryScanner from './components/LibraryScanner.vue';
import AppSettingsDialog from './components/AppSettings.vue';
import ImporterDetails from './components/ImporterDetails.vue';
import { useAppSettings } from './composables/useAppSettings';
import type { ImportedComponent, ImportedFields } from './types/importer';

const activityPanel = ref<InstanceType<typeof ApiActivityPanel> | null>(null);
const chatBox = ref<InstanceType<typeof ChatBox> | null>(null);
const rightPanelCollapsed = ref(true);
const rightPanelTab = ref<'context' | 'llm' | 'api'>('context');
const showSettings = ref(false);

// Load settings from backend on startup
const { loadFieldDefaultsFromBackend, loadLibraryDefaultsFromBackend } = useAppSettings();
onMounted(async () => {
  await loadFieldDefaultsFromBackend();
  await loadLibraryDefaultsFromBackend();
});

// Importer details overlay state
const importedComponent = ref<ImportedComponent | null>(null);
const importWarnings = ref<string[]>([]);

function handleComponentImported(component: ImportedComponent, warnings: string[]) {
  const existing = importedComponent.value;
  if (existing) {
    // Merge: new non-empty values override existing (last-wins),
    // but empty values from the new import don't wipe existing data.
    const merged: ImportedComponent = { ...existing };

    // Scalar string fields — keep existing when new value is empty
    const keys: (keyof ImportedComponent)[] = [
      'name', 'import_method', 'source_info',
      'symbol_path', 'footprint_path',
      'symbol_sexpr', 'footprint_sexpr', 'step_data',
    ];
    for (const k of keys) {
      if (component[k]) {
        (merged as any)[k] = component[k];
      }
    }

    // model_paths — non-empty array wins
    if (component.model_paths?.length) {
      merged.model_paths = [...component.model_paths];
    }

    // Fields — merge individually, non-empty wins
    const fieldKeys: (keyof ImportedFields)[] = [
      'mpn', 'manufacturer', 'digikey_pn', 'mouser_pn',
      'lcsc_pn', 'value', 'reference', 'footprint',
      'datasheet', 'description', 'package',
    ];
    merged.fields = { ...existing.fields };
    for (const fk of fieldKeys) {
      if (component.fields[fk]) {
        (merged.fields as any)[fk] = component.fields[fk];
      }
    }
    // Merge extra fields
    merged.fields.extra = { ...existing.fields.extra };
    if (component.fields.extra) {
      for (const [ek, ev] of Object.entries(component.fields.extra)) {
        if (ev) merged.fields.extra[ek] = ev;
      }
    }

    importedComponent.value = merged;
  } else {
    importedComponent.value = component;
  }
  importWarnings.value = warnings;
}

function handleImporterClose() {
  importedComponent.value = null;
  importWarnings.value = [];
}

function handleContextQuestionsReady(questions: Array<{ question: string; suggestions: string[] }>) {
  console.log('[App] handleContextQuestionsReady, questions:', questions.length, 'chatBox ref:', !!chatBox.value);
  chatBox.value?.startContextQA(questions);
}
</script>

<template>
  <div class="app-container">
    <aside class="left-panel">
      <div class="left-panel-content">
        <KiCadInstanceSelector
          @context-questions-ready="handleContextQuestionsReady"
        />
        <div class="left-panel-divider"></div>
        <SymbolImporter
          @component-imported="handleComponentImported"
        />
        <div class="left-panel-divider"></div>
        <LibraryScanner />
        <div class="left-panel-divider"></div>
        <button class="settings-btn" @click="showSettings = true">
          <span class="material-icons settings-btn-icon">settings</span>
          <span class="settings-btn-text">Settings</span>
        </button>
      </div>
    </aside>
    <main class="chat-panel">
      <ImporterDetails
        v-if="importedComponent"
        :component="importedComponent"
        :warnings="importWarnings"
        @close="handleImporterClose"
      />
      <ChatBox v-else ref="chatBox" :activityPanel="activityPanel" />
    </main>
    <button
      class="panel-toggle-btn"
      @click="rightPanelCollapsed = !rightPanelCollapsed"
      :title="rightPanelCollapsed ? 'Show Debug Panel' : 'Hide Debug Panel'"
    >
      <span class="material-icons">{{ rightPanelCollapsed ? 'chevron_left' : 'chevron_right' }}</span>
    </button>
    <aside v-show="!rightPanelCollapsed" class="right-panel">
      <div class="right-panel-tabs">
        <button
          :class="['tab-btn', { active: rightPanelTab === 'context' }]"
          @click="rightPanelTab = 'context'"
          title="Project context (schematic hierarchy, BOM, netlist)"
        >
          <span class="material-icons tab-icon">account_tree</span>
          Context
        </button>
        <button
          :class="['tab-btn', { active: rightPanelTab === 'llm' }]"
          @click="rightPanelTab = 'llm'"
          title="LLM interactions (backend ↔ AI)"
        >
          <span class="material-icons tab-icon">psychology</span>
          LLM
        </button>
        <button
          :class="['tab-btn', { active: rightPanelTab === 'api' }]"
          @click="rightPanelTab = 'api'"
          title="API calls (frontend ↔ backend)"
        >
          <span class="material-icons tab-icon">swap_horiz</span>
          API
        </button>
      </div>
      <div class="right-panel-content">
        <ProjectContextPanel v-show="rightPanelTab === 'context'" />
        <LlmActivityPanel v-show="rightPanelTab === 'llm'" />
        <ApiActivityPanel v-show="rightPanelTab === 'api'" ref="activityPanel" />
      </div>
    </aside>

    <!-- Settings dialog -->
    <AppSettingsDialog v-if="showSettings" @close="showSettings = false" />
  </div>
</template>

<style>
/* Default color variables (light mode) */
:root {
  --bg-primary: #ffffff;
  --bg-secondary: #f8f9fa;
  --bg-tertiary: #f0f2f5;
  --bg-input: #ffffff;
  --text-primary: #1a1a2e;
  --text-secondary: #5a5a72;
  --border-color: #e1e4e8;
  --accent-color: #5865f2;
  --accent-hover: #4752c4;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.05);
  --shadow-md: 0 2px 8px rgba(0, 0, 0, 0.08);
  --radius-sm: 4px;
  --radius-md: 6px;
  --radius-lg: 8px;
}

/* Dark mode support */
@media (prefers-color-scheme: dark) {
  :root {
    --bg-primary: #1e1e2e;
    --bg-secondary: #181825;
    --bg-tertiary: #242438;
    --bg-input: #2a2a3c;
    --text-primary: #e8e8f0;
    --text-secondary: #a0a0b8;
    --border-color: #3a3a4c;
    --accent-color: #7289da;
    --accent-hover: #5865f2;
    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.2);
    --shadow-md: 0 2px 8px rgba(0, 0, 0, 0.3);
  }
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html, body {
  height: 100%;
  overflow: hidden;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen',
    'Ubuntu', 'Cantarell', 'Fira Sans', 'Droid Sans', 'Helvetica Neue',
    sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  background-color: var(--bg-secondary);
}

#app {
  height: 100vh;
  width: 100vw;
  overflow: hidden;
}

.app-container {
  display: flex;
  flex-direction: row;
  height: 100%;
  width: 100%;
}

.left-panel {
  width: 260px;
  min-width: 260px;
  max-width: 260px;
  height: 100%;
  background-color: var(--bg-primary);
  border-right: 1px solid var(--border-color);
  overflow: hidden;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-sm);
}

.left-panel-content {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

.left-panel-divider {
  height: 0;
  border-top: 1px solid var(--border-color);
  margin: 0;
}

.settings-btn {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  width: 100%;
  padding: 0.5rem 0.6rem;
  border: none;
  background-color: var(--bg-secondary);
  border-top: 1px solid var(--border-color);
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 600;
  transition: all 0.12s;
}

.settings-btn:hover {
  background-color: var(--bg-tertiary);
  color: var(--accent-color);
}

.settings-btn-icon {
  font-size: 1rem;
  color: var(--accent-color);
}

.settings-btn-text {
  flex: 1;
  text-align: left;
}

.chat-panel {
  flex: 1;
  height: 100%;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.panel-toggle-btn {
  width: 20px;
  min-width: 20px;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background-color: var(--bg-tertiary);
  border-left: 1px solid var(--border-color);
  border-right: 1px solid var(--border-color);
  cursor: pointer;
  padding: 0;
  color: var(--text-secondary);
  transition: background-color 0.15s ease;
  flex-shrink: 0;
}

.panel-toggle-btn:hover {
  background-color: var(--bg-secondary);
  color: var(--accent-color);
}

.panel-toggle-btn .material-icons {
  font-size: 1rem;
}

.right-panel {
  width: 340px;
  min-width: 340px;
  max-width: 340px;
  height: 100%;
  background-color: var(--bg-primary);
  border-left: 1px solid var(--border-color);
  overflow: hidden;
  flex-shrink: 0;
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
}

.right-panel-tabs {
  display: flex;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.tab-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.25rem;
  padding: 0.5rem 0.5rem;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  border-bottom: 2px solid transparent;
}

.tab-btn:hover {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}

.tab-btn.active {
  color: var(--accent-color);
  border-bottom-color: var(--accent-color);
  background-color: var(--bg-secondary);
}

.tab-icon {
  font-size: 1rem;
}

.right-panel-content {
  flex: 1;
  overflow: hidden;
  min-height: 0;
}
</style>