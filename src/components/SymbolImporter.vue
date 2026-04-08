<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import type { ImportedComponent } from '../types/importer';

// -----------------------------------------------------------------------
// Emits
// -----------------------------------------------------------------------

const emit = defineEmits<{
  (e: 'component-imported', component: ImportedComponent, warnings: string[]): void;
}>();

// -----------------------------------------------------------------------
// Types
// -----------------------------------------------------------------------

interface SearchResult {
  library: string;
  name: string;
  description?: string;
  value?: string;
  footprint?: string;
  path?: string;
}

interface LibraryInfo {
  nickname: string;
  uri: string;
}

// -----------------------------------------------------------------------
// State
// -----------------------------------------------------------------------

const loading = ref(false);
const error = ref('');
const lcscAvailable = ref(true);

// Global libraries
const symLibraries = ref<LibraryInfo[]>([]);
const fpLibraries = ref<LibraryInfo[]>([]);
const librariesLoading = ref(false);
const librariesError = ref('');
const libraryFilter = ref('');
const libraryTab = ref<'symbols' | 'footprints'>('symbols');
const librariesCollapsed = ref(false);
const libraryScope = ref<'all' | 'custom'>('all');

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

/** Returns true when the library entry is a built-in KiCad library. */
function isBuiltinLib(lib: LibraryInfo): boolean {
  return /\$\{KICAD\d*_/.test(lib.uri);
}

// Computed — filtered library list
const filteredLibraries = computed(() => {
  let list = libraryTab.value === 'symbols' ? symLibraries.value : fpLibraries.value;
  if (libraryScope.value === 'custom') {
    list = list.filter(l => !isBuiltinLib(l));
  }
  if (libraryFilter.value.trim()) {
    const q = libraryFilter.value.toLowerCase();
    list = list.filter(l => l.nickname.toLowerCase().includes(q));
  }
  return list;
});

// Counts for the badge
const customSymCount = computed(() => symLibraries.value.filter(l => !isBuiltinLib(l)).length);
const customFpCount = computed(() => fpLibraries.value.filter(l => !isBuiltinLib(l)).length);

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
  // Load libraries on startup
  await loadLibraries();
});

async function loadLibraries() {
  const api = getApi();
  if (!api) return;
  librariesLoading.value = true;
  librariesError.value = '';
  try {
    const [symRes, fpRes] = await Promise.all([
      api.importer_get_sym_libraries(),
      api.importer_get_fp_libraries(),
    ]);
    if (symRes?.success) {
      symLibraries.value = (symRes.libraries || []).map((l: any) => ({
        nickname: l.nickname,
        uri: l.uri,
      }));
    } else {
      librariesError.value = symRes?.error || 'Failed to load symbol libraries';
    }
    if (fpRes?.success) {
      fpLibraries.value = (fpRes.libraries || []).map((l: any) => ({
        nickname: l.nickname,
        uri: l.uri,
      }));
    } else {
      librariesError.value = fpRes?.error || 'Failed to load footprint libraries';
    }
  } catch (e: any) {
    librariesError.value = e.message || 'Failed to load libraries';
  } finally {
    librariesLoading.value = false;
  }
}

// -----------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------

function getApi() {
  return (window as any).pywebview?.api ?? null;
}

function clearError() {
  error.value = '';
}

/** Scroll the importer body so the KiCad Library search section is visible */
function leftPanelScrollToSearch() {
  const el = document.querySelector('.importer-body .kicad-lib-section');
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// -----------------------------------------------------------------------
// LCSC import
// -----------------------------------------------------------------------

async function importLcsc() {
  const api = getApi();
  if (!api) { error.value = 'Backend not available'; return; }
  if (!lcscId.value.trim()) { error.value = 'Please enter an LCSC part number'; return; }

  clearError();
  loading.value = true;
  try {
    const r = await api.importer_import_lcsc(
      lcscId.value.trim(),
      '',
      '',
      '',
      false,
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

  clearError();
  loading.value = true;
  try {
    const r = await api.importer_import_zip(
      zipPath.value.trim(),
      '',
      '',
      '',
      false,
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

  clearError();
  loading.value = true;
  try {
    const r = await api.importer_import_from_kicad(
      selectedResult.value.name,
      selectedResult.value.library,
      '',
      '',
      '',
      false,
    );
    handleResult(r);
  } catch (e: any) {
    error.value = e.message || 'Import failed';
  } finally {
    loading.value = false;
  }
}

// -----------------------------------------------------------------------
// Result handling — emit to parent instead of switching page
// -----------------------------------------------------------------------

function handleResult(r: any) {
  if (!r) { error.value = 'No response from backend'; return; }
  if (r.success) {
    const comp: ImportedComponent | null = r.component ?? null;
    const warns: string[] = r.warnings ?? [];
    if (comp) {
      emit('component-imported', comp, warns);
    }
  } else {
    error.value = r.error || 'Import failed';
  }
}
</script>

<template>
  <div class="importer-panel">
    <div class="importer-header">
      <span class="material-icons header-icon">download</span>
      <span class="header-title">Component Importer</span>
      <button
        class="action-btn secondary refresh-btn"
        @click="loadLibraries"
        :disabled="librariesLoading"
        title="Refresh KiCad libraries"
      >
        <span class="material-icons" :class="{ spinning: librariesLoading }">refresh</span>
      </button>
    </div>

    <div class="importer-body">

      <!-- ======== KiCad Libraries Section ======== -->
      <div class="method-section">
        <div class="method-label" style="cursor: pointer;" @click="librariesCollapsed = !librariesCollapsed">
          <span class="material-icons method-icon">local_library</span>
          KiCad Libraries
          <span class="lib-badge" v-if="symLibraries.length || fpLibraries.length">
            {{ symLibraries.length }} sym · {{ fpLibraries.length }} fp
          </span>
          <span class="material-icons collapse-chevron">{{ librariesCollapsed ? 'expand_more' : 'expand_less' }}</span>
        </div>

        <template v-if="!librariesCollapsed">
          <div v-if="librariesLoading" class="empty-hint">
            <span class="material-icons spinning" style="font-size:0.9rem">autorenew</span>
            Loading libraries…
          </div>

          <div v-else-if="librariesError" class="notice error" style="margin-top:0.25rem">
            <span class="material-icons">error_outline</span>
            {{ librariesError }}
          </div>

          <template v-else>
            <!-- Tab toggle -->
            <div class="lib-tabs">
              <button
                :class="['lib-tab-btn', { active: libraryTab === 'symbols' }]"
                @click="libraryTab = 'symbols'"
              >
                Symbols ({{ symLibraries.length }})
              </button>
              <button
                :class="['lib-tab-btn', { active: libraryTab === 'footprints' }]"
                @click="libraryTab = 'footprints'"
              >
                Footprints ({{ fpLibraries.length }})
              </button>
            </div>

            <!-- Scope toggle: All / Custom -->
            <div class="lib-tabs" style="margin-top:0.2rem">
              <button
                :class="['lib-tab-btn', { active: libraryScope === 'all' }]"
                @click="libraryScope = 'all'"
              >
                All
              </button>
              <button
                :class="['lib-tab-btn', { active: libraryScope === 'custom' }]"
                @click="libraryScope = 'custom'"
              >
                Custom ({{ libraryTab === 'symbols' ? customSymCount : customFpCount }})
              </button>
            </div>

            <!-- Filter -->
            <div class="input-row" style="margin-top:0.2rem">
              <input
                v-model="libraryFilter"
                class="text-input"
                placeholder="Filter libraries…"
              />
            </div>

            <!-- Library list -->
            <div class="library-list" v-if="filteredLibraries.length">
              <div
                v-for="lib in filteredLibraries"
                :key="lib.nickname"
                class="library-row"
                :title="lib.uri"
                @click="searchLibrary = lib.nickname; searchQuery = ''; leftPanelScrollToSearch()"
              >
                <span class="material-icons lib-row-icon">
                  {{ libraryTab === 'symbols' ? 'memory' : 'view_in_ar' }}
                </span>
                <span class="lib-nick">{{ lib.nickname }}</span>
              </div>
            </div>
            <div v-else class="empty-hint">No libraries found.</div>
          </template>
        </template>
      </div>

      <div class="section-divider"></div>

      <!-- ======== LCSC Section ======== -->
      <div class="method-section">
        <div class="method-label">
          <span class="material-icons method-icon">cloud_download</span>
          LCSC / EasyEDA
        </div>

        <div v-if="!lcscAvailable" class="notice warn">
          <span class="material-icons">warning</span>
          <span>
            <strong>easyeda2kicad</strong> is not available.<br>
            Try restarting the application.
          </span>
        </div>

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
            {{ loading ? '…' : 'Import' }}
          </button>
        </div>
      </div>

      <div class="section-divider"></div>

      <!-- ======== ZIP Section ======== -->
      <div class="method-section">
        <div class="method-label">
          <span class="material-icons method-icon">folder_zip</span>
          ZIP File (SnapEDA / Ultra Librarian)
        </div>
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
          style="margin-top: 0.35rem"
        >
          <span class="material-icons">download</span>
          {{ loading ? '…' : 'Import ZIP' }}
        </button>
      </div>

      <div class="section-divider"></div>

      <!-- ======== KiCad Lib Section ======== -->
      <div class="method-section kicad-lib-section">
        <div class="method-label">
          <span class="material-icons method-icon">library_books</span>
          KiCad Library
        </div>
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
        <div class="input-row" style="margin-top:0.2rem">
          <select
            v-model="searchLibrary"
            class="text-input"
            style="flex:1"
          >
            <option value="">All libraries</option>
            <option v-for="lib in symLibraries" :key="lib.nickname" :value="lib.nickname">
              {{ lib.nickname }}
            </option>
          </select>
          <button
            v-if="searchLibrary"
            class="action-btn secondary"
            @click="searchLibrary = ''"
            title="Clear library filter"
            style="padding: 0.3rem 0.35rem"
          >
            <span class="material-icons" style="font-size:0.85rem">close</span>
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
          v-if="selectedResult"
          class="action-btn primary full-width"
          @click="importFromKicad"
          :disabled="loading"
          style="margin-top: 0.35rem"
        >
          <span class="material-icons">add_circle_outline</span>
          {{ loading ? '…' : 'Import Selected' }}
        </button>
      </div>

      <!-- ======== Status ======== -->
      <div v-if="error" class="notice error">
        <span class="material-icons">error_outline</span>
        {{ error }}
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

/* ===== Header ===== */
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
  flex: 1;
}

/* ===== Body ===== */
.importer-body {
  flex: 1;
  overflow-y: auto;
  padding: 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  min-height: 0;
}

/* ===== Method sections ===== */
.method-section {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.method-label {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.72rem;
  font-weight: 700;
  color: var(--text-primary);
}

.method-icon {
  font-size: 0.9rem;
  color: var(--accent-color);
}

.section-divider {
  height: 0;
  border-top: 1px solid var(--border-color);
  margin: 0.15rem 0;
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

/* ===== Search results ===== */
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

.notice.warn {
  background-color: color-mix(in srgb, #f39c12 15%, transparent);
  color: #c17d10;
  border: 1px solid color-mix(in srgb, #f39c12 30%, transparent);
}

.notice ul {
  margin: 0;
  padding-left: 1rem;
}

code {
  background-color: var(--bg-tertiary);
  border-radius: 3px;
  padding: 0.1em 0.3em;
  font-family: monospace;
  font-size: 0.9em;
}

/* ===== Refresh button ===== */
.refresh-btn {
  margin-left: auto;
  padding: 0.2rem 0.3rem !important;
}

.refresh-btn .material-icons {
  font-size: 1rem;
}

/* ===== Library section ===== */
.lib-badge {
  margin-left: auto;
  font-size: 0.6rem;
  font-weight: 600;
  color: var(--text-secondary);
  background-color: var(--bg-tertiary);
  padding: 0.1rem 0.35rem;
  border-radius: 8px;
}

.collapse-chevron {
  font-size: 0.9rem;
  color: var(--text-secondary);
  margin-left: 0.15rem;
}

.lib-tabs {
  display: flex;
  gap: 0;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  overflow: hidden;
  margin-top: 0.2rem;
}

.lib-tab-btn {
  flex: 1;
  padding: 0.25rem 0.3rem;
  border: none;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  font-size: 0.65rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}

.lib-tab-btn:not(:last-child) {
  border-right: 1px solid var(--border-color);
}

.lib-tab-btn.active {
  background-color: var(--accent-color);
  color: #fff;
}

.lib-tab-btn:hover:not(.active) {
  background-color: var(--bg-secondary);
  color: var(--text-primary);
}

.library-list {
  max-height: 180px;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background-color: var(--bg-input);
  margin-top: 0.2rem;
}

.library-row {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.25rem 0.45rem;
  cursor: pointer;
  border-bottom: 1px solid var(--border-color);
  font-size: 0.68rem;
  transition: background-color 0.12s;
}

.library-row:last-child {
  border-bottom: none;
}

.library-row:hover {
  background-color: var(--bg-tertiary);
}

.lib-row-icon {
  font-size: 0.8rem;
  color: var(--accent-color);
  flex-shrink: 0;
}

.lib-nick {
  color: var(--text-primary);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
