<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useAppSettings, type FieldDefault, FIELD_DESCRIPTIONS } from '../composables/useAppSettings';
import { getApi } from '../composables/useApi';

// -----------------------------------------------------------------------
// Props & Emits
// -----------------------------------------------------------------------

const emit = defineEmits<{
  (e: 'close'): void;
}>();

// -----------------------------------------------------------------------
// Settings
// -----------------------------------------------------------------------

const {
  settings,
  saveFieldDefaultsToBackend,
} = useAppSettings();

// -----------------------------------------------------------------------
// Tabs
// -----------------------------------------------------------------------

const activeTab = ref<'fields' | 'libraries' | 'variants'>('fields');

// -----------------------------------------------------------------------
// Field defaults (local working copy)
// -----------------------------------------------------------------------

const fieldsCopy = ref<FieldDefault[]>([]);
const fieldsDirty = ref(false);
const fieldsSaving = ref(false);
const fieldsError = ref('');
const fieldsSuccess = ref('');
const newFieldKey = ref('');

function loadFieldsCopy() {
  fieldsCopy.value = settings.value.fieldDefaults.map(f => ({ ...f }));
  fieldsDirty.value = false;
  fieldsError.value = '';
  fieldsSuccess.value = '';
}

function toggleField(idx: number) {
  fieldsCopy.value[idx].enabled = !fieldsCopy.value[idx].enabled;
  fieldsDirty.value = true;
}

function removeField(idx: number) {
  fieldsCopy.value.splice(idx, 1);
  fieldsDirty.value = true;
}

function moveField(idx: number, direction: -1 | 1) {
  const target = idx + direction;
  if (target < 0 || target >= fieldsCopy.value.length) return;
  const temp = fieldsCopy.value[idx];
  fieldsCopy.value[idx] = fieldsCopy.value[target];
  fieldsCopy.value[target] = temp;
  fieldsDirty.value = true;
}

const editingIdx = ref<number | null>(null);

function startEditKey(idx: number) {
  editingIdx.value = idx;
}

function finishEditKey(idx: number) {
  fieldsCopy.value[idx].key = fieldsCopy.value[idx].key.trim();
  editingIdx.value = null;
  fieldsDirty.value = true;
}

function addField() {
  const key = newFieldKey.value.trim();
  if (!key) return;
  if (fieldsCopy.value.some(f => f.key.toLowerCase() === key.toLowerCase())) {
    fieldsError.value = `Field "${key}" already exists`;
    return;
  }
  fieldsCopy.value.push({ key, enabled: true, description: FIELD_DESCRIPTIONS[key] || undefined });
  newFieldKey.value = '';
  fieldsDirty.value = true;
  fieldsError.value = '';
}

async function saveFieldDefaults() {
  fieldsSaving.value = true;
  fieldsError.value = '';
  fieldsSuccess.value = '';
  // Apply to global settings
  settings.value.fieldDefaults = fieldsCopy.value.map(f => ({ ...f }));
  // Sync to backend
  const r = await saveFieldDefaultsToBackend();
  if (r?.success) {
    fieldsDirty.value = false;
    fieldsSuccess.value = 'Field defaults saved';
    setTimeout(() => { fieldsSuccess.value = ''; }, 2000);
  } else {
    fieldsError.value = r?.error || 'Save failed';
  }
  fieldsSaving.value = false;
}

// -----------------------------------------------------------------------
// Library defaults
// -----------------------------------------------------------------------

interface LibraryInfo {
  nickname: string;
  uri: string;
}

const symLibraries = ref<LibraryInfo[]>([]);
const fpLibraries = ref<LibraryInfo[]>([]);
const libsLoading = ref(false);
const libsSaving = ref(false);
const libsError = ref('');
const libsSuccess = ref('');

// Local working copies
const defaultSymLib = ref('');
const defaultFpLib = ref('');
const defaultModelsDir = ref('');

// 3D model dir history management
const newModelDir = ref('');

function loadLibsCopy() {
  defaultSymLib.value = settings.value.defaultSymLib;
  defaultFpLib.value = settings.value.defaultFpLib;
  defaultModelsDir.value = settings.value.defaultModelsDir;
}

async function loadLibraries() {
  const api = getApi();
  if (!api) return;
  libsLoading.value = true;
  try {
    const [symRes, fpRes] = await Promise.all([
      api.importer_get_sym_libraries?.() ?? { success: false },
      api.importer_get_fp_libraries?.() ?? { success: false },
    ]);
    if (symRes?.success) {
      symLibraries.value = (symRes.libraries || []).map((l: any) => ({
        nickname: l.nickname,
        uri: l.uri,
      }));
    }
    if (fpRes?.success) {
      fpLibraries.value = (fpRes.libraries || []).map((l: any) => ({
        nickname: l.nickname,
        uri: l.uri,
      }));
    }
  } catch { /* ignore */ }
  finally { libsLoading.value = false; }
}

function isKicadLib(uri: string): boolean {
  const kicadVars = ['KICAD_SYMBOL_DIR', 'KICAD_FOOTPRINT_DIR', 'KICAD_3DMODEL_DIR',
    'KICAD10_SYMBOL_DIR', 'KICAD10_FOOTPRINT_DIR', 'KICAD11_SYMBOL_DIR',
    'KICAD11_FOOTPRINT_DIR', 'KICAD12_SYMBOL_DIR', 'KICAD12_FOOTPRINT_DIR'];
  return kicadVars.some(v => uri.includes('${' + v + '}'));
}

function saveLibraryDefaults() {
  libsSaving.value = true;
  libsError.value = '';
  libsSuccess.value = '';
  settings.value.defaultSymLib = defaultSymLib.value;
  settings.value.defaultFpLib = defaultFpLib.value;
  settings.value.defaultModelsDir = defaultModelsDir.value;
  libsSaving.value = false;
  libsSuccess.value = 'Library defaults saved';
  setTimeout(() => { libsSuccess.value = ''; }, 2000);
}

async function browseModelsDir() {
  const api = getApi();
  if (!api) return;
  try {
    const r = await api.importer_browse_models_dir();
    if (r?.success && r.path) {
      defaultModelsDir.value = r.path;
    }
  } catch { /* ignore */ }
}

function addModelDirToHistory() {
  const dir = newModelDir.value.trim();
  if (!dir) return;
  if (!settings.value.modelsDirHistory.includes(dir)) {
    settings.value.modelsDirHistory.unshift(dir);
    if (settings.value.modelsDirHistory.length > 10) {
      settings.value.modelsDirHistory = settings.value.modelsDirHistory.slice(0, 10);
    }
  }
  newModelDir.value = '';
}

function removeModelDirFromHistory(idx: number) {
  settings.value.modelsDirHistory.splice(idx, 1);
}

// -----------------------------------------------------------------------
// Variant default symbols
// -----------------------------------------------------------------------

interface VariantSymbolEntry {
  type: string;
  label: string;
  library: string;
  symbol: string;
}

interface SymSearchResult {
  library: string;
  name: string;
  description?: string;
}

const variantSymbols = ref<VariantSymbolEntry[]>([]);
const variantsDirty = ref(false);
const variantsSuccess = ref('');

// Symbol search state per component type
const variantSearchQuery = ref<Record<string, string>>({});
const variantSearchResults = ref<Record<string, SymSearchResult[]>>({});
const variantSearchLoading = ref<Record<string, boolean>>({});
const variantSearchOpen = ref<Record<string, boolean>>({});
let variantSearchTimers: Record<string, ReturnType<typeof setTimeout>> = {};

function loadVariantsCopy() {
  const defs = settings.value.variantDefaultSymbols || {};
  const types = [
    { type: 'resistor',  label: 'Resistor' },
    { type: 'capacitor', label: 'Capacitor' },
    { type: 'inductor',  label: 'Inductor' },
    { type: 'diode',     label: 'Diode' },
  ];
  variantSymbols.value = types.map(t => ({
    type: t.type,
    label: t.label,
    library: defs[t.type]?.library || '',
    symbol: defs[t.type]?.symbol || '',
  }));
  // Initialise per-type search state so Vue can track the keys
  const q: Record<string, string> = {};
  const r: Record<string, SymSearchResult[]> = {};
  const l: Record<string, boolean> = {};
  const o: Record<string, boolean> = {};
  for (const t of types) {
    q[t.type] = '';
    r[t.type] = [];
    l[t.type] = false;
    o[t.type] = false;
  }
  variantSearchQuery.value = q;
  variantSearchResults.value = r;
  variantSearchLoading.value = l;
  variantSearchOpen.value = o;
  variantsDirty.value = false;
}

function variantDisplayValue(vs: VariantSymbolEntry): string {
  if (vs.library && vs.symbol) return `${vs.library}:${vs.symbol}`;
  return '';
}

function onVariantSearchInput(type: string) {
  const query = (variantSearchQuery.value[type] ?? '').trim();
  if (variantSearchTimers[type]) clearTimeout(variantSearchTimers[type]);
  if (!query) {
    variantSearchResults.value = { ...variantSearchResults.value, [type]: [] };
    return;
  }
  variantSearchLoading.value = { ...variantSearchLoading.value, [type]: true };
  variantSearchOpen.value = { ...variantSearchOpen.value, [type]: true };
  variantSearchTimers[type] = setTimeout(() => doVariantSymSearch(type, query), 300);
}

async function doVariantSymSearch(type: string, query: string) {
  const api = getApi();
  if (!api) {
    variantSearchLoading.value = { ...variantSearchLoading.value, [type]: false };
    return;
  }
  try {
    const r = await api.importer_search_symbols(query);
    variantSearchResults.value = {
      ...variantSearchResults.value,
      [type]: r?.success ? (r.results ?? []) : [],
    };
  } catch {
    variantSearchResults.value = { ...variantSearchResults.value, [type]: [] };
  } finally {
    variantSearchLoading.value = { ...variantSearchLoading.value, [type]: false };
  }
}

function selectVariantSymbol(type: string, result: SymSearchResult) {
  const entry = variantSymbols.value.find(v => v.type === type);
  if (entry) {
    entry.library = result.library;
    entry.symbol = result.name;
  }
  variantSearchOpen.value = { ...variantSearchOpen.value, [type]: false };
  variantSearchQuery.value = { ...variantSearchQuery.value, [type]: '' };
  variantSearchResults.value = { ...variantSearchResults.value, [type]: [] };
  variantsDirty.value = true;
}

function clearVariantSymbol(type: string) {
  const entry = variantSymbols.value.find(v => v.type === type);
  if (entry) {
    entry.library = '';
    entry.symbol = '';
  }
  variantsDirty.value = true;
}

function closeVariantSearch(type: string) {
  variantSearchOpen.value = { ...variantSearchOpen.value, [type]: false };
  variantSearchResults.value = { ...variantSearchResults.value, [type]: [] };
  variantSearchQuery.value = { ...variantSearchQuery.value, [type]: '' };
}

function saveVariantDefaults() {
  const result: Record<string, { library: string; symbol: string }> = {};
  for (const v of variantSymbols.value) {
    if (v.library.trim() && v.symbol.trim()) {
      result[v.type] = { library: v.library.trim(), symbol: v.symbol.trim() };
    }
  }
  settings.value.variantDefaultSymbols = result;
  variantsDirty.value = false;
  variantsSuccess.value = 'Variant symbol defaults saved';
  setTimeout(() => { variantsSuccess.value = ''; }, 2000);
}

// -----------------------------------------------------------------------
// Lifecycle
// -----------------------------------------------------------------------

onMounted(async () => {
  loadFieldsCopy();
  loadLibsCopy();
  loadVariantsCopy();
  await loadLibraries();
});
</script>

<template>
  <div class="settings-overlay" @click.self="emit('close')">
    <div class="settings-dialog">
      <!-- Header -->
      <div class="dialog-header">
        <span class="material-icons dialog-header-icon">settings</span>
        <span class="dialog-title">Settings</span>
        <button class="dialog-close-btn" @click="emit('close')" title="Close">
          <span class="material-icons">close</span>
        </button>
      </div>

      <!-- Tabs -->
      <div class="dialog-tabs">
        <button
          :class="['dialog-tab', { active: activeTab === 'fields' }]"
          @click="activeTab = 'fields'"
        >
          <span class="material-icons tab-icon">text_fields</span>
          Symbol Field Defaults
        </button>
        <button
          :class="['dialog-tab', { active: activeTab === 'libraries' }]"
          @click="activeTab = 'libraries'"
        >
          <span class="material-icons tab-icon">folder</span>
          Default Libraries
        </button>
        <button
          :class="['dialog-tab', { active: activeTab === 'variants' }]"
          @click="activeTab = 'variants'"
        >
          <span class="material-icons tab-icon">library_add</span>
          Variant Symbols
        </button>
      </div>

      <!-- Content -->
      <div class="dialog-body">
        <!-- ===== Symbol Field Defaults Tab ===== -->
        <div v-if="activeTab === 'fields'" class="tab-content">
          <p class="settings-hint">
            Configure which extra fields appear by default on imported symbols.
            Double-click a field name to rename it. The description shows the original purpose of the field.
          </p>

          <!-- Field rows -->
          <div class="field-list">
            <div
              v-for="(f, idx) in fieldsCopy"
              :key="idx"
              :class="['field-row', { disabled: !f.enabled }]"
            >
              <button
                class="field-toggle"
                @click="toggleField(idx)"
                :title="f.enabled ? 'Disable field' : 'Enable field'"
              >
                <span class="material-icons">{{ f.enabled ? 'check_box' : 'check_box_outline_blank' }}</span>
              </button>

              <!-- Editable key name -->
              <input
                v-if="editingIdx === idx"
                v-model="f.key"
                class="field-key-input"
                @blur="finishEditKey(idx)"
                @keydown.enter="finishEditKey(idx)"
                @keydown.escape="finishEditKey(idx)"
                autofocus
              />
              <span
                v-else
                class="field-key"
                @dblclick="startEditKey(idx)"
                title="Double-click to rename"
              >{{ f.key }}</span>

              <!-- Non-editable description -->
              <span v-if="f.description" class="field-description" :title="f.description">
                {{ f.description }}
              </span>

              <!-- Reorder buttons -->
              <button class="field-action-btn" @click="moveField(idx, -1)" :disabled="idx === 0" title="Move up">
                <span class="material-icons">arrow_drop_up</span>
              </button>
              <button class="field-action-btn" @click="moveField(idx, 1)" :disabled="idx === fieldsCopy.length - 1" title="Move down">
                <span class="material-icons">arrow_drop_down</span>
              </button>

              <!-- Remove -->
              <button class="field-action-btn remove-btn" @click="removeField(idx)" title="Remove field">
                <span class="material-icons">close</span>
              </button>
            </div>
          </div>

          <!-- Add new field -->
          <div class="add-field-row">
            <input
              v-model="newFieldKey"
              class="text-input"
              placeholder="New field name…"
              @keydown.enter="addField"
            />
            <button class="action-btn secondary" @click="addField" :disabled="!newFieldKey.trim()" title="Add field">
              <span class="material-icons">add</span>
            </button>
          </div>

          <!-- Save -->
          <button
            class="action-btn primary full-width"
            @click="saveFieldDefaults"
            :disabled="fieldsSaving || !fieldsDirty"
          >
            <span class="material-icons">save</span>
            {{ fieldsSaving ? 'Saving…' : (fieldsDirty ? 'Save Field Defaults' : 'Saved') }}
          </button>

          <!-- Messages -->
          <div v-if="fieldsSuccess" class="notice success">
            <span class="material-icons">check_circle</span>
            {{ fieldsSuccess }}
          </div>
          <div v-if="fieldsError" class="notice error">
            <span class="material-icons">error_outline</span>
            {{ fieldsError }}
          </div>
        </div>

        <!-- ===== Default Libraries Tab ===== -->
        <div v-if="activeTab === 'libraries'" class="tab-content">
          <p class="settings-hint">
            Set default libraries for saving imported components. These will be pre-selected in the
            Save to Library panel. The last-used library will override the default until changed.
          </p>

          <div v-if="libsLoading" class="empty-hint">
            <span class="material-icons spinning">autorenew</span>
            Loading libraries…
          </div>

          <template v-else>
            <!-- Default symbol library -->
            <label class="lib-label">Default Symbol Library</label>
            <select v-model="defaultSymLib" class="lib-select">
              <option value="">— None (select each time) —</option>
              <optgroup label="Custom Libraries">
                <template v-for="lib in symLibraries" :key="lib.nickname">
                  <option v-if="!isKicadLib(lib.uri)" :value="lib.nickname">{{ lib.nickname }}</option>
                </template>
              </optgroup>
              <optgroup label="KiCad Libraries">
                <template v-for="lib in symLibraries" :key="lib.nickname">
                  <option v-if="isKicadLib(lib.uri)" :value="lib.nickname">{{ lib.nickname }}</option>
                </template>
              </optgroup>
            </select>

            <!-- Default footprint library -->
            <label class="lib-label">Default Footprint Library</label>
            <select v-model="defaultFpLib" class="lib-select">
              <option value="">— None (select each time) —</option>
              <optgroup label="Custom Libraries">
                <template v-for="lib in fpLibraries" :key="lib.nickname">
                  <option v-if="!isKicadLib(lib.uri)" :value="lib.nickname">{{ lib.nickname }}</option>
                </template>
              </optgroup>
              <optgroup label="KiCad Libraries">
                <template v-for="lib in fpLibraries" :key="lib.nickname">
                  <option v-if="isKicadLib(lib.uri)" :value="lib.nickname">{{ lib.nickname }}</option>
                </template>
              </optgroup>
            </select>

            <!-- Default 3D models directory -->
            <label class="lib-label">Default 3D Models Directory</label>
            <div class="dir-row">
              <input
                v-model="defaultModelsDir"
                class="text-input dir-input"
                placeholder="Auto: <footprint_lib>/3dmodels"
              />
              <button class="action-btn secondary browse-btn" @click="browseModelsDir" title="Browse">
                <span class="material-icons">folder_open</span>
              </button>
            </div>

            <!-- 3D model directory history -->
            <label class="lib-label" style="margin-top: 1rem">
              Saved 3D Model Directories
              <span class="label-hint">(shown in dropdown when saving)</span>
            </label>
            <div v-if="settings.modelsDirHistory.length" class="dir-history">
              <div v-for="(dir, idx) in settings.modelsDirHistory" :key="dir" class="dir-history-row">
                <span class="dir-history-path" :title="dir">{{ dir }}</span>
                <button class="field-action-btn remove-btn" @click="removeModelDirFromHistory(idx)" title="Remove">
                  <span class="material-icons">close</span>
                </button>
              </div>
            </div>
            <div v-else class="empty-hint">No saved directories yet</div>

            <div class="add-field-row">
              <input
                v-model="newModelDir"
                class="text-input"
                placeholder="Add directory path…"
                @keydown.enter="addModelDirToHistory"
              />
              <button class="action-btn secondary" @click="addModelDirToHistory" :disabled="!newModelDir.trim()" title="Add">
                <span class="material-icons">add</span>
              </button>
            </div>

            <!-- Save -->
            <button class="action-btn primary full-width" @click="saveLibraryDefaults" :disabled="libsSaving">
              <span class="material-icons">save</span>
              {{ libsSaving ? 'Saving…' : 'Save Library Defaults' }}
            </button>

            <!-- Messages -->
            <div v-if="libsSuccess" class="notice success">
              <span class="material-icons">check_circle</span>
              {{ libsSuccess }}
            </div>
            <div v-if="libsError" class="notice error">
              <span class="material-icons">error_outline</span>
              {{ libsError }}
            </div>
          </template>
        </div>

        <!-- ===== Variant Symbols Tab ===== -->
        <div v-if="activeTab === 'variants'" class="tab-content">
          <p class="settings-hint">
            Set the default KiCad symbol used for each component type when importing variants.
            The symbol graphics/pins are reused — only field values are updated per variant.
          </p>

          <div class="variant-symbol-list">
            <div
              v-for="vs in variantSymbols"
              :key="vs.type"
              class="variant-symbol-row"
            >
              <span class="variant-type-label">{{ vs.label }}</span>

              <!-- Current selection display -->
              <div v-if="variantDisplayValue(vs) && !variantSearchOpen[vs.type]" class="variant-selected">
                <span class="variant-selected-text" :title="variantDisplayValue(vs)">
                  {{ variantDisplayValue(vs) }}
                </span>
                <button
                  class="field-action-btn"
                  @click="variantSearchOpen = { ...variantSearchOpen, [vs.type]: true }; variantSearchQuery = { ...variantSearchQuery, [vs.type]: '' }"
                  title="Change symbol"
                >
                  <span class="material-icons">edit</span>
                </button>
                <button
                  class="field-action-btn remove-btn"
                  @click="clearVariantSymbol(vs.type)"
                  title="Clear"
                >
                  <span class="material-icons">close</span>
                </button>
              </div>

              <!-- Search input -->
              <div v-else class="variant-search-wrapper">
                <div class="variant-search-input-row">
                  <span class="material-icons variant-search-icon">search</span>
                  <input
                    v-model="variantSearchQuery[vs.type]"
                    class="text-input variant-search-input"
                    :placeholder="`Search symbols (e.g. ${vs.type === 'resistor' ? 'R_Small' : vs.type === 'capacitor' ? 'C_Small' : vs.type === 'inductor' ? 'L_Small' : 'D_Small'})…`"
                    @input="onVariantSearchInput(vs.type)"
                    @keydown.escape="closeVariantSearch(vs.type)"
                  />
                  <span
                    v-if="variantSearchLoading[vs.type]"
                    class="material-icons spinning variant-search-spinner"
                  >autorenew</span>
                  <button
                    v-if="variantDisplayValue(vs)"
                    class="field-action-btn"
                    @click="closeVariantSearch(vs.type)"
                    title="Cancel"
                  >
                    <span class="material-icons">close</span>
                  </button>
                </div>

                <!-- Search results dropdown -->
                <div
                  v-if="variantSearchResults[vs.type]?.length"
                  class="variant-search-results"
                >
                  <div
                    v-for="result in variantSearchResults[vs.type]"
                    :key="`${result.library}:${result.name}`"
                    class="variant-search-result"
                    @click="selectVariantSymbol(vs.type, result)"
                  >
                    <span class="variant-result-name">{{ result.name }}</span>
                    <span class="variant-result-lib">{{ result.library }}</span>
                    <span v-if="result.description" class="variant-result-desc">{{ result.description }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Save -->
          <button
            class="action-btn primary full-width"
            @click="saveVariantDefaults"
            :disabled="!variantsDirty"
          >
            <span class="material-icons">save</span>
            {{ variantsDirty ? 'Save Variant Defaults' : 'Saved' }}
          </button>

          <!-- Messages -->
          <div v-if="variantsSuccess" class="notice success">
            <span class="material-icons">check_circle</span>
            {{ variantsSuccess }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.settings-dialog {
  width: 540px;
  max-width: 90vw;
  max-height: 85vh;
  background-color: var(--bg-primary);
  border-radius: var(--radius-lg);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Header */
.dialog-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--border-color);
  background-color: var(--bg-secondary);
  flex-shrink: 0;
}

.dialog-header-icon {
  font-size: 1.2rem;
  color: var(--accent-color);
}

.dialog-title {
  font-weight: 700;
  font-size: 1rem;
  color: var(--text-primary);
  flex: 1;
}

.dialog-close-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.8rem;
  height: 1.8rem;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.12s;
}

.dialog-close-btn:hover {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}

/* Tabs */
.dialog-tabs {
  display: flex;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.dialog-tab {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.35rem;
  padding: 0.6rem 0.75rem;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.8rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  border-bottom: 2px solid transparent;
}

.dialog-tab:hover {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}

.dialog-tab.active {
  color: var(--accent-color);
  border-bottom-color: var(--accent-color);
  background-color: var(--bg-secondary);
}

.tab-icon {
  font-size: 1rem;
}

/* Body */
.dialog-body {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

.tab-content {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.settings-hint {
  font-size: 0.75rem;
  color: var(--text-secondary);
  line-height: 1.5;
  margin: 0 0 0.25rem 0;
}

/* Field list */
.field-list {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.field-row {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.3rem 0.45rem;
  background: var(--bg-input);
  border-bottom: 1px solid var(--border-color);
  transition: opacity 0.15s;
}

.field-row:last-child {
  border-bottom: none;
}

.field-row.disabled {
  opacity: 0.5;
}

.field-toggle {
  display: flex;
  align-items: center;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--accent-color);
  padding: 0;
  flex-shrink: 0;
}

.field-toggle .material-icons {
  font-size: 1.1rem;
}

.field-key {
  flex: 1;
  font-weight: 600;
  font-size: 0.8rem;
  color: var(--text-primary);
  cursor: default;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 0.15rem 0.25rem;
  border-radius: var(--radius-sm);
}

.field-key:hover {
  background-color: var(--bg-tertiary);
}

.field-description {
  font-size: 0.7rem;
  color: var(--text-secondary);
  font-style: italic;
  opacity: 0.75;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-left: 0.15rem;
  flex-shrink: 1;
  min-width: 0;
}

.field-key-input {
  flex: 1;
  font-weight: 600;
  font-size: 0.8rem;
  color: var(--text-primary);
  border: 1px solid var(--accent-color);
  border-radius: var(--radius-sm);
  background: var(--bg-input);
  padding: 0.15rem 0.25rem;
  outline: none;
  min-width: 0;
}

.field-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--text-secondary);
  padding: 0;
  width: 1.2rem;
  height: 1.2rem;
  flex-shrink: 0;
  border-radius: 2px;
  transition: all 0.12s;
}

.field-action-btn:hover:not(:disabled) {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}

.field-action-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.field-action-btn .material-icons {
  font-size: 1rem;
}

.field-action-btn.remove-btn:hover:not(:disabled) {
  background-color: color-mix(in srgb, #e74c3c 15%, transparent);
  color: #e74c3c;
}

/* Add field row */
.add-field-row {
  display: flex;
  gap: 0.3rem;
  align-items: center;
}

/* Shared input */
.text-input {
  flex: 1;
  min-width: 0;
  padding: 0.35rem 0.5rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background-color: var(--bg-input);
  color: var(--text-primary);
  font-size: 0.8rem;
  outline: none;
  transition: border-color 0.15s;
}

.text-input:focus {
  border-color: var(--accent-color);
}

/* Library defaults */
.lib-label {
  display: block;
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--text-secondary);
  margin-top: 0.6rem;
}

.label-hint {
  font-weight: 400;
  font-style: italic;
  font-size: 0.68rem;
}

.lib-select {
  width: 100%;
  padding: 0.4rem 0.5rem;
  font-size: 0.8rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background-color: var(--bg-input);
  color: var(--text-primary);
  margin-top: 0.2rem;
}

.dir-row {
  display: flex;
  gap: 0;
  margin-top: 0.2rem;
}

.dir-input {
  border-top-right-radius: 0 !important;
  border-bottom-right-radius: 0 !important;
}

.browse-btn {
  border-top-left-radius: 0 !important;
  border-bottom-left-radius: 0 !important;
  border-left: none !important;
  padding: 0.35rem 0.5rem !important;
}

.browse-btn .material-icons {
  font-size: 0.95rem;
}

/* Directory history */
.dir-history {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  overflow: hidden;
  margin-top: 0.2rem;
}

.dir-history-row {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.25rem 0.4rem;
  background: var(--bg-input);
  border-bottom: 1px solid var(--border-color);
}

.dir-history-row:last-child {
  border-bottom: none;
}

.dir-history-path {
  flex: 1;
  font-size: 0.75rem;
  font-family: monospace;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Buttons */
.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.35rem 0.6rem;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  font-size: 0.78rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
  flex-shrink: 0;
}

.action-btn .material-icons {
  font-size: 0.95rem;
}

.action-btn.primary {
  background-color: var(--accent-color);
  color: #fff;
}

.action-btn.primary:hover:not(:disabled) {
  background-color: var(--accent-hover);
}

.action-btn.secondary {
  background-color: var(--bg-tertiary);
  color: var(--text-secondary);
  border-color: var(--border-color);
}

.action-btn.secondary:hover:not(:disabled) {
  background-color: var(--bg-secondary);
  color: var(--text-primary);
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.action-btn.full-width {
  width: 100%;
  justify-content: center;
}

/* Notices */
.empty-hint {
  color: var(--text-secondary);
  font-size: 0.75rem;
  text-align: center;
  padding: 0.5rem;
  font-style: italic;
}

.notice {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  padding: 0.4rem 0.5rem;
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
  line-height: 1.4;
}

.notice .material-icons {
  font-size: 1rem;
  flex-shrink: 0;
}

.notice.error {
  background-color: color-mix(in srgb, #e74c3c 15%, transparent);
  color: #e74c3c;
  border: 1px solid color-mix(in srgb, #e74c3c 30%, transparent);
}

.notice.success {
  background: color-mix(in srgb, #27ae60 10%, var(--bg-tertiary));
  color: #27ae60;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.spinning {
  animation: spin 1s linear infinite;
  font-size: 0.95rem;
}

/* ===== Variant Symbols ===== */
.variant-symbol-list {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  margin-bottom: 0.75rem;
}

.variant-symbol-row {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.variant-type-label {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--text-secondary);
}

.variant-selected {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.3rem 0.5rem;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  border-left: 3px solid var(--accent-color);
}

.variant-selected-text {
  flex: 1;
  font-size: 0.75rem;
  font-family: monospace;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.variant-search-wrapper {
  position: relative;
}

.variant-search-input-row {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-input);
  padding: 0 0.35rem;
}

.variant-search-input-row:focus-within {
  border-color: var(--accent-color);
}

.variant-search-icon {
  font-size: 0.9rem;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.variant-search-input {
  border: none !important;
  background: transparent !important;
  padding: 0.3rem 0.25rem !important;
  flex: 1;
  min-width: 0;
}

.variant-search-input:focus {
  border: none !important;
  outline: none;
}

.variant-search-spinner {
  font-size: 0.85rem;
  color: var(--text-secondary);
}

.variant-search-results {
  max-height: 180px;
  overflow-y: auto;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-top: none;
  border-radius: 0 0 var(--radius-sm) var(--radius-sm);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.variant-search-result {
  display: flex;
  flex-wrap: wrap;
  gap: 0.2rem 0.5rem;
  padding: 0.35rem 0.5rem;
  cursor: pointer;
  transition: background 0.1s;
}

.variant-search-result:hover {
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
}

.variant-result-name {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--text-primary);
}

.variant-result-lib {
  font-size: 0.65rem;
  color: var(--text-secondary);
  font-family: monospace;
}

.variant-result-desc {
  width: 100%;
  font-size: 0.62rem;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
