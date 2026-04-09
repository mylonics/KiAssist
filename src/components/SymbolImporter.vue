<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue';
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

// Part lookup fields
const partMpn = ref('');
const partSpn = ref('');
const partLcsc = ref('');

// CAD sources (when EasyEDA has no data)
const cadSources = ref<CadSource[]>([]);
const octopartUrl = ref('');
const partLookupFailed = ref(false);

// Saved fields from Octopart/part-lookup to overlay on ZIP imports
const partLookupFields = ref<Record<string, any> | null>(null);

// Progress polling timer
let _progressTimer: ReturnType<typeof setInterval> | null = null;

// File import state
const zipPaths = ref<string[]>([]);
const zipsLoading = ref(false);
const zipDragging = ref(false);

// KiCad lib search
const searchQuery = ref('');
const searchLibrary = ref('');
const searchResults = ref<SearchResult[]>([]);
const selectedResult = ref<SearchResult | null>(null);
const searching = ref(false);

// Library index status
interface IndexStatus {
  ready: boolean;
  building: boolean;
  symbol_count: number;
  footprint_count: number;
  build_time: number;
  from_cache: boolean;
}
const indexStatus = ref<IndexStatus | null>(null);
let _indexPollTimer: ReturnType<typeof setInterval> | null = null;

// -----------------------------------------------------------------------
// Lifecycle
// -----------------------------------------------------------------------

/** Wait for pywebview API bridge to be available. */
function waitForApi(): Promise<boolean> {
  if ((window as any).pywebview?.api) return Promise.resolve(true);
  return new Promise((resolve) => {
    const onReady = () => resolve(!!(window as any).pywebview?.api);
    window.addEventListener('pywebviewready', onReady, { once: true });
    // Timeout after 5s so we don't hang forever in dev/test
    setTimeout(() => {
      window.removeEventListener('pywebviewready', onReady);
      resolve(!!(window as any).pywebview?.api);
    }, 5000);
  });
}

onMounted(async () => {
  const ready = await waitForApi();
  if (!ready) return;
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
  // Start polling library index status
  await pollIndexStatus();
  _indexPollTimer = setInterval(pollIndexStatus, 2000);
});

onBeforeUnmount(() => {
  if (_progressTimer) clearInterval(_progressTimer);
  if (_indexPollTimer) { clearInterval(_indexPollTimer); _indexPollTimer = null; }
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

/** Reset all importer form state (called when user closes a part). */
function resetImporter() {
  partMpn.value = '';
  partSpn.value = '';
  partLcsc.value = '';
  cadSources.value = [];
  octopartUrl.value = '';
  partLookupFailed.value = false;
  partLookupFields.value = null;
  zipPaths.value = [];
  searchQuery.value = '';
  searchLibrary.value = '';
  searchResults.value = [];
  selectedResult.value = null;
  error.value = '';
  lookupStatus.value = '';
}

defineExpose({ resetImporter });

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
  partLookupFields.value = null;
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
    // Save lookup fields so they can be overlaid onto ZIP imports
    if (r?.component?.fields) {
      partLookupFields.value = { ...r.component.fields };
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

// -----------------------------------------------------------------------
// Browse & auto-import
// -----------------------------------------------------------------------

async function browseAndImport() {
  const api = getApi();
  if (!api) return;
  try {
    const r = await api.importer_browse_zips();
    if (r?.paths?.length) {
      zipPaths.value = r.paths;
      await importZips();
    }
  } catch (e: any) {
    error.value = e.message || 'File dialog failed';
  }
}

// -----------------------------------------------------------------------
// Drag-and-drop files (ZIP, KiCad, STEP)
// -----------------------------------------------------------------------

function onZipDragOver(e: DragEvent) {
  e.preventDefault();
  e.stopPropagation();
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
  zipDragging.value = true;
}

function onZipDragLeave(e: DragEvent) {
  e.preventDefault();
  e.stopPropagation();
  zipDragging.value = false;
}

async function onZipDrop(e: DragEvent) {
  e.preventDefault();
  e.stopPropagation();
  zipDragging.value = false;

  const files = e.dataTransfer?.files;
  if (!files || !files.length) return;

  const api = getApi();
  if (!api) { error.value = 'Backend not available'; return; }

  // Read each file as base64 and send to backend for temp-saving
  const SUPPORTED_EXTS = ['.zip', '.kicad_sym', '.lib', '.kicad_mod', '.mod', '.step', '.stp', '.wrl'];
  const fileEntries: { name: string; data: string }[] = [];
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
    if (!SUPPORTED_EXTS.includes(ext)) {
      error.value = `Skipped unsupported file: ${file.name}`;
      continue;
    }
    const buf = await file.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = '';
    for (let j = 0; j < bytes.length; j++) {
      binary += String.fromCharCode(bytes[j]);
    }
    fileEntries.push({ name: file.name, data: btoa(binary) });
  }

  if (!fileEntries.length) return;

  try {
    const r = await api.importer_save_dropped_zips(fileEntries);
    if (r?.success && r.paths?.length) {
      zipPaths.value = r.paths;
      await importZips();
    } else if (r?.error) {
      error.value = r.error;
    }
  } catch (e: any) {
    error.value = e.message || 'Failed to process dropped files';
  }
}


async function importZips() {
  const api = getApi();
  if (!api) { error.value = 'Backend not available'; return; }
  if (!zipPaths.value.length) { error.value = 'Please select at least one file'; return; }

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
      partLookupFields.value || {},
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
// Library index status polling
// -----------------------------------------------------------------------

async function pollIndexStatus() {
  const api = getApi();
  if (!api) return;
  try {
    const s = await api.importer_library_index_status();
    if (s) {
      indexStatus.value = s as IndexStatus;
      // Stop polling once index is ready and not currently building
      if (s.ready && !s.building && _indexPollTimer) {
        clearInterval(_indexPollTimer);
        _indexPollTimer = null;
      }
    }
  } catch { /* ignore */ }
}

async function rescanLibraries() {
  const api = getApi();
  if (!api) return;
  try {
    const r = await api.importer_reload_libraries();
    // Immediately reflect building state from the response
    if (r?.status) {
      indexStatus.value = r.status as IndexStatus;
    }
    // Resume polling to track the background scan
    if (!_indexPollTimer) {
      _indexPollTimer = setInterval(pollIndexStatus, 2000);
    }
  } catch { /* ignore */ }
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
    </div>

    <div class="importer-body">

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
            style="flex: 1; min-width: 12rem"
            @click="importByPart"
            :disabled="loading || (!partMpn.trim() && !partSpn.trim() && !partLcsc.trim())"
          >
            <span class="material-icons">download</span>
            {{ loading ? (lookupStatus || 'Looking up…') : 'Lookup' }}
          </button>
          <button
            class="action-btn secondary"
            @click="partMpn = ''; partSpn = ''; partLcsc = ''; cadSources = []; octopartUrl = ''; partLookupFailed = false; partLookupFields = null; zipPaths = []"
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

          <!-- Import from file(s) subsection -->
          <div class="zip-import-section">
            <div
              class="zip-drop-zone"
              :class="{ 'drag-over': zipDragging, 'importing': loading }"
              @dragover="onZipDragOver"
              @dragleave="onZipDragLeave"
              @drop="onZipDrop"
              @click="browseAndImport"
            >
              <span class="material-icons drop-icon">{{ loading ? 'hourglass_empty' : 'cloud_upload' }}</span>
              <span class="drop-text">{{ loading ? 'Importing…' : 'Drop files here or click to browse' }}</span>
              <span class="drop-hint">.zip · .kicad_sym · .kicad_mod · .step</span>
            </div>
          </div>
        </div>
      </div>

        <!-- Fallback ZIP import when part lookup failed but no CAD sources found -->
        <div v-if="partLookupFailed && !cadSources.length" class="cad-sources-panel">
          <div class="cad-sources-header">
            <span class="material-icons" style="font-size:0.85rem">info</span>
            Part not found via EasyEDA. Import from ZIP or KiCad files:
          </div>
          <div class="zip-import-section" style="margin-top: 0.2rem; padding-top: 0; border-top: none;">
            <div
              class="zip-drop-zone"
              :class="{ 'drag-over': zipDragging, 'importing': loading }"
              @dragover="onZipDragOver"
              @dragleave="onZipDragLeave"
              @drop="onZipDrop"
              @click="browseAndImport"
            >
              <span class="material-icons drop-icon">{{ loading ? 'hourglass_empty' : 'cloud_upload' }}</span>
              <span class="drop-text">{{ loading ? 'Importing…' : 'Drop files here or click to browse' }}</span>
              <span class="drop-hint">.zip · .kicad_sym · .kicad_mod · .step</span>
            </div>
          </div>
        </div>

      <div v-if="!cadSources.length && !partLookupFailed" class="section-divider"></div>
      <div v-if="!cadSources.length && !partLookupFailed" class="method-section">
        <div class="method-label">
          <span class="material-icons method-icon">folder_zip</span>
          Import Files (ZIP / KiCad / STEP)
        </div>
        <div
          class="zip-drop-zone"
          :class="{ 'drag-over': zipDragging, 'importing': loading }"
          @dragover="onZipDragOver"
          @dragleave="onZipDragLeave"
          @drop="onZipDrop"
          @click="browseAndImport"
        >
          <span class="material-icons drop-icon">{{ loading ? 'hourglass_empty' : 'cloud_upload' }}</span>
          <span class="drop-text">{{ loading ? 'Importing…' : 'Drop files here or click to browse' }}</span>
          <span class="drop-hint">.zip · .kicad_sym · .kicad_mod · .step</span>
        </div>
      </div>

      <div class="section-divider"></div>

      <!-- ======== KiCad Lib Section ======== -->
      <div class="method-section kicad-lib-section">
        <div class="method-label">
          <span class="material-icons method-icon">library_books</span>
          KiCad Library
          <!-- Symbol count badge when ready -->
          <span
            v-if="indexStatus && indexStatus.ready && !indexStatus.building"
            class="index-badge ready"
            :title="`${indexStatus.symbol_count} symbols, ${indexStatus.footprint_count} footprints`"
          >
            <span class="material-icons">check_circle</span>
            <span class="index-badge-text">{{ indexStatus.symbol_count }}</span>
          </span>
          <!-- Single sync icon: spinning when building, clickable when idle -->
          <button
            class="index-sync-btn"
            :class="{ building: indexStatus?.building }"
            :title="indexStatus?.building ? 'Scanning KiCad libraries…' : 'Rescan KiCad libraries'"
            :disabled="!!indexStatus?.building"
            @click="rescanLibraries"
          >
            <span class="material-icons" :class="{ spin: indexStatus?.building }">sync</span>
          </button>
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
  padding: 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
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

/* ===== Drop Zone ===== */

.zip-drop-zone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.15rem;
  padding: 0.7rem 0.5rem;
  border: 2px dashed var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-secondary);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
  user-select: none;
}

.zip-drop-zone:hover {
  border-color: var(--accent-color);
  background: color-mix(in srgb, var(--accent-color) 6%, transparent);
}

.zip-drop-zone.drag-over {
  border-color: var(--accent-color);
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
  color: var(--accent-color);
}

.zip-drop-zone.importing {
  cursor: wait;
  opacity: 0.7;
  pointer-events: none;
}

.zip-drop-zone .drop-icon {
  font-size: 1.3rem;
  opacity: 0.45;
}

.zip-drop-zone:hover .drop-icon,
.zip-drop-zone.drag-over .drop-icon {
  opacity: 1;
}

.zip-drop-zone .drop-text {
  font-size: 0.68rem;
  font-weight: 600;
}

.zip-drop-zone .drop-hint {
  font-size: 0.58rem;
  opacity: 0.55;
  font-weight: 400;
}

/* ===== Library index status indicator ===== */

.index-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  font-size: 0.62rem;
  font-weight: 600;
  padding: 0.05rem 0.35rem 0.05rem 0.15rem;
  border-radius: 9999px;
  line-height: 1;
  white-space: nowrap;
  margin-left: auto;
}

.index-badge .material-icons {
  font-size: 0.78rem;
}

.index-badge.ready {
  color: #3ba55d;
  background: color-mix(in srgb, #3ba55d 10%, transparent);
}

.index-badge-text {
  font-size: 0.62rem;
}

/* Single sync/rescan button — always visible */
.index-sync-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: 1px solid transparent;
  cursor: pointer;
  color: var(--text-secondary, #999);
  padding: 0.12rem;
  border-radius: 4px;
  transition: color 0.15s, background 0.15s, border-color 0.15s;
  margin-left: 0.15rem;
}

.index-sync-btn:not(.building):hover {
  color: var(--accent-color);
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
  border-color: color-mix(in srgb, var(--accent-color) 25%, transparent);
}

.index-sync-btn.building {
  color: var(--accent-color);
  cursor: default;
}

.index-sync-btn .material-icons {
  font-size: 0.92rem;
}

/* Spinning animation for sync icon */
@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

.spin {
  animation: spin 1.2s linear infinite;
}
</style>
