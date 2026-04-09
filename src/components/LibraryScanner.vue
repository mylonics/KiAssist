<script setup lang="ts">
import { useLibraryScanner } from '../composables/useLibraryScanner';

const {
  // State
  discovering,
  discoverError,
  selectedLibs,
  showDefaults,
  scanning,
  fixing,

  // Computeds
  allLibraries,
  visibleLibraries,
  allSelected,
  someSelected,
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

  // Helpers
  resultFor,
  libTypeIcon,
  statusBadge,
} = useLibraryScanner();
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
            {{ visibleLibraries.length }} {{ showDefaults ? 'total' : 'custom' }}
          </span>
        </label>
        <div class="lib-list-actions">
          <button
            class="scanner-btn-sm"
            @click="showDefaults = !showDefaults"
            :title="showDefaults ? 'Hide default KiCad libraries' : 'Show default KiCad libraries'"
          >
            <span class="material-icons" style="font-size: 0.9rem">{{ showDefaults ? 'visibility_off' : 'visibility' }}</span>
            <span class="toggle-label">{{ showDefaults ? 'Hide' : 'Show' }} defaults ({{ allLibraries.length - customLibCount }})</span>
          </button>
          <button class="scanner-btn-sm" @click="discoverLibraries" title="Refresh">
            <span class="material-icons" style="font-size: 0.9rem">refresh</span>
          </button>
        </div>
      </div>

      <div class="lib-list">
        <div
          v-for="lib in visibleLibraries"
          :key="lib.key"
          class="lib-row"
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
            <span v-if="lib.is_default" class="default-badge" title="Default KiCad library">KiCad</span>

            <!-- Result badge if scanned -->
            <template v-if="resultFor(lib.key)">
              <span
                class="result-badge"
                :style="{ color: statusBadge(resultFor(lib.key)!).color }"
              >
                <span class="material-icons badge-icon">{{ statusBadge(resultFor(lib.key)!).icon }}</span>
                {{ statusBadge(resultFor(lib.key)!).text }}
              </span>
            </template>
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

    <!-- Compact summary -->
    <div class="scan-summary" v-if="hasResults && !scanning">
      <span class="summary-pill error" v-if="totalStats.errors">{{ totalStats.errors }} errors</span>
      <span class="summary-pill warning" v-if="totalStats.warnings">{{ totalStats.warnings }} warnings</span>
      <span class="summary-pill info" v-if="totalStats.infos">{{ totalStats.infos }} info</span>
      <span class="summary-pill fixable" v-if="totalStats.fixable">{{ totalStats.fixable }} fixable</span>
      <span class="summary-pill ok" v-if="totalStats.errors === 0 && totalStats.warnings === 0">All clean!</span>
    </div>

    <p v-if="discoverError && allLibraries.length > 0" class="error-text">{{ discoverError }}</p>
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
  display: flex;
  align-items: center;
  gap: 0.15rem;
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
  flex-wrap: wrap;
  gap: 0.25rem;
}
.lib-list-actions {
  display: flex;
  align-items: center;
  gap: 0.25rem;
}
.toggle-label {
  font-size: 0.65rem;
  white-space: nowrap;
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

.default-badge {
  flex-shrink: 0;
  font-size: 0.58rem;
  font-weight: 700;
  padding: 0.05rem 0.3rem;
  border-radius: 3px;
  background: var(--color-info, #42a5f5);
  color: #fff;
  opacity: 0.7;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

/* Result badge */
.result-badge {
  display: flex;
  align-items: center;
  gap: 0.15rem;
  font-size: 0.68rem;
  font-weight: 700;
  white-space: nowrap;
}
.badge-icon { font-size: 0.85rem; }

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
