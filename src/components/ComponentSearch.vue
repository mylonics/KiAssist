<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import type { ComponentCandidate } from '../types/pywebview';

// ---------------------------------------------------------------------------
// Emits
// ---------------------------------------------------------------------------

const emit = defineEmits<{
  (e: 'insert-in-chat', text: string): void;
}>();

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const query = ref('');
const libraryFilter = ref('');
const libraries = ref<string[]>([]);
const candidates = ref<ComponentCandidate[]>([]);
const totalSearched = ref(0);
const selectedCandidate = ref<ComponentCandidate | null>(null);
const loading = ref(false);
const loadingLibraries = ref(false);
const error = ref('');
let debounceTimer: ReturnType<typeof setTimeout> | null = null;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getApi() {
  return (window as any).pywebview?.api;
}

async function loadLibraries() {
  const api = getApi();
  if (!api) return;
  loadingLibraries.value = true;
  try {
    const result = await api.get_component_libraries();
    if (result.success) {
      libraries.value = result.libraries ?? [];
    }
  } catch {
    // ignore – not critical
  } finally {
    loadingLibraries.value = false;
  }
}

async function runSearch() {
  const api = getApi();
  if (!api) {
    error.value = 'Backend not available';
    return;
  }
  if (!query.value.trim()) {
    candidates.value = [];
    totalSearched.value = 0;
    error.value = '';
    return;
  }
  loading.value = true;
  error.value = '';
  try {
    const result = await api.search_components(
      query.value.trim(),
      libraryFilter.value || null,
      50,
    );
    if (result.success) {
      candidates.value = result.candidates ?? [];
      totalSearched.value = result.total_searched ?? 0;
    } else {
      error.value = result.error ?? 'Search failed';
      candidates.value = [];
    }
  } catch (e: any) {
    error.value = e.message ?? 'Unknown error';
    candidates.value = [];
  } finally {
    loading.value = false;
  }
}

function scheduleSearch() {
  if (debounceTimer !== null) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(runSearch, 350);
}

function selectCandidate(c: ComponentCandidate) {
  selectedCandidate.value = selectedCandidate.value?.lib_id === c.lib_id ? null : c;
}

function copyLibId(libId: string) {
  navigator.clipboard.writeText(libId).catch(() => {});
}

function insertInChat(candidate: ComponentCandidate) {
  const keywords = candidate.keywords.length > 0 ? candidate.keywords.join(', ') : '(none)';
  const text = `Component: **${candidate.lib_id}**\nDescription: ${candidate.description || '(none)'}\nPins: ${candidate.pin_count}\nKeywords: ${keywords}`;
  emit('insert-in-chat', text);
}

// Reload library list when the component is first interacted with
function onSearchFocus() {
  if (libraries.value.length === 0) {
    loadLibraries();
  }
}

// Rerun search when library filter changes
watch(libraryFilter, () => {
  if (query.value.trim()) scheduleSearch();
});

// Computed: sortable properties for the detail view (exclude hidden properties)
const detailProperties = computed<[string, string][]>(() => {
  if (!selectedCandidate.value) return [];
  const skipKeys = new Set(['Reference', 'Value', 'Footprint', 'Datasheet', 'ki_description', 'ki_keywords']);
  return Object.entries(selectedCandidate.value.properties).filter(
    ([k]) => !skipKeys.has(k),
  );
});
</script>

<template>
  <div class="component-search">
    <!-- Search controls -->
    <div class="search-controls">
      <div class="search-input-row">
        <span class="material-icons search-icon">search</span>
        <input
          v-model="query"
          type="text"
          class="search-input"
          placeholder="Search components…"
          @input="scheduleSearch"
          @focus="onSearchFocus"
        />
        <button
          v-if="query"
          class="clear-btn"
          @click="query = ''; candidates = []; error = ''"
          title="Clear search"
        >
          <span class="material-icons">close</span>
        </button>
      </div>
      <select v-model="libraryFilter" class="library-select" title="Filter by library">
        <option value="">All libraries</option>
        <option v-for="lib in libraries" :key="lib" :value="lib">{{ lib }}</option>
      </select>
    </div>

    <!-- Status bar -->
    <div class="status-bar">
      <span v-if="loading" class="status-loading">
        <span class="material-icons spinning">sync</span>
        Searching…
      </span>
      <span v-else-if="error" class="status-error">{{ error }}</span>
      <span v-else-if="query && candidates.length > 0" class="status-count">
        {{ candidates.length }} result{{ candidates.length !== 1 ? 's' : '' }}
        <template v-if="totalSearched > 0"> / {{ totalSearched }} scanned</template>
      </span>
      <span v-else-if="query && !loading" class="status-empty">No results</span>
      <span v-else class="status-hint">Type to search KiCad symbol libraries</span>
    </div>

    <!-- Results list -->
    <div class="results-list">
      <div
        v-for="c in candidates"
        :key="c.lib_id"
        :class="['result-row', { selected: selectedCandidate?.lib_id === c.lib_id }]"
        @click="selectCandidate(c)"
      >
        <div class="result-main">
          <span class="result-lib-id">{{ c.lib_id }}</span>
          <span v-if="c.description" class="result-desc">{{ c.description }}</span>
        </div>
        <div class="result-meta">
          <span class="pin-badge" title="Pin count">
            <span class="material-icons pin-icon">device_hub</span>{{ c.pin_count }}
          </span>
        </div>
      </div>

      <div v-if="!loading && !query" class="empty-state">
        <span class="material-icons">electrical_services</span>
        <p>Search for electronic components across KiCad symbol libraries.</p>
      </div>
    </div>

    <!-- Detail panel -->
    <transition name="slide-up">
      <div v-if="selectedCandidate" class="detail-panel">
        <div class="detail-header">
          <div class="detail-title-row">
            <span class="detail-lib-id">{{ selectedCandidate.lib_id }}</span>
            <div class="detail-actions">
              <button
                class="action-btn"
                title="Copy lib:symbol id"
                @click="copyLibId(selectedCandidate.lib_id)"
              >
                <span class="material-icons">content_copy</span>
              </button>
              <button
                class="action-btn primary"
                title="Insert in chat"
                @click="insertInChat(selectedCandidate)"
              >
                <span class="material-icons">chat</span>
                Chat
              </button>
            </div>
          </div>
          <p v-if="selectedCandidate.description" class="detail-desc">
            {{ selectedCandidate.description }}
          </p>
        </div>

        <div class="detail-body">
          <div v-if="selectedCandidate.keywords.length" class="detail-section">
            <div class="detail-section-label">Keywords</div>
            <div class="keyword-list">
              <span
                v-for="kw in selectedCandidate.keywords"
                :key="kw"
                class="keyword-chip"
              >{{ kw }}</span>
            </div>
          </div>

          <div class="detail-section">
            <div class="detail-section-label">Properties</div>
            <table class="props-table">
              <tbody>
                <tr>
                  <td class="prop-key">Reference</td>
                  <td class="prop-val">{{ selectedCandidate.properties['Reference'] || '—' }}</td>
                </tr>
                <tr>
                  <td class="prop-key">Value</td>
                  <td class="prop-val">{{ selectedCandidate.properties['Value'] || '—' }}</td>
                </tr>
                <tr>
                  <td class="prop-key">Footprint</td>
                  <td class="prop-val">{{ selectedCandidate.properties['Footprint'] || '—' }}</td>
                </tr>
                <tr>
                  <td class="prop-key">Datasheet</td>
                  <td class="prop-val">{{ selectedCandidate.properties['Datasheet'] || '—' }}</td>
                </tr>
                <tr>
                  <td class="prop-key">Pins</td>
                  <td class="prop-val">{{ selectedCandidate.pin_count }}</td>
                </tr>
                <tr v-for="[k, v] in detailProperties" :key="k">
                  <td class="prop-key">{{ k }}</td>
                  <td class="prop-val">{{ v }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.component-search {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background-color: var(--bg-primary);
}

/* Search controls */
.search-controls {
  padding: 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.search-input-row {
  display: flex;
  align-items: center;
  background-color: var(--bg-input);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  padding: 0.2rem 0.4rem;
  gap: 0.25rem;
}

.search-input-row:focus-within {
  border-color: var(--accent-color);
}

.search-icon {
  font-size: 1rem;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.search-input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 0.8rem;
  padding: 0.15rem 0;
}

.search-input::placeholder {
  color: var(--text-secondary);
}

.clear-btn {
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 0;
  display: flex;
  align-items: center;
}

.clear-btn .material-icons {
  font-size: 0.9rem;
}

.clear-btn:hover {
  color: var(--text-primary);
}

.library-select {
  width: 100%;
  padding: 0.25rem 0.4rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background-color: var(--bg-input);
  color: var(--text-primary);
  font-size: 0.75rem;
  cursor: pointer;
  outline: none;
}

.library-select:focus {
  border-color: var(--accent-color);
}

/* Status bar */
.status-bar {
  padding: 0.25rem 0.5rem;
  font-size: 0.7rem;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
  min-height: 1.5rem;
  display: flex;
  align-items: center;
}

.status-loading {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}

.status-loading .material-icons {
  font-size: 0.85rem;
}

.status-error {
  color: #e53e3e;
}

.status-count {
  color: var(--text-secondary);
}

.status-empty {
  color: var(--text-secondary);
  font-style: italic;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Results list */
.results-list {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

.result-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.4rem 0.5rem;
  border-bottom: 1px solid var(--border-color);
  cursor: pointer;
  transition: background-color 0.1s ease;
  gap: 0.5rem;
}

.result-row:hover {
  background-color: var(--bg-tertiary);
}

.result-row.selected {
  background-color: color-mix(in srgb, var(--accent-color) 12%, transparent);
  border-left: 2px solid var(--accent-color);
}

.result-main {
  flex: 1;
  min-width: 0;
}

.result-lib-id {
  display: block;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--text-primary);
  font-family: 'Consolas', 'Monaco', monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.result-desc {
  display: block;
  font-size: 0.65rem;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 0.1rem;
}

.result-meta {
  flex-shrink: 0;
}

.pin-badge {
  display: flex;
  align-items: center;
  gap: 0.1rem;
  font-size: 0.65rem;
  color: var(--text-secondary);
}

.pin-icon {
  font-size: 0.7rem;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 2rem 1rem;
  color: var(--text-secondary);
  text-align: center;
  gap: 0.5rem;
}

.empty-state .material-icons {
  font-size: 2rem;
  opacity: 0.4;
}

.empty-state p {
  font-size: 0.75rem;
  line-height: 1.4;
}

/* Detail panel */
.detail-panel {
  border-top: 2px solid var(--accent-color);
  background-color: var(--bg-secondary);
  max-height: 40%;
  overflow-y: auto;
  flex-shrink: 0;
}

.detail-header {
  padding: 0.5rem;
  border-bottom: 1px solid var(--border-color);
}

.detail-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.detail-lib-id {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 0.75rem;
  font-weight: 700;
  color: var(--accent-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.detail-desc {
  font-size: 0.7rem;
  color: var(--text-secondary);
  margin-top: 0.25rem;
  line-height: 1.4;
}

.detail-actions {
  display: flex;
  gap: 0.25rem;
  flex-shrink: 0;
}

.action-btn {
  display: flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.2rem 0.4rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background-color: transparent;
  color: var(--text-secondary);
  font-size: 0.65rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.action-btn:hover {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}

.action-btn.primary {
  background-color: var(--accent-color);
  border-color: var(--accent-color);
  color: #fff;
}

.action-btn.primary:hover {
  background-color: var(--accent-hover);
}

.action-btn .material-icons {
  font-size: 0.8rem;
}

.detail-body {
  padding: 0.4rem 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.detail-section {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.detail-section-label {
  font-size: 0.65rem;
  font-weight: 700;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.keyword-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.keyword-chip {
  padding: 0.1rem 0.35rem;
  border-radius: 99px;
  background-color: color-mix(in srgb, var(--accent-color) 15%, transparent);
  color: var(--accent-color);
  font-size: 0.65rem;
  font-weight: 500;
}

.props-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.68rem;
}

.props-table tr:nth-child(even) {
  background-color: var(--bg-tertiary);
}

.prop-key {
  padding: 0.15rem 0.4rem 0.15rem 0;
  color: var(--text-secondary);
  white-space: nowrap;
  width: 35%;
  vertical-align: top;
}

.prop-val {
  padding: 0.15rem 0;
  color: var(--text-primary);
  word-break: break-word;
  font-family: 'Consolas', 'Monaco', monospace;
}

/* Transition */
.slide-up-enter-active,
.slide-up-leave-active {
  transition: all 0.2s ease;
}

.slide-up-enter-from,
.slide-up-leave-to {
  transform: translateY(20px);
  opacity: 0;
}
</style>
