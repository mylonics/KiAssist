<script setup lang="ts">
import { ref, computed } from 'vue';
import type { ImportedComponent } from '../types/importer';
import { useAppSettings } from '../composables/useAppSettings';

// -----------------------------------------------------------------------
// API helper
// -----------------------------------------------------------------------

function getApi() {
  return (window as any).pywebview?.api ?? null;
}

// -----------------------------------------------------------------------
// Emits — same pattern as SymbolImporter
// -----------------------------------------------------------------------

const emit = defineEmits<{
  (e: 'component-imported', component: ImportedComponent, warnings: string[]): void;
}>();

// -----------------------------------------------------------------------
// Settings
// -----------------------------------------------------------------------

const { settings: appSettings } = useAppSettings();

// -----------------------------------------------------------------------
// Types
// -----------------------------------------------------------------------

type ComponentType = 'resistor' | 'capacitor' | 'inductor' | 'diode';

// -----------------------------------------------------------------------
// State
// -----------------------------------------------------------------------

const selectedType = ref<ComponentType | null>(null);
const mpnInput = ref('');

// Import
const importing = ref(false);
const importStatus = ref('');
const importError = ref('');

// Progress polling
let progressTimer: ReturnType<typeof setInterval> | null = null;

// -----------------------------------------------------------------------
// Component type buttons
// -----------------------------------------------------------------------

const componentTypes: { type: ComponentType; label: string; icon: string }[] = [
  { type: 'resistor',  label: 'Resistor',  icon: 'R' },
  { type: 'capacitor', label: 'Capacitor', icon: 'C' },
  { type: 'inductor',  label: 'Inductor',  icon: 'L' },
  { type: 'diode',     label: 'Diode',     icon: 'D' },
];

const canImport = computed(() => !!selectedType.value && !!mpnInput.value.trim());

// -----------------------------------------------------------------------
// Actions
// -----------------------------------------------------------------------

function selectType(type: ComponentType) {
  if (selectedType.value === type) {
    selectedType.value = null;
    resetState();
    return;
  }
  selectedType.value = type;
  importError.value = '';
}

function resetState() {
  mpnInput.value = '';
  importError.value = '';
  importing.value = false;
}

/** Reset to accept the next MPN (keep type selected). */
function resetForNext() {
  mpnInput.value = '';
  importError.value = '';
}

async function importVariant() {
  if (!canImport.value) return;

  const api = getApi();
  if (!api) { importError.value = 'Backend not available'; return; }

  importing.value = true;
  importError.value = '';
  importStatus.value = 'Starting import…';

  // Poll for progress
  progressTimer = setInterval(async () => {
    try {
      const p = await api.importer_import_progress();
      if (p?.status) importStatus.value = p.status;
    } catch { /* ignore */ }
  }, 400);

  try {
    // Build default symbol overrides from settings
    const defaultSymbols = appSettings.value.variantDefaultSymbols || {};

    const r = await api.importer_variant_import(
      selectedType.value,
      mpnInput.value.trim(),
      Object.keys(defaultSymbols).length ? defaultSymbols : null,
    );

    if (r?.success && r.component) {
      // Emit to parent — renders in ImporterDetails (same as SymbolImporter)
      const comp: ImportedComponent = r.component;
      const warns: string[] = r.warnings ?? [];
      emit('component-imported', comp, warns);
      resetForNext();
    } else {
      importError.value = r?.error || 'Import failed';
    }
  } catch (e: any) {
    importError.value = e.message || 'Import failed';
  } finally {
    importing.value = false;
    importStatus.value = '';
    if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
  }
}
</script>

<template>
  <div class="variant-panel">
    <div class="variant-header">
      <span class="material-icons header-icon">library_add</span>
      <span class="header-title">Quick Variant Import</span>
    </div>

    <div class="variant-body">
      <!-- Component type buttons -->
      <div class="type-grid">
        <button
          v-for="ct in componentTypes"
          :key="ct.type"
          :class="['type-btn', { active: selectedType === ct.type }]"
          @click="selectType(ct.type)"
          :disabled="importing"
        >
          <span class="type-icon">{{ ct.icon }}</span>
          <span class="type-label">{{ ct.label }}</span>
        </button>
      </div>

      <!-- MPN input (shown when type selected) -->
      <div v-if="selectedType" class="variant-form">
        <div class="input-row">
          <label class="field-label">MPN</label>
          <input
            v-model="mpnInput"
            class="text-input"
            placeholder="e.g. RC0603FR-074K7L"
            :disabled="importing"
            @keydown.enter="importVariant"
          />
        </div>

        <button
          class="action-btn primary full-width"
          @click="importVariant"
          :disabled="!canImport || importing"
        >
          <span class="material-icons">{{ importing ? 'sync' : 'search' }}</span>
          {{ importing ? (importStatus || 'Importing…') : 'Import Variant' }}
        </button>

        <!-- Error notices -->
        <div v-if="importError" class="notice error">
          <span class="material-icons">error_outline</span>
          <span>{{ importError }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ===== Panel Container ===== */
.variant-panel {
  display: flex;
  flex-direction: column;
}

/* ===== Header ===== */
.variant-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.6rem 0.75rem;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
  background-color: var(--bg-secondary);
}

.header-icon {
  font-size: 1.1rem;
  color: var(--accent-color);
}

.header-title {
  font-weight: 700;
  font-size: 0.82rem;
  color: var(--text-primary);
  flex: 1;
}

/* ===== Body ===== */
.variant-body {
  padding: 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

/* ===== Type Grid ===== */
.type-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.35rem;
}

.type-btn {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.4rem 0.55rem;
  border: 1.5px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-input);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
  font-size: 0.72rem;
  font-weight: 600;
}

.type-btn:hover:not(:disabled) {
  border-color: var(--accent-color);
  color: var(--text-primary);
  background: color-mix(in srgb, var(--accent-color) 8%, transparent);
}

.type-btn.active {
  border-color: var(--accent-color);
  background: color-mix(in srgb, var(--accent-color) 15%, transparent);
  color: var(--accent-color);
}

.type-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.type-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.4rem;
  height: 1.4rem;
  border-radius: 50%;
  background: var(--bg-tertiary);
  font-weight: 800;
  font-size: 0.75rem;
  flex-shrink: 0;
}

.type-btn.active .type-icon {
  background: var(--accent-color);
  color: #fff;
}

.type-label {
  font-size: 0.7rem;
}

/* ===== Form ===== */
.variant-form {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.input-row {
  display: flex;
  gap: 0.3rem;
  align-items: center;
}

.field-label {
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--text-secondary);
  min-width: 2.5rem;
  flex-shrink: 0;
}

.text-input {
  flex: 1;
  min-width: 0;
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background-color: var(--bg-input);
  color: var(--text-primary);
  font-size: 0.75rem;
  outline: none;
  transition: border-color 0.15s;
}

.text-input:focus {
  border-color: var(--accent-color);
}

.text-input:disabled {
  opacity: 0.6;
}

/* ===== Buttons ===== */
.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.3rem 0.6rem;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  font-size: 0.72rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
  flex-shrink: 0;
}

.action-btn .material-icons {
  font-size: 0.9rem;
}

.action-btn.primary {
  background-color: var(--accent-color);
  color: #fff;
}

.action-btn.primary:hover:not(:disabled) {
  background-color: var(--accent-hover);
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.action-btn.full-width {
  width: 100%;
  justify-content: center;
}

/* ===== Notices ===== */
.notice {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  padding: 0.4rem 0.5rem;
  border-radius: var(--radius-sm);
  font-size: 0.72rem;
  line-height: 1.4;
}

.notice .material-icons {
  font-size: 1rem;
  flex-shrink: 0;
  margin-top: 0.05rem;
}

.notice.error {
  background-color: color-mix(in srgb, #e74c3c 15%, transparent);
  color: #e74c3c;
  border: 1px solid color-mix(in srgb, #e74c3c 30%, transparent);
}

/* ===== Spinning animation ===== */
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.spinning {
  animation: spin 1s linear infinite;
}
</style>
