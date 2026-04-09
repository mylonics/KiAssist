<script setup lang="ts">
import { ref, computed } from 'vue';
import type {
  AnalyzerLibInfo,
  AnalyzerBatchResult,
  AnalyzerIssue,
  AnalyzerLibEntry,
} from '../types/pywebview';

// -----------------------------------------------------------------------
// State
// -----------------------------------------------------------------------

// Discovery
const discovering = ref(false);
const discoverError = ref('');
const symLibraries = ref<AnalyzerLibInfo[]>([]);
const fpLibraries = ref<AnalyzerLibInfo[]>([]);

// Selection
const selectedLibs = ref<Set<string>>(new Set());

// Scanning
const scanning = ref(false);
const scanProgress = ref(0);
const scanTotal = ref(0);
const scanResults = ref<AnalyzerBatchResult[]>([]);
const scanError = ref('');

// Fixing
const fixing = ref(false);
const fixResults = ref<{ nickname: string; fixes: number; error?: string }[]>([]);

// Detail view
const expandedLib = ref<string | null>(null);
const filterSeverity = ref<'all' | 'error' | 'warning' | 'info'>('all');

// -----------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------

function getApi() {
  return (window as any).pywebview?.api ?? null;
}

// Combine all libs into selectable entries
const allLibraries = computed(() => {
  const entries: (AnalyzerLibInfo & { type: 'sym' | 'fp'; key: string })[] = [];
  for (const lib of symLibraries.value) {
    entries.push({ ...lib, type: 'sym', key: `sym:${lib.nickname}` });
  }
  for (const lib of fpLibraries.value) {
    entries.push({ ...lib, type: 'fp', key: `fp:${lib.nickname}` });
  }
  return entries;
});

const allSelected = computed(() =>
  allLibraries.value.length > 0 && selectedLibs.value.size === allLibraries.value.length
);

const someSelected = computed(() =>
  selectedLibs.value.size > 0 && selectedLibs.value.size < allLibraries.value.length
);

// Get result for a library key
function resultFor(key: string): AnalyzerBatchResult | undefined {
  const [type, nick] = key.split(':', 2);
  return scanResults.value.find(r => r.nickname === nick && r.type === type);
}

// Filter issues for expanded lib
const filteredIssues = computed((): AnalyzerIssue[] => {
  if (!expandedLib.value) return [];
  const r = resultFor(expandedLib.value);
  if (!r) return [];
  if (filterSeverity.value === 'all') return r.issues;
  return r.issues.filter(i => i.severity === filterSeverity.value);
});

// Total stats across scanned results
const totalStats = computed(() => {
  let errors = 0, warnings = 0, infos = 0, fixable = 0;
  for (const r of scanResults.value) {
    errors += r.errors ?? 0;
    warnings += r.warnings ?? 0;
    infos += r.infos ?? 0;
    fixable += r.fixable ?? 0;
  }
  return { errors, warnings, infos, fixable };
});

const hasResults = computed(() => scanResults.value.length > 0);

const fixableLibs = computed(() => {
  return scanResults.value.filter(r => (r.fixable ?? 0) > 0);
});

// -----------------------------------------------------------------------
// Actions
// -----------------------------------------------------------------------

async function discoverLibraries() {
  const api = getApi();
  if (!api) { discoverError.value = 'Backend not available'; return; }

  discovering.value = true;
  discoverError.value = '';
  symLibraries.value = [];
  fpLibraries.value = [];
  selectedLibs.value = new Set();
  scanResults.value = [];
  fixResults.value = [];
  expandedLib.value = null;

  try {
    const r = await api.analyzer_scan_libraries('both');
    if (r?.success) {
      symLibraries.value = r.symbol_libraries ?? [];
      fpLibraries.value = r.footprint_libraries ?? [];
      // Auto-select all
      for (const lib of allLibraries.value) {
        selectedLibs.value.add(lib.key);
      }
    } else {
      discoverError.value = r?.error || 'Failed to discover libraries';
    }
  } catch (e: any) {
    discoverError.value = e?.message || 'Unexpected error';
  } finally {
    discovering.value = false;
  }
}

function toggleLib(key: string) {
  if (selectedLibs.value.has(key)) {
    selectedLibs.value.delete(key);
  } else {
    selectedLibs.value.add(key);
  }
  // Force reactivity
  selectedLibs.value = new Set(selectedLibs.value);
}

function toggleAll() {
  if (allSelected.value) {
    selectedLibs.value = new Set();
  } else {
    selectedLibs.value = new Set(allLibraries.value.map(l => l.key));
  }
}

async function scanSelected() {
  const api = getApi();
  if (!api) return;

  const entries: AnalyzerLibEntry[] = [];
  for (const lib of allLibraries.value) {
    if (selectedLibs.value.has(lib.key)) {
      entries.push({
        nickname: lib.nickname,
        path: lib.resolved_path,
        type: lib.type,
      });
    }
  }
  if (!entries.length) return;

  scanning.value = true;
  scanError.value = '';
  scanResults.value = [];
  fixResults.value = [];
  scanTotal.value = entries.length;
  scanProgress.value = 0;
  expandedLib.value = null;

  try {
    const r = await api.analyzer_batch_scan(entries);
    if (r?.success) {
      scanResults.value = r.results ?? [];
      scanProgress.value = entries.length;
    } else {
      scanError.value = r?.error || 'Scan failed';
    }
  } catch (e: any) {
    scanError.value = e?.message || 'Unexpected error';
  } finally {
    scanning.value = false;
  }
}

async function fixAll() {
  const api = getApi();
  if (!api) return;

  const entries: AnalyzerLibEntry[] = fixableLibs.value.map(r => ({
    nickname: r.nickname,
    path: r.file_path ?? '',
    type: r.type,
  }));

  // Build path from allLibraries since report file_path may not have it
  for (const entry of entries) {
    const lib = allLibraries.value.find(
      l => l.nickname === entry.nickname && l.type === entry.type
    );
    if (lib) entry.path = lib.resolved_path;
  }

  if (!entries.length) return;

  fixing.value = true;
  fixResults.value = [];

  try {
    const r = await api.analyzer_batch_fix(entries);
    if (r?.success) {
      fixResults.value = (r.results ?? []).map((fr: any) => ({
        nickname: fr.nickname,
        fixes: fr.fixes_applied ?? 0,
        error: fr.error,
      }));
      // Re-scan to show updated results
      await scanSelected();
    } else {
      scanError.value = r?.error || 'Fix failed';
    }
  } catch (e: any) {
    scanError.value = e?.message || 'Fix error';
  } finally {
    fixing.value = false;
  }
}

function toggleExpand(key: string) {
  expandedLib.value = expandedLib.value === key ? null : key;
}

function severityIcon(s: string) {
  if (s === 'error') return 'error';
  if (s === 'warning') return 'warning';
  return 'info';
}

function severityColor(s: string) {
  if (s === 'error') return 'var(--color-error, #e53935)';
  if (s === 'warning') return 'var(--color-warning, #fb8c00)';
  return 'var(--color-info, #42a5f5)';
}

function libTypeIcon(t: string) {
  return t === 'sym' ? 'memory' : 'view_in_ar';
}

function statusBadge(r: AnalyzerBatchResult) {
  if ((r.errors ?? 0) > 0) return { icon: 'error', color: severityColor('error'), text: `${r.errors}E` };
  if ((r.warnings ?? 0) > 0) return { icon: 'warning', color: severityColor('warning'), text: `${r.warnings}W` };
  return { icon: 'check_circle', color: 'var(--color-success, #43a047)', text: 'OK' };
}
</script>

<template>
  <div class="scanner-container">
    <!-- Header -->
    <div class="scanner-header">
      <span class="material-icons scanner-header-icon">biotech</span>
      <span class="scanner-header-title">Library Scanner</span>
    </div>

    <!-- Discover button -->
    <div class="scanner-section" v-if="allLibraries.length === 0 && !discovering">
      <button class="scanner-btn primary" @click="discoverLibraries" :disabled="discovering">
        <span class="material-icons btn-icon">search</span>
        Discover Libraries
      </button>
      <p v-if="discoverError" class="error-text">{{ discoverError }}</p>
    </div>

    <!-- Loading -->
    <div class="scanner-section" v-if="discovering">
      <div class="loading-row">
        <span class="spinner"></span>
        <span class="loading-text">Discovering libraries…</span>
      </div>
    </div>

    <!-- Library list -->
    <div class="lib-list-section" v-if="allLibraries.length > 0">
      <div class="lib-list-header">
        <label class="select-all-row" @click.prevent="toggleAll">
          <input
            type="checkbox"
            :checked="allSelected"
            :indeterminate="someSelected && !allSelected"
            @click.stop="toggleAll"
          />
          <span class="select-all-label">
            {{ allLibraries.length }} {{ allLibraries.length === 1 ? 'library' : 'libraries' }}
          </span>
        </label>
        <button class="scanner-btn-sm" @click="discoverLibraries" title="Refresh">
          <span class="material-icons" style="font-size: 0.9rem">refresh</span>
        </button>
      </div>

      <div class="lib-list">
        <div
          v-for="lib in allLibraries"
          :key="lib.key"
          class="lib-row"
          :class="{ expanded: expandedLib === lib.key }"
        >
          <div class="lib-row-main" @click="toggleLib(lib.key)">
            <input
              type="checkbox"
              :checked="selectedLibs.has(lib.key)"
              @click.stop="toggleLib(lib.key)"
            />
            <span class="material-icons lib-type-icon" :title="lib.type === 'sym' ? 'Symbol Library' : 'Footprint Library'">
              {{ libTypeIcon(lib.type) }}
            </span>
            <span class="lib-nick" :title="lib.resolved_path">{{ lib.nickname }}</span>

            <!-- Result badge if scanned -->
            <template v-if="resultFor(lib.key)">
              <span
                class="result-badge"
                :style="{ color: statusBadge(resultFor(lib.key)!).color }"
                @click.stop="toggleExpand(lib.key)"
                :title="'Click for details'"
              >
                <span class="material-icons badge-icon">{{ statusBadge(resultFor(lib.key)!).icon }}</span>
                {{ statusBadge(resultFor(lib.key)!).text }}
                <span class="material-icons expand-icon">{{ expandedLib === lib.key ? 'expand_less' : 'expand_more' }}</span>
              </span>
            </template>
          </div>

          <!-- Expanded detail -->
          <div v-if="expandedLib === lib.key && resultFor(lib.key)" class="lib-detail">
            <div class="detail-stats">
              <span class="stat-chip error" v-if="resultFor(lib.key)!.errors">{{ resultFor(lib.key)!.errors }} errors</span>
              <span class="stat-chip warning" v-if="resultFor(lib.key)!.warnings">{{ resultFor(lib.key)!.warnings }} warnings</span>
              <span class="stat-chip info" v-if="resultFor(lib.key)!.infos">{{ resultFor(lib.key)!.infos }} info</span>
              <span class="stat-chip fixable" v-if="resultFor(lib.key)!.fixable">{{ resultFor(lib.key)!.fixable }} fixable</span>
            </div>

            <!-- Severity filter -->
            <div class="detail-filter">
              <button
                v-for="sev in ['all', 'error', 'warning', 'info'] as const"
                :key="sev"
                :class="['filter-btn', { active: filterSeverity === sev }]"
                @click="filterSeverity = sev"
              >
                {{ sev === 'all' ? 'All' : sev.charAt(0).toUpperCase() + sev.slice(1) }}
              </button>
            </div>

            <!-- Issues list -->
            <div class="issues-list">
              <div
                v-for="(issue, idx) in filteredIssues"
                :key="idx"
                class="issue-row"
              >
                <span class="material-icons issue-icon" :style="{ color: severityColor(issue.severity) }">
                  {{ severityIcon(issue.severity) }}
                </span>
                <div class="issue-body">
                  <span class="issue-entity">{{ issue.entity }}</span>
                  <span class="issue-msg">{{ issue.message }}</span>
                  <span v-if="issue.fixable" class="issue-fixable">FIXABLE</span>
                </div>
              </div>
              <div v-if="filteredIssues.length === 0" class="no-issues">No issues in this filter.</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Action buttons -->
    <div class="scanner-actions" v-if="allLibraries.length > 0">
      <button
        class="scanner-btn primary"
        @click="scanSelected"
        :disabled="scanning || selectedLibs.size === 0"
      >
        <span class="material-icons btn-icon">{{ scanning ? '' : 'radar' }}</span>
        <span class="spinner-sm" v-if="scanning"></span>
        {{ scanning ? 'Scanning…' : `Scan (${selectedLibs.size})` }}
      </button>
      <button
        class="scanner-btn fix"
        @click="fixAll"
        :disabled="fixing || fixableLibs.length === 0"
        v-if="hasResults"
      >
        <span class="material-icons btn-icon">{{ fixing ? '' : 'build' }}</span>
        <span class="spinner-sm" v-if="fixing"></span>
        {{ fixing ? 'Fixing…' : `Fix All (${totalStats.fixable})` }}
      </button>
    </div>

    <!-- Summary bar -->
    <div class="scan-summary" v-if="hasResults && !scanning">
      <span class="summary-pill error" v-if="totalStats.errors">{{ totalStats.errors }} errors</span>
      <span class="summary-pill warning" v-if="totalStats.warnings">{{ totalStats.warnings }} warnings</span>
      <span class="summary-pill info" v-if="totalStats.infos">{{ totalStats.infos }} info</span>
      <span class="summary-pill fixable" v-if="totalStats.fixable">{{ totalStats.fixable }} fixable</span>
      <span class="summary-pill ok" v-if="totalStats.errors === 0 && totalStats.warnings === 0">All clean!</span>
    </div>

    <!-- Fix results -->
    <div class="fix-summary" v-if="fixResults.length > 0">
      <div class="fix-summary-header">
        <span class="material-icons" style="font-size: 0.9rem; color: var(--color-success, #43a047)">check_circle</span>
        Fixes applied
      </div>
      <div v-for="fr in fixResults" :key="fr.nickname" class="fix-entry">
        <span class="fix-nick">{{ fr.nickname }}</span>
        <span class="fix-count" v-if="!fr.error">{{ fr.fixes }} fixed</span>
        <span class="fix-error" v-else>{{ fr.error }}</span>
      </div>
    </div>

    <!-- Scan error -->
    <p v-if="scanError" class="error-text">{{ scanError }}</p>
  </div>
</template>

<style scoped>
.scanner-container {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  padding: 0.5rem 0.6rem;
  font-size: 0.8rem;
}

/* Header */
.scanner-header {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding-bottom: 0.25rem;
}
.scanner-header-icon {
  font-size: 1.1rem;
  color: var(--accent-color);
}
.scanner-header-title {
  font-weight: 700;
  font-size: 0.82rem;
  color: var(--text-primary);
  letter-spacing: 0.01em;
}

/* Section */
.scanner-section {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

/* Buttons */
.scanner-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.3rem;
  padding: 0.4rem 0.7rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: 0.76rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.12s;
}
.scanner-btn:hover:not(:disabled) {
  background: var(--bg-tertiary);
}
.scanner-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.scanner-btn.primary {
  background: var(--accent-color);
  color: #fff;
  border-color: var(--accent-color);
}
.scanner-btn.primary:hover:not(:disabled) {
  background: var(--accent-hover);
}
.scanner-btn.fix {
  background: var(--color-success, #43a047);
  color: #fff;
  border-color: var(--color-success, #43a047);
}
.scanner-btn.fix:hover:not(:disabled) {
  filter: brightness(1.1);
}
.scanner-btn-sm {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-secondary);
  padding: 0.15rem;
  border-radius: var(--radius-sm);
  transition: color 0.12s;
}
.scanner-btn-sm:hover {
  color: var(--accent-color);
}
.btn-icon {
  font-size: 1rem;
}

/* Loading */
.loading-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  color: var(--text-secondary);
}
.loading-text { font-size: 0.76rem; }
.spinner {
  width: 14px; height: 14px;
  border: 2px solid var(--border-color);
  border-top-color: var(--accent-color);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}
.spinner-sm {
  display: inline-block;
  width: 12px; height: 12px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* Library list */
.lib-list-section {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.lib-list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.15rem 0;
}
.select-all-row {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  cursor: pointer;
  font-size: 0.73rem;
  color: var(--text-secondary);
  font-weight: 600;
}
.select-all-label { user-select: none; }

.lib-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
  max-height: 260px;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-secondary);
}

.lib-row {
  background: var(--bg-primary);
}
.lib-row.expanded {
  background: var(--bg-tertiary);
}

.lib-row-main {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.28rem 0.4rem;
  cursor: pointer;
  transition: background 0.1s;
}
.lib-row-main:hover {
  background: var(--bg-tertiary);
}

.lib-type-icon {
  font-size: 0.9rem;
  color: var(--text-secondary);
}
.lib-nick {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.75rem;
  color: var(--text-primary);
}

/* Result badge */
.result-badge {
  display: flex;
  align-items: center;
  gap: 0.15rem;
  font-size: 0.68rem;
  font-weight: 700;
  cursor: pointer;
  white-space: nowrap;
}
.badge-icon { font-size: 0.85rem; }
.expand-icon { font-size: 0.85rem; opacity: 0.6; }

/* Detail (expanded) */
.lib-detail {
  padding: 0.35rem 0.5rem 0.5rem 1.8rem;
  border-top: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.detail-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}
.stat-chip {
  padding: 0.1rem 0.4rem;
  border-radius: 9px;
  font-size: 0.65rem;
  font-weight: 700;
}
.stat-chip.error   { background: rgba(229,57,53,0.12); color: #e53935; }
.stat-chip.warning { background: rgba(251,140,0,0.12); color: #fb8c00; }
.stat-chip.info    { background: rgba(66,165,245,0.12); color: #42a5f5; }
.stat-chip.fixable { background: rgba(67,160,71,0.12); color: #43a047; }

/* Filter buttons */
.detail-filter {
  display: flex;
  gap: 0.15rem;
}
.filter-btn {
  padding: 0.12rem 0.4rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-primary);
  color: var(--text-secondary);
  font-size: 0.65rem;
  cursor: pointer;
  transition: all 0.1s;
}
.filter-btn.active {
  background: var(--accent-color);
  color: #fff;
  border-color: var(--accent-color);
}

/* Issues list */
.issues-list {
  max-height: 200px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.issue-row {
  display: flex;
  align-items: flex-start;
  gap: 0.25rem;
  padding: 0.2rem 0;
  font-size: 0.7rem;
}
.issue-icon { font-size: 0.85rem; flex-shrink: 0; margin-top: 1px; }
.issue-body {
  display: flex;
  flex-wrap: wrap;
  gap: 0.2rem;
  align-items: baseline;
  min-width: 0;
}
.issue-entity {
  font-weight: 700;
  color: var(--text-primary);
  word-break: break-word;
}
.issue-msg {
  color: var(--text-secondary);
  word-break: break-word;
}
.issue-fixable {
  font-size: 0.58rem;
  font-weight: 800;
  color: #43a047;
  background: rgba(67,160,71,0.1);
  padding: 0 0.25rem;
  border-radius: 3px;
  white-space: nowrap;
}
.no-issues {
  font-size: 0.7rem;
  color: var(--text-secondary);
  padding: 0.3rem;
  text-align: center;
}

/* Action bar */
.scanner-actions {
  display: flex;
  gap: 0.35rem;
  padding-top: 0.15rem;
}
.scanner-actions .scanner-btn { flex: 1; }

/* Summary bar */
.scan-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  justify-content: center;
  padding: 0.25rem 0;
}
.summary-pill {
  padding: 0.12rem 0.5rem;
  border-radius: 10px;
  font-size: 0.66rem;
  font-weight: 700;
}
.summary-pill.error   { background: rgba(229,57,53,0.1); color: #e53935; }
.summary-pill.warning { background: rgba(251,140,0,0.1); color: #fb8c00; }
.summary-pill.info    { background: rgba(66,165,245,0.1); color: #42a5f5; }
.summary-pill.fixable { background: rgba(67,160,71,0.1); color: #43a047; }
.summary-pill.ok      { background: rgba(67,160,71,0.15); color: #43a047; }

/* Fix results */
.fix-summary {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  padding: 0.3rem 0.4rem;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  font-size: 0.72rem;
}
.fix-summary-header {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-weight: 700;
  color: var(--text-primary);
  font-size: 0.73rem;
}
.fix-entry {
  display: flex;
  justify-content: space-between;
  padding: 0.1rem 0;
}
.fix-nick { color: var(--text-primary); font-weight: 600; }
.fix-count { color: var(--color-success, #43a047); font-weight: 600; }
.fix-error { color: var(--color-error, #e53935); font-size: 0.68rem; }

/* Error */
.error-text {
  color: var(--color-error, #e53935);
  font-size: 0.72rem;
  padding: 0.15rem 0;
}

/* Checkbox visual */
input[type="checkbox"] {
  width: 14px;
  height: 14px;
  accent-color: var(--accent-color);
  cursor: pointer;
  flex-shrink: 0;
}
</style>
