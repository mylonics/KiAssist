<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import type { ImportedComponent, CadSource } from '../types/importer';

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
const lookupStatus = ref('');
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

// Part lookup fields
const partMpn = ref('');
const partSpn = ref('');
const partLcsc = ref('');

// CAD sources (when EasyEDA has no data)
const cadSources = ref<CadSource[]>([]);
const octopartUrl = ref('');
const partLookupFailed = ref(false);

// Progress polling timer
let _progressTimer: ReturnType<typeof setInterval> | null = null;

// ZIP
const zipPath = ref('');

// Multi-ZIP (used from CAD sources / Part Lookup fallback)
const zipPaths = ref<string[]>([]);
const zipsLoading = ref(false);

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

onBeforeUnmount(() => {
  if (_progressTimer) clearInterval(_progressTimer);
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
// Part lookup import (MPN / SPN / LCSC → Octopart + EasyEDA)
// -----------------------------------------------------------------------

async function importByPart() {
  const api = getApi();
  if (!api) { error.value = 'Backend not available'; return; }
  const m = partMpn.value.trim();
  const s = partSpn.value.trim();
  const l = partLcsc.value.trim();
  if (!m && !s && !l) { error.value = 'Please enter at least one part identifier'; return; }

  clearError();
  cadSources.value = [];
  octopartUrl.value = '';
  partLookupFailed.value = false;
  zipPaths.value = [];
  lookupStatus.value = 'Starting lookup…';
  loading.value = true;

  // Poll backend for progress updates every 400ms
  _progressTimer = setInterval(async () => {
    try {
      const p = await api.importer_import_progress();
      if (p?.status) lookupStatus.value = p.status;
    } catch { /* ignore */ }
  }, 400);

  try {
    const r = await api.importer_import_by_part(m, s, l);
    console.log('[SymbolImporter] import response:', JSON.stringify(r?.cad_sources), 'octopart_url:', r?.octopart_url);
    // Capture alternative CAD sources (present on both success and failure)
    if (r?.cad_sources?.length) {
      cadSources.value = r.cad_sources;
      octopartUrl.value = r.octopart_url || '';
    }
    if (!r?.success) {
      partLookupFailed.value = true;
    }
    handleResult(r);
  } catch (e: any) {
    error.value = e.message || 'Import failed';
    partLookupFailed.value = true;
  } finally {
    if (_progressTimer) { clearInterval(_progressTimer); _progressTimer = null; }
    loading.value = false;
    lookupStatus.value = '';
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
// Multi-ZIP import (from CAD sources fallback)
// -----------------------------------------------------------------------

async function browseZips() {
  const api = getApi();
  if (!api) return;
  try {
    const r = await api.importer_browse_zips();
    if (r?.paths?.length) {
      // Append new paths, avoiding duplicates
      const existing = new Set(zipPaths.value);
      for (const p of r.paths) {
        if (!existing.has(p)) {
          zipPaths.value.push(p);
        }
      }
    }
  } catch (e: any) {
    error.value = e.message || 'File dialog failed';
  }
}

function removeZipPath(index: number) {
  zipPaths.value.splice(index, 1);
}

async function importZips() {
  const api = getApi();
  if (!api) { error.value = 'Backend not available'; return; }
  if (!zipPaths.value.length) { error.value = 'Please select at least one ZIP file'; return; }

  clearError();
  zipsLoading.value = true;
  loading.value = true;
  try {
    const r = await api.importer_import_zips(
      zipPaths.value,
      '',
      '',
      '',
      false,
    );
    handleResult(r);
  } catch (e: any) {
    error.value = e.message || 'Import failed';
  } finally {
    zipsLoading.value = false;
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

      <!-- ======== Part Lookup Section ======== -->
      <div class="method-section">
        <div class="method-label">
          <span class="material-icons method-icon">cloud_download</span>
          Part Lookup
        </div>

        <div v-if="!lcscAvailable" class="notice warn">
          <span class="material-icons">warning</span>
          <span>
            <strong>easyeda2kicad</strong> is not available.<br>
            Symbol/footprint import will be skipped.
          </span>
        </div>

        <div class="part-fields">
          <div class="input-row">
            <label class="field-label">MPN</label>
            <input
              v-model="partMpn"
              class="text-input"
              placeholder="e.g. STM32F103C8T6"
              :disabled="loading"
              @keydown.enter="importByPart"
            />
          </div>
          <div class="input-row">
            <label class="field-label">SPN</label>
            <input
              v-model="partSpn"
              class="text-input"
              placeholder="e.g. 497-6063-ND"
              :disabled="loading"
              @keydown.enter="importByPart"
            />
          </div>
          <div class="input-row">
            <label class="field-label">LCSC</label>
            <input
              v-model="partLcsc"
              class="text-input"
              placeholder="e.g. C8734"
              :disabled="loading"
              @keydown.enter="importByPart"
            />
          </div>
        </div>
        <div class="button-row" style="margin-top: 0.35rem">
          <button
            class="action-btn primary"
            style="flex: 1"
            @click="importByPart"
            :disabled="loading || (!partMpn.trim() && !partSpn.trim() && !partLcsc.trim())"
          >
            <span class="material-icons">download</span>
            {{ loading ? (lookupStatus || 'Looking up…') : 'Import' }}
          </button>
          <button
            class="action-btn secondary"
            @click="partMpn = ''; partSpn = ''; partLcsc = ''; cadSources = []; octopartUrl = ''; partLookupFailed = false; zipPaths = []"
            :disabled="loading || (!partMpn.trim() && !partSpn.trim() && !partLcsc.trim())"
            title="Clear part lookup fields"
          >
            <span class="material-icons">clear</span>
          </button>
        </div>

        <!-- CAD Sources (Octopart partners + Ultra Librarian) -->
        <div v-if="cadSources.length" class="cad-sources-panel">
          <div class="cad-sources-header">
            <span class="material-icons" style="font-size:0.85rem">info</span>
            Additional CAD models available from:
          </div>
          <div
            v-for="src in cadSources"
            :key="src.partner"
            class="cad-source-row"
          >
            <span class="cad-partner-name">{{ src.partner }}</span>
            <span v-if="src.has_symbol" class="cad-badge sym" title="Symbol available">SYM</span>
            <span v-if="src.has_footprint" class="cad-badge fp" title="Footprint available">FP</span>
            <span v-if="src.has_3d_model" class="cad-badge model3d" title="3D model available">3D</span>
            <a
              v-if="src.download_url"
              :href="src.download_url"
              target="_blank"
              class="cad-row-link"
              title="Open download page"
            >
              <span class="material-icons" style="font-size:0.75rem">open_in_new</span>
            </a>
          </div>
          <a
            v-if="octopartUrl"
            :href="octopartUrl + '#CadModels'"
            target="_blank"
            class="cad-download-link"
          >
            <span class="material-icons" style="font-size:0.8rem">open_in_new</span>
            Download on Octopart
          </a>

          <!-- Import from ZIP(s) subsection -->
          <div class="zip-import-section">
            <div class="zip-import-header">
              <span class="material-icons" style="font-size:0.8rem">folder_zip</span>
              Import from downloaded ZIP file(s):
            </div>
            <div v-if="zipPaths.length" class="zip-paths-list">
              <div v-for="(zp, idx) in zipPaths" :key="idx" class="zip-path-row">
                <span class="zip-path-name" :title="zp">{{ zp.split(/[\\/]/).pop() }}</span>
                <button class="zip-remove-btn" @click="removeZipPath(idx)" title="Remove">
                  <span class="material-icons" style="font-size:0.7rem">close</span>
                </button>
              </div>
            </div>
            <div class="zip-import-actions">
              <button
                class="action-btn secondary"
                @click="browseZips"
                :disabled="loading"
                style="flex:1"
              >
                <span class="material-icons">folder_open</span>
                {{ zipPaths.length ? 'Add ZIP files…' : 'Select ZIP files…' }}
              </button>
              <button
                class="action-btn primary"
                @click="importZips"
                :disabled="loading || !zipPaths.length"
                style="flex:1"
              >
                <span class="material-icons">download</span>
                {{ zipsLoading ? '…' : `Import (${zipPaths.length})` }}
              </button>
            </div>
          </div>
        </div>
      </div>

        <!-- Fallback ZIP import when part lookup failed but no CAD sources found -->
        <div v-if="partLookupFailed && !cadSources.length" class="cad-sources-panel">
          <div class="cad-sources-header">
            <span class="material-icons" style="font-size:0.85rem">info</span>
            Part not found via EasyEDA. You can import from ZIP files downloaded from SnapEDA, Ultra Librarian, or other sources:
          </div>
          <div class="zip-import-section" style="margin-top: 0.2rem; padding-top: 0; border-top: none;">
            <div v-if="zipPaths.length" class="zip-paths-list">
              <div v-for="(zp, idx) in zipPaths" :key="idx" class="zip-path-row">
                <span class="zip-path-name" :title="zp">{{ zp.split(/[\\/]/).pop() }}</span>
                <button class="zip-remove-btn" @click="removeZipPath(idx)" title="Remove">
                  <span class="material-icons" style="font-size:0.7rem">close</span>
                </button>
              </div>
            </div>
            <div class="zip-import-actions">
              <button
                class="action-btn secondary"
                @click="browseZips"
                :disabled="loading"
                style="flex:1"
              >
                <span class="material-icons">folder_open</span>
                {{ zipPaths.length ? 'Add ZIP files\u2026' : 'Select ZIP files\u2026' }}
              </button>
              <button
                class="action-btn primary"
                @click="importZips"
                :disabled="loading || !zipPaths.length"
                style="flex:1"
              >
                <span class="material-icons">download</span>
                {{ zipsLoading ? '\u2026' : `Import (${zipPaths.length})` }}
              </button>
            </div>
          </div>
        </div>

      <div class="section-divider"></div>
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

.button-row {
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

/* ===== Part-lookup fields ===== */
.part-fields {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.part-fields .input-row {
  display: flex;
  gap: 0.35rem;
  align-items: center;
}

.field-label {
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--text-secondary);
  min-width: 2.5rem;
  flex-shrink: 0;
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

/* ===== CAD Sources Panel ===== */

.cad-sources-panel {
  margin-top: 0.4rem;
  padding: 0.45rem;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  border-left: 3px solid var(--accent-color);
}

.cad-sources-header {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.68rem;
  color: var(--text-secondary);
  margin-bottom: 0.35rem;
  line-height: 1.3;
}

.cad-source-row {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.2rem 0.3rem;
  border-bottom: 1px solid var(--border-color);
  font-size: 0.68rem;
}

.cad-source-row:last-of-type {
  border-bottom: none;
}

.cad-partner-name {
  font-weight: 600;
  color: var(--text-primary);
  flex: 1;
}

.cad-row-link {
  color: var(--accent-color);
  display: flex;
  align-items: center;
  text-decoration: none;
  opacity: 0.7;
  transition: opacity 0.15s;
}

.cad-row-link:hover {
  opacity: 1;
}

.cad-badge {
  display: inline-block;
  padding: 0.05rem 0.3rem;
  border-radius: 3px;
  font-size: 0.55rem;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.cad-badge.sym {
  background: #1b5e20;
  color: #e8f5e9;
}

.cad-badge.fp {
  background: #0d47a1;
  color: #e3f2fd;
}

.cad-badge.model3d {
  background: #e65100;
  color: #fff3e0;
}

.cad-download-link {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  margin-top: 0.35rem;
  padding: 0.3rem 0.5rem;
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--accent-color);
  background: var(--bg-secondary);
  border: 1px solid var(--accent-color);
  border-radius: var(--radius-sm);
  text-decoration: none;
  cursor: pointer;
  transition: all 0.15s;
  justify-content: center;
}

.cad-download-link:hover {
  background: var(--accent-color);
  color: #fff;
}

/* ===== ZIP Import inside CAD Sources ===== */

.zip-import-section {
  margin-top: 0.45rem;
  padding-top: 0.4rem;
  border-top: 1px dashed var(--border-color);
}

.zip-import-header {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 0.3rem;
}

.zip-paths-list {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  margin-bottom: 0.3rem;
}

.zip-path-row {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.15rem 0.3rem;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  font-size: 0.65rem;
}

.zip-path-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}

.zip-remove-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1rem;
  height: 1rem;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  border-radius: 2px;
  flex-shrink: 0;
  padding: 0;
}

.zip-remove-btn:hover {
  background: color-mix(in srgb, #e74c3c 20%, transparent);
  color: #e74c3c;
}

.zip-import-actions {
  display: flex;
  gap: 0.3rem;
}
</style>
