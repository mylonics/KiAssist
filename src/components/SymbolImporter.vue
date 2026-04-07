<script setup lang="ts">
import { ref, onMounted } from 'vue';

// -----------------------------------------------------------------------
// Types
// -----------------------------------------------------------------------

type ImportMethod = 'lcsc' | 'zip' | 'kicad_lib';

interface ImportedFields {
  mpn: string;
  manufacturer: string;
  digikey_pn: string;
  mouser_pn: string;
  lcsc_pn: string;
  value: string;
  reference: string;
  footprint: string;
  datasheet: string;
  description: string;
  package: string;
  extra: Record<string, string>;
}

interface ImportedComponent {
  name: string;
  import_method: string;
  source_info: string;
  symbol_path: string;
  footprint_path: string;
  model_paths: string[];
  fields: ImportedFields;
}

interface SearchResult {
  library: string;
  name: string;
  description?: string;
  value?: string;
  footprint?: string;
  path?: string;
}

// -----------------------------------------------------------------------
// State
// -----------------------------------------------------------------------

const activeMethod = ref<ImportMethod>('lcsc');
const loading = ref(false);
const error = ref('');
const warnings = ref<string[]>([]);
const resultComponent = ref<ImportedComponent | null>(null);
const lcscAvailable = ref(true);

// LCSC
const lcscId = ref('');

// ZIP
const zipPath = ref('');

// KiCad lib search
const searchQuery = ref('');
const searchLibrary = ref('');
const searchResults = ref<SearchResult[]>([]);
const selectedResult = ref<SearchResult | null>(null);
const searching = ref(false);

// Output paths
const targetSymLib = ref('');
const targetFpLibDir = ref('');
const overwrite = ref(false);
const showAdvanced = ref(false);

// -----------------------------------------------------------------------
// Lifecycle
// -----------------------------------------------------------------------

onMounted(async () => {
  const api = (window as any).pywebview?.api;
  if (!api) return;
  try {
    const r = await api.importer_lcsc_available();
    lcscAvailable.value = r?.available ?? false;
  } catch {
    lcscAvailable.value = false;
  }
});

// -----------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------

function getApi() {
  return (window as any).pywebview?.api ?? null;
}

function resetResult() {
  resultComponent.value = null;
  error.value = '';
  warnings.value = [];
}

// -----------------------------------------------------------------------
// LCSC import
// -----------------------------------------------------------------------

async function importLcsc() {
  const api = getApi();
  if (!api) { error.value = 'Backend not available'; return; }
  if (!lcscId.value.trim()) { error.value = 'Please enter an LCSC part number'; return; }

  resetResult();
  loading.value = true;
  try {
    const r = await api.importer_import_lcsc(
      lcscId.value.trim(),
      targetSymLib.value.trim(),
      targetFpLibDir.value.trim(),
      '',
      overwrite.value,
    );
    handleResult(r);
  } catch (e: any) {
    error.value = e.message || 'Import failed';
  } finally {
    loading.value = false;
  }
}

// -----------------------------------------------------------------------
// ZIP import
// -----------------------------------------------------------------------

async function browseZip() {
  const api = getApi();
  if (!api) return;
  try {
    const r = await api.importer_browse_zip();
    if (r?.path) zipPath.value = r.path;
  } catch (e: any) {
    error.value = e.message || 'File dialog failed';
  }
}

async function importZip() {
  const api = getApi();
  if (!api) { error.value = 'Backend not available'; return; }
  if (!zipPath.value.trim()) { error.value = 'Please select a ZIP file'; return; }

  resetResult();
  loading.value = true;
  try {
    const r = await api.importer_import_zip(
      zipPath.value.trim(),
      targetSymLib.value.trim(),
      targetFpLibDir.value.trim(),
      '',
      overwrite.value,
    );
    handleResult(r);
  } catch (e: any) {
    error.value = e.message || 'Import failed';
  } finally {
    loading.value = false;
  }
}

// -----------------------------------------------------------------------
// KiCad lib search + import
// -----------------------------------------------------------------------

async function searchKicad() {
  const api = getApi();
  if (!api) { error.value = 'Backend not available'; return; }
  if (!searchQuery.value.trim()) { error.value = 'Please enter a search term'; return; }

  selectedResult.value = null;
  error.value = '';
  searching.value = true;
  try {
    const r = await api.importer_search_symbols(
      searchQuery.value.trim(),
      searchLibrary.value.trim(),
    );
    if (r?.success) {
      searchResults.value = r.results || [];
    } else {
      error.value = r?.error || 'Search failed';
      searchResults.value = [];
    }
  } catch (e: any) {
    error.value = e.message || 'Search failed';
    searchResults.value = [];
  } finally {
    searching.value = false;
  }
}

async function importFromKicad() {
  if (!selectedResult.value) { error.value = 'No symbol selected'; return; }
  const api = getApi();
  if (!api) { error.value = 'Backend not available'; return; }

  resetResult();
  loading.value = true;
  try {
    const r = await api.importer_import_from_kicad(
      selectedResult.value.name,
      selectedResult.value.library,
      targetSymLib.value.trim(),
      targetFpLibDir.value.trim(),
      '',
      overwrite.value,
    );
    handleResult(r);
  } catch (e: any) {
    error.value = e.message || 'Import failed';
  } finally {
    loading.value = false;
  }
}

// -----------------------------------------------------------------------
// Result handling
// -----------------------------------------------------------------------

function handleResult(r: any) {
  if (!r) { error.value = 'No response from backend'; return; }
  if (r.success) {
    resultComponent.value = r.component ?? null;
    warnings.value = r.warnings ?? [];
  } else {
    error.value = r.error || 'Import failed';
    warnings.value = r.warnings ?? [];
  }
}

// -----------------------------------------------------------------------
// Post-import actions
// -----------------------------------------------------------------------

async function openInKicad() {
  const api = getApi();
  if (!api || !resultComponent.value?.footprint_path) return;
  try {
    const r = await api.importer_open_in_kicad(resultComponent.value.footprint_path);
    if (!r?.success) error.value = r?.error || 'Could not open KiCad';
  } catch (e: any) {
    error.value = e.message || 'Failed';
  }
}

async function browseOutputDir(field: 'sym' | 'fp') {
  const api = getApi();
  if (!api) return;
  try {
    const r = await api.importer_browse_output_dir();
    if (r?.path) {
      if (field === 'sym') {
        // Place the .kicad_sym file directly in the selected directory;
        // use the existing filename stem if the user has already typed something.
        const stem = targetSymLib.value
          ? targetSymLib.value.replace(/^.*[\\/]/, '').replace(/\.kicad_sym$/i, '')
          : 'my_parts';
        targetSymLib.value = r.path + '/' + stem + '.kicad_sym';
      } else {
        const stem = targetFpLibDir.value
          ? targetFpLibDir.value.replace(/^.*[\\/]/, '').replace(/\.pretty$/i, '')
          : 'my_parts';
        targetFpLibDir.value = r.path + '/' + stem + '.pretty';
      }
    }
  } catch {
    // ignore
  }
}
</script>

<template>
  <div class="importer-panel">
    <div class="importer-header">
      <span class="material-icons header-icon">download</span>
      <span class="header-title">Symbol / Footprint Importer</span>
    </div>

    <!-- Method tabs -->
    <div class="method-tabs">
      <button
        :class="['method-tab', { active: activeMethod === 'lcsc' }]"
        @click="activeMethod = 'lcsc'; resetResult()"
        title="Import from LCSC via EasyEDA"
      >
        <span class="material-icons tab-icon">cloud_download</span>
        LCSC
      </button>
      <button
        :class="['method-tab', { active: activeMethod === 'zip' }]"
        @click="activeMethod = 'zip'; resetResult()"
        title="Import from ZIP (SnapEDA / Ultra Librarian)"
      >
        <span class="material-icons tab-icon">folder_zip</span>
        ZIP
      </button>
      <button
        :class="['method-tab', { active: activeMethod === 'kicad_lib' }]"
        @click="activeMethod = 'kicad_lib'; resetResult()"
        title="Reuse component from existing KiCad library"
      >
        <span class="material-icons tab-icon">library_books</span>
        KiCad Lib
      </button>
    </div>

    <div class="importer-body">

      <!-- ======== LCSC ======== -->
      <div v-show="activeMethod === 'lcsc'" class="method-section">
        <div v-if="!lcscAvailable" class="notice warn">
          <span class="material-icons">warning</span>
          <span>
            <strong>easyeda2kicad</strong> is not installed.<br>
            Install with: <code>pip install easyeda2kicad</code>
          </span>
        </div>

        <label class="field-label">LCSC Part Number</label>
        <div class="input-row">
          <input
            v-model="lcscId"
            class="text-input"
            placeholder="e.g. C14663"
            :disabled="loading || !lcscAvailable"
            @keydown.enter="importLcsc"
          />
          <button
            class="action-btn primary"
            @click="importLcsc"
            :disabled="loading || !lcscAvailable || !lcscId.trim()"
          >
            <span class="material-icons">download</span>
            {{ loading ? 'Importing…' : 'Import' }}
          </button>
        </div>
      </div>

      <!-- ======== ZIP ======== -->
      <div v-show="activeMethod === 'zip'" class="method-section">
        <label class="field-label">ZIP File (SnapEDA / Ultra Librarian)</label>
        <div class="input-row">
          <input
            v-model="zipPath"
            class="text-input"
            placeholder="Path to .zip file"
            :disabled="loading"
            @keydown.enter="importZip"
          />
          <button class="action-btn secondary" @click="browseZip" :disabled="loading">
            <span class="material-icons">folder_open</span>
          </button>
        </div>
        <button
          class="action-btn primary full-width"
          @click="importZip"
          :disabled="loading || !zipPath.trim()"
          style="margin-top: 0.5rem"
        >
          <span class="material-icons">download</span>
          {{ loading ? 'Importing…' : 'Import ZIP' }}
        </button>
      </div>

      <!-- ======== KiCad Lib ======== -->
      <div v-show="activeMethod === 'kicad_lib'" class="method-section">
        <label class="field-label">Search existing symbol libraries</label>
        <div class="input-row">
          <input
            v-model="searchQuery"
            class="text-input"
            placeholder="Part name / keyword"
            :disabled="searching"
            @keydown.enter="searchKicad"
          />
          <button
            class="action-btn primary"
            @click="searchKicad"
            :disabled="searching || !searchQuery.trim()"
          >
            <span class="material-icons">search</span>
            {{ searching ? '…' : 'Search' }}
          </button>
        </div>

        <div v-if="searchResults.length" class="search-results">
          <div
            v-for="r in searchResults"
            :key="r.library + ':' + r.name"
            :class="['search-row', { selected: selectedResult === r }]"
            @click="selectedResult = r"
            :title="r.description || r.name"
          >
            <span class="result-lib">{{ r.library }}</span>
            <span class="result-name">{{ r.name }}</span>
            <span v-if="r.description" class="result-desc">{{ r.description }}</span>
          </div>
        </div>
        <div v-else-if="!searching && searchQuery && !searchResults.length" class="empty-hint">
          No results found.
        </div>

        <button
          class="action-btn primary full-width"
          @click="importFromKicad"
          :disabled="loading || !selectedResult"
          style="margin-top: 0.5rem"
        >
          <span class="material-icons">add_circle_outline</span>
          {{ loading ? 'Importing…' : 'Import Selected' }}
        </button>
      </div>

      <!-- ======== Output options ======== -->
      <div class="advanced-section">
        <button
          class="toggle-advanced"
          @click="showAdvanced = !showAdvanced"
        >
          <span class="material-icons">{{ showAdvanced ? 'expand_less' : 'expand_more' }}</span>
          Output settings
        </button>
        <div v-if="showAdvanced" class="advanced-body">
          <label class="field-label">Symbol library (.kicad_sym)</label>
          <div class="input-row">
            <input v-model="targetSymLib" class="text-input" placeholder="Leave blank to preview only" />
            <button class="action-btn secondary" @click="browseOutputDir('sym')">
              <span class="material-icons">folder_open</span>
            </button>
          </div>

          <label class="field-label" style="margin-top: 0.5rem">Footprint library (.pretty dir)</label>
          <div class="input-row">
            <input v-model="targetFpLibDir" class="text-input" placeholder="Leave blank to preview only" />
            <button class="action-btn secondary" @click="browseOutputDir('fp')">
              <span class="material-icons">folder_open</span>
            </button>
          </div>

          <label class="checkbox-row" style="margin-top: 0.4rem">
            <input type="checkbox" v-model="overwrite" />
            <span>Overwrite existing entries</span>
          </label>
        </div>
      </div>

      <!-- ======== Status ======== -->
      <div v-if="error" class="notice error">
        <span class="material-icons">error_outline</span>
        {{ error }}
      </div>

      <div v-if="warnings.length" class="notice warn">
        <span class="material-icons">warning</span>
        <ul>
          <li v-for="w in warnings" :key="w">{{ w }}</li>
        </ul>
      </div>

      <!-- ======== Result ======== -->
      <div v-if="resultComponent" class="result-card">
        <div class="result-header">
          <span class="material-icons" style="color: var(--accent-color)">check_circle</span>
          <span class="result-title">{{ resultComponent.name }}</span>
        </div>

        <table class="fields-table">
          <tbody>
            <tr v-if="resultComponent.fields.mpn">
              <td class="field-key">MPN</td>
              <td class="field-val">{{ resultComponent.fields.mpn }}</td>
            </tr>
            <tr v-if="resultComponent.fields.manufacturer">
              <td class="field-key">MF</td>
              <td class="field-val">{{ resultComponent.fields.manufacturer }}</td>
            </tr>
            <tr v-if="resultComponent.fields.lcsc_pn">
              <td class="field-key">LCSC</td>
              <td class="field-val">{{ resultComponent.fields.lcsc_pn }}</td>
            </tr>
            <tr v-if="resultComponent.fields.digikey_pn">
              <td class="field-key">DKPN</td>
              <td class="field-val">{{ resultComponent.fields.digikey_pn }}</td>
            </tr>
            <tr v-if="resultComponent.fields.mouser_pn">
              <td class="field-key">MSPN</td>
              <td class="field-val">{{ resultComponent.fields.mouser_pn }}</td>
            </tr>
            <tr v-if="resultComponent.fields.description">
              <td class="field-key">Description</td>
              <td class="field-val">{{ resultComponent.fields.description }}</td>
            </tr>
            <tr v-if="resultComponent.fields.package">
              <td class="field-key">Package</td>
              <td class="field-val">{{ resultComponent.fields.package }}</td>
            </tr>
            <tr v-if="resultComponent.fields.footprint">
              <td class="field-key">Footprint</td>
              <td class="field-val fp-val">{{ resultComponent.fields.footprint }}</td>
            </tr>
            <tr v-if="resultComponent.fields.datasheet && resultComponent.fields.datasheet !== '~'">
              <td class="field-key">Datasheet</td>
              <td class="field-val">
                <a :href="resultComponent.fields.datasheet" target="_blank" class="ds-link">
                  {{ resultComponent.fields.datasheet }}
                </a>
              </td>
            </tr>
            <template v-for="(v, k) in resultComponent.fields.extra" :key="k">
              <tr v-if="v">
                <td class="field-key extra-key">{{ k }}</td>
                <td class="field-val">{{ v }}</td>
              </tr>
            </template>
          </tbody>
        </table>

        <div v-if="resultComponent.symbol_path || resultComponent.footprint_path" class="paths-section">
          <div v-if="resultComponent.symbol_path" class="path-row">
            <span class="material-icons path-icon">memory</span>
            <span class="path-text" :title="resultComponent.symbol_path">{{ resultComponent.symbol_path }}</span>
          </div>
          <div v-if="resultComponent.footprint_path" class="path-row">
            <span class="material-icons path-icon">crop_square</span>
            <span class="path-text" :title="resultComponent.footprint_path">{{ resultComponent.footprint_path }}</span>
          </div>
          <div v-for="mp in resultComponent.model_paths" :key="mp" class="path-row">
            <span class="material-icons path-icon">view_in_ar</span>
            <span class="path-text" :title="mp">{{ mp }}</span>
          </div>
        </div>

        <div v-if="resultComponent.footprint_path" class="result-actions">
          <button class="action-btn primary" @click="openInKicad">
            <span class="material-icons">open_in_new</span>
            Open Footprint in KiCad
          </button>
        </div>
      </div>

    </div><!-- /importer-body -->
  </div>
</template>

<style scoped>
.importer-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  font-size: 0.8rem;
}

.importer-header {
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
}

/* Method tabs */
.method-tabs {
  display: flex;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.method-tab {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.2rem;
  padding: 0.4rem 0.3rem;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.7rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  border-bottom: 2px solid transparent;
}

.method-tab:hover {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}

.method-tab.active {
  color: var(--accent-color);
  border-bottom-color: var(--accent-color);
  background-color: var(--bg-secondary);
}

.tab-icon {
  font-size: 0.9rem;
}

/* Body scroll area */
.importer-body {
  flex: 1;
  overflow-y: auto;
  padding: 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  min-height: 0;
}

/* Method sections */
.method-section {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.field-label {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 0.1rem;
}

.input-row {
  display: flex;
  gap: 0.3rem;
  align-items: center;
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

/* Buttons */
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

.action-btn.secondary {
  background-color: var(--bg-tertiary);
  color: var(--text-secondary);
  border-color: var(--border-color);
  padding: 0.3rem 0.4rem;
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

/* Search results */
.search-results {
  max-height: 160px;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background-color: var(--bg-input);
}

.search-row {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 0.4rem;
  padding: 0.35rem 0.5rem;
  cursor: pointer;
  border-bottom: 1px solid var(--border-color);
  align-items: center;
  font-size: 0.7rem;
}

.search-row:last-child {
  border-bottom: none;
}

.search-row:hover {
  background-color: var(--bg-tertiary);
}

.search-row.selected {
  background-color: color-mix(in srgb, var(--accent-color) 15%, transparent);
}

.result-lib {
  color: var(--text-secondary);
  font-size: 0.65rem;
  min-width: 60px;
}

.result-name {
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-desc {
  color: var(--text-secondary);
  font-size: 0.65rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100px;
}

.empty-hint {
  color: var(--text-secondary);
  font-size: 0.72rem;
  text-align: center;
  padding: 0.5rem;
}

/* Advanced section */
.advanced-section {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
}

.toggle-advanced {
  display: flex;
  align-items: center;
  gap: 0.2rem;
  width: 100%;
  padding: 0.35rem 0.5rem;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.72rem;
  font-weight: 600;
  cursor: pointer;
  text-align: left;
}

.toggle-advanced:hover {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}

.toggle-advanced .material-icons {
  font-size: 0.9rem;
}

.advanced-body {
  padding: 0.5rem;
  border-top: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.checkbox-row {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  cursor: pointer;
  font-size: 0.72rem;
  color: var(--text-secondary);
}

/* Notices */
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

.notice.warn {
  background-color: color-mix(in srgb, #f39c12 15%, transparent);
  color: #c17d10;
  border: 1px solid color-mix(in srgb, #f39c12 30%, transparent);
}

.notice ul {
  margin: 0;
  padding-left: 1rem;
}

/* Result card */
.result-card {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background-color: var(--bg-secondary);
  overflow: hidden;
}

.result-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--border-color);
  background-color: var(--bg-tertiary);
}

.result-title {
  font-weight: 700;
  font-size: 0.8rem;
  color: var(--text-primary);
}

.fields-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.72rem;
}

.fields-table tr:not(:last-child) td {
  border-bottom: 1px solid var(--border-color);
}

.field-key {
  padding: 0.25rem 0.5rem;
  color: var(--text-secondary);
  font-weight: 600;
  white-space: nowrap;
  width: 80px;
}

.extra-key {
  color: var(--text-secondary);
  font-style: italic;
}

.field-val {
  padding: 0.25rem 0.5rem;
  color: var(--text-primary);
  word-break: break-all;
}

.fp-val {
  font-family: monospace;
  font-size: 0.68rem;
}

.ds-link {
  color: var(--accent-color);
  text-decoration: none;
  word-break: break-all;
}

.ds-link:hover {
  text-decoration: underline;
}

/* Paths section */
.paths-section {
  padding: 0.4rem 0.5rem;
  border-top: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.path-row {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.68rem;
}

.path-icon {
  font-size: 0.8rem;
  color: var(--accent-color);
  flex-shrink: 0;
}

.path-text {
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: monospace;
}

/* Post-import actions */
.result-actions {
  padding: 0.5rem;
  border-top: 1px solid var(--border-color);
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
}

code {
  background-color: var(--bg-tertiary);
  border-radius: 3px;
  padding: 0.1em 0.3em;
  font-family: monospace;
  font-size: 0.9em;
}
</style>
