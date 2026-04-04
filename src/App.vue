<script setup lang="ts">
import { ref } from 'vue';
import ChatBox from './components/ChatBox.vue';
import KiCadInstanceSelector from './components/KiCadInstanceSelector.vue';
import ApiActivityPanel from './components/ApiActivityPanel.vue';

const activityPanel = ref<InstanceType<typeof ApiActivityPanel> | null>(null);
const rightPanelCollapsed = ref(false);
</script>

<template>
  <div class="app-container">
    <aside class="left-panel">
      <KiCadInstanceSelector />
    </aside>
    <main class="chat-panel">
      <ChatBox :activityPanel="activityPanel" />
    </main>
    <button
      class="panel-toggle-btn"
      @click="rightPanelCollapsed = !rightPanelCollapsed"
      :title="rightPanelCollapsed ? 'Show API Activity' : 'Hide API Activity'"
    >
      <span class="material-icons">{{ rightPanelCollapsed ? 'chevron_left' : 'chevron_right' }}</span>
    </button>
    <aside v-if="!rightPanelCollapsed" class="right-panel">
      <ApiActivityPanel ref="activityPanel" />
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
  overflow-y: auto;
  flex-shrink: 0;
  box-shadow: var(--shadow-sm);
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
}
</style>