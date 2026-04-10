/**
 * Shared composable for library scanner state and actions.
 *
 * Both the left-panel controls (`LibraryScanner.vue`) and the main-panel
 * results view (`LibraryScanResults.vue`) import this composable so they
 * share the same reactive singletons.
 */
import { ref, computed } from 'vue';
import { getApi } from './useApi';
import type {
  AnalyzerLibInfo,
  AnalyzerBatchResult,
  AnalyzerIssue,
  AnalyzerLibEntry,
} from '../types/pywebview';

// -----------------------------------------------------------------------
// Module-level singleton state (shared across all consumers)
// -----------------------------------------------------------------------

// Discovery
const discovering = ref(false);
const discoverError = ref('');
const symLibraries = ref<AnalyzerLibInfo[]>([]);
const fpLibraries = ref<AnalyzerLibInfo[]>([]);

// Selection
const selectedLibs = ref<Set<string>>(new Set());
const showDefaults = ref(false);

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

// Visible subset (hides defaults unless toggled)
const visibleLibraries = computed(() =>
  showDefaults.value ? allLibraries.value : allLibraries.value.filter(l => !l.is_default)
);

const allSelected = computed(() =>
  visibleLibraries.value.length > 0 &&
  visibleLibraries.value.every(l => selectedLibs.value.has(l.key))
);

const someSelected = computed(() =>
  visibleLibraries.value.some(l => selectedLibs.value.has(l.key)) && !allSelected.value
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

const customLibCount = computed(() =>
  allLibraries.value.filter(l => !l.is_default).length
);

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
      // Auto-select only custom (non-default) libraries
      for (const lib of allLibraries.value) {
        if (!lib.is_default) {
          selectedLibs.value.add(lib.key);
        }
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
    const next = new Set(selectedLibs.value);
    for (const l of visibleLibraries.value) next.delete(l.key);
    selectedLibs.value = next;
  } else {
    const next = new Set(selectedLibs.value);
    for (const l of visibleLibraries.value) next.add(l.key);
    selectedLibs.value = next;
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

function closeResults() {
  scanResults.value = [];
  fixResults.value = [];
  scanError.value = '';
  expandedLib.value = null;
}

// -----------------------------------------------------------------------
// Display helpers
// -----------------------------------------------------------------------

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

// -----------------------------------------------------------------------
// Composable export
// -----------------------------------------------------------------------

export function useLibraryScanner() {
  return {
    // State
    discovering,
    discoverError,
    symLibraries,
    fpLibraries,
    selectedLibs,
    showDefaults,
    scanning,
    scanProgress,
    scanTotal,
    scanResults,
    scanError,
    fixing,
    fixResults,
    expandedLib,
    filterSeverity,

    // Computeds
    allLibraries,
    visibleLibraries,
    allSelected,
    someSelected,
    filteredIssues,
    totalStats,
    hasResults,
    customLibCount,
    fixableLibs,

    // Actions
    discoverLibraries,
    toggleLib,
    toggleAll,
    scanSelected,
    fixAll,
    toggleExpand,
    closeResults,

    // Helpers
    resultFor,
    severityIcon,
    severityColor,
    libTypeIcon,
    statusBadge,
  };
}
