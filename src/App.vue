<script setup lang="ts">
import { ref } from 'vue';
import ChatBox from './components/ChatBox.vue';
import KiCadInstanceSelector from './components/KiCadInstanceSelector.vue';
import ApiActivityPanel from './components/ApiActivityPanel.vue';
import LlmActivityPanel from './components/LlmActivityPanel.vue';
import ProjectContextPanel from './components/ProjectContextPanel.vue';
import ComponentSearch from './components/ComponentSearch.vue';
import SymbolImporter from './components/SymbolImporter.vue';
import ImporterDetails from './components/ImporterDetails.vue';
import type { ImportedComponent } from './types/importer';

const activityPanel = ref<InstanceType<typeof ApiActivityPanel> | null>(null);
const chatBox = ref<InstanceType<typeof ChatBox> | null>(null);
const rightPanelCollapsed = ref(true);
const rightPanelTab = ref<'context' | 'llm' | 'api'>('context');
const leftPanelTab = ref<'kicad' | 'search' | 'importer'>('kicad');

// Importer details overlay state
const importedComponent = ref<ImportedComponent | null>(null);
const importWarnings = ref<string[]>([]);

function handleComponentImported(component: ImportedComponent, warnings: string[]) {
  importedComponent.value = component;
  importWarnings.value = warnings;
}

function handleImporterClose() {
  importedComponent.value = null;
  importWarnings.value = [];
}

function handleInsertInChat(text: string) {
  chatBox.value?.insertText(text);
}

function handleContextQuestionsReady(questions: Array<{ question: string; suggestions: string[] }>) {
  console.log('[App] handleContextQuestionsReady, questions:', questions.length, 'chatBox ref:', !!chatBox.value);
  chatBox.value?.startContextQA(questions);
}
</script>

<template>
  <div class="app-container">
    <aside class="left-panel">
      <div class="left-panel-tabs">
        <button
          :class="['left-tab-btn', { active: leftPanelTab === 'kicad' }]"
          @click="leftPanelTab = 'kicad'"
          title="KiCad instance selector"
        >
          <span class="material-icons tab-icon">developer_board</span>
          KiCad
        </button>
        <button
          :class="['left-tab-btn', { active: leftPanelTab === 'search' }]"
          @click="leftPanelTab = 'search'"
          title="Search for components"
        >
          <span class="material-icons tab-icon">search</span>
          Components
        </button>
        <button
          :class="['left-tab-btn', { active: leftPanelTab === 'importer' }]"
          @click="leftPanelTab = 'importer'"
          title="Symbol / Footprint Importer"
        >
          <span class="material-icons tab-icon">download</span>
          Import
        </button>
      </div>
      <div class="left-panel-content">
        <KiCadInstanceSelector
          v-show="leftPanelTab === 'kicad'"
          @context-questions-ready="handleContextQuestionsReady"
        />
        <ComponentSearch
          v-show="leftPanelTab === 'search'"
          @insert-in-chat="handleInsertInChat"
        />
        <SymbolImporter
          v-show="leftPanelTab === 'importer'"
          @component-imported="handleComponentImported"
        />
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

.left-panel-tabs {
  display: flex;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.left-tab-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.25rem;
  padding: 0.45rem 0.3rem;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.7rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  border-bottom: 2px solid transparent;
}

.left-tab-btn:hover {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}

.left-tab-btn.active {
  color: var(--accent-color);
  border-bottom-color: var(--accent-color);
  background-color: var(--bg-secondary);
}

.left-panel-content {
  flex: 1;
  overflow: hidden;
  min-height: 0;
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