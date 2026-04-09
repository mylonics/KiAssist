<script setup lang="ts">
import { useLibraryScanner } from '../composables/useLibraryScanner';

const {
  scanResults,
  scanError,
  scanning,
  fixing,
  fixResults,
  expandedLib,
  filterSeverity,
  filteredIssues,
  totalStats,
  hasResults,
  fixableLibs,
  fixAll,
  toggleExpand,
  closeResults,
  resultFor,
  severityIcon,
  severityColor,
  libTypeIcon,
  statusBadge,
} = useLibraryScanner();
</script>

<template>
  <div class="results-container" v-if="hasResults || scanError">
    <!-- Header bar -->
    <div class="results-header">
      <div class="results-header-left">
        <span class="material-icons results-header-icon">biotech</span>
        <span class="results-header-title">Scan Results</span>
        <span class="results-count">{{ scanResults.length }} {{ scanResults.length === 1 ? 'library' : 'libraries' }}</span>
      </div>
      <div class="results-header-right">
        <button
          class="results-btn fix"
          @click="fixAll"
          :disabled="fixing || fixableLibs.length === 0"
          v-if="totalStats.fixable > 0"
        >
          <span class="material-icons btn-icon">{{ fixing ? '' : 'build' }}</span>
          <span class="spinner-sm" v-if="fixing"></span>
          {{ fixing ? 'Fixing…' : `Fix All (${totalStats.fixable})` }}
        </button>
        <button class="results-btn close" @click="closeResults" title="Close results">
          <span class="material-icons">close</span>
        </button>
      </div>
    </div>

    <!-- Summary pills -->
    <div class="results-summary">
      <span class="summary-pill error" v-if="totalStats.errors">
        <span class="material-icons pill-icon">error</span>
        {{ totalStats.errors }} {{ totalStats.errors === 1 ? 'error' : 'errors' }}
      </span>
      <span class="summary-pill warning" v-if="totalStats.warnings">
        <span class="material-icons pill-icon">warning</span>
        {{ totalStats.warnings }} {{ totalStats.warnings === 1 ? 'warning' : 'warnings' }}
      </span>
      <span class="summary-pill info" v-if="totalStats.infos">
        <span class="material-icons pill-icon">info</span>
        {{ totalStats.infos }} info
      </span>
      <span class="summary-pill fixable" v-if="totalStats.fixable">
        <span class="material-icons pill-icon">build</span>
        {{ totalStats.fixable }} fixable
      </span>
      <span class="summary-pill ok" v-if="totalStats.errors === 0 && totalStats.warnings === 0 && totalStats.infos === 0">
        <span class="material-icons pill-icon">check_circle</span>
        All libraries clean!
      </span>
    </div>

    <!-- Fix results banner -->
    <div class="fix-banner" v-if="fixResults.length > 0">
      <div class="fix-banner-header">
        <span class="material-icons" style="font-size: 1rem; color: var(--color-success, #43a047)">check_circle</span>
        <span>Fixes applied</span>
      </div>
      <div class="fix-banner-list">
        <div v-for="fr in fixResults" :key="fr.nickname" class="fix-entry">
          <span class="fix-nick">{{ fr.nickname }}</span>
          <span class="fix-count" v-if="!fr.error">{{ fr.fixes }} fixed</span>
          <span class="fix-error" v-else>{{ fr.error }}</span>
        </div>
      </div>
    </div>

    <!-- Scan error -->
    <div class="error-banner" v-if="scanError">
      <span class="material-icons" style="font-size: 1rem">error</span>
      {{ scanError }}
    </div>

    <!-- Results list -->
    <div class="results-list">
      <div
        v-for="result in scanResults"
        :key="`${result.type}:${result.nickname}`"
        class="result-card"
        :class="{ expanded: expandedLib === `${result.type}:${result.nickname}` }"
      >
        <div class="result-card-header" @click="toggleExpand(`${result.type}:${result.nickname}`)">
          <span class="material-icons lib-type-icon" :title="result.type === 'sym' ? 'Symbol Library' : 'Footprint Library'">
            {{ libTypeIcon(result.type) }}
          </span>
          <span class="result-nick">{{ result.nickname }}</span>
          <div class="result-chips">
            <span class="chip error" v-if="(result.errors ?? 0) > 0">{{ result.errors }}E</span>
            <span class="chip warning" v-if="(result.warnings ?? 0) > 0">{{ result.warnings }}W</span>
            <span class="chip info" v-if="(result.infos ?? 0) > 0">{{ result.infos }}I</span>
            <span class="chip fixable" v-if="(result.fixable ?? 0) > 0">{{ result.fixable }} fix</span>
            <span class="chip ok" v-if="(result.errors ?? 0) === 0 && (result.warnings ?? 0) === 0">
              <span class="material-icons" style="font-size: 0.8rem">check_circle</span>
              OK
            </span>
          </div>
          <span class="material-icons expand-chevron">
            {{ expandedLib === `${result.type}:${result.nickname}` ? 'expand_less' : 'expand_more' }}
          </span>
        </div>

        <!-- Expanded detail -->
        <div v-if="expandedLib === `${result.type}:${result.nickname}`" class="result-detail">
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

          <!-- Issues -->
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
            <div v-if="filteredIssues.length === 0" class="no-issues">No issues matching this filter.</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.results-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  font-size: 0.85rem;
}

/* Header */
.results-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.6rem 0.8rem;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-secondary);
  flex-shrink: 0;
}
.results-header-left {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.results-header-icon {
  font-size: 1.2rem;
  color: var(--accent-color);
}
.results-header-title {
  font-weight: 700;
  font-size: 0.95rem;
  color: var(--text-primary);
}
.results-count {
  font-size: 0.75rem;
  color: var(--text-secondary);
  padding: 0.1rem 0.4rem;
  background: var(--bg-tertiary);
  border-radius: 10px;
}
.results-header-right {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

/* Buttons */
.results-btn {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.35rem 0.65rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: 0.78rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.12s;
}
.results-btn:hover:not(:disabled) {
  background: var(--bg-tertiary);
}
.results-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.results-btn.fix {
  background: var(--color-success, #43a047);
  color: #fff;
  border-color: var(--color-success, #43a047);
}
.results-btn.fix:hover:not(:disabled) {
  filter: brightness(1.1);
}
.results-btn.close {
  background: none;
  border: none;
  color: var(--text-secondary);
  padding: 0.25rem;
}
.results-btn.close:hover {
  color: var(--text-primary);
}
.btn-icon { font-size: 1rem; }

/* Summary */
.results-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  padding: 0.5rem 0.8rem;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}
.summary-pill {
  display: flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.2rem 0.55rem;
  border-radius: 12px;
  font-size: 0.75rem;
  font-weight: 700;
}
.pill-icon { font-size: 0.85rem; }
.summary-pill.error   { background: rgba(229,57,53,0.1); color: #e53935; }
.summary-pill.warning { background: rgba(251,140,0,0.1); color: #fb8c00; }
.summary-pill.info    { background: rgba(66,165,245,0.1); color: #42a5f5; }
.summary-pill.fixable { background: rgba(67,160,71,0.1); color: #43a047; }
.summary-pill.ok      { background: rgba(67,160,71,0.15); color: #43a047; }

/* Fix banner */
.fix-banner {
  padding: 0.45rem 0.8rem;
  background: rgba(67,160,71,0.06);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}
.fix-banner-header {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-weight: 700;
  font-size: 0.8rem;
  color: var(--text-primary);
  margin-bottom: 0.25rem;
}
.fix-banner-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem 0.8rem;
}
.fix-entry {
  display: flex;
  gap: 0.35rem;
  font-size: 0.76rem;
}
.fix-nick { color: var(--text-primary); font-weight: 600; }
.fix-count { color: var(--color-success, #43a047); font-weight: 600; }
.fix-error { color: var(--color-error, #e53935); }

/* Error banner */
.error-banner {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.45rem 0.8rem;
  background: rgba(229,57,53,0.06);
  border-bottom: 1px solid var(--border-color);
  color: var(--color-error, #e53935);
  font-size: 0.8rem;
  font-weight: 600;
  flex-shrink: 0;
}

/* Results list */
.results-list {
  flex: 1;
  overflow-y: auto;
  padding: 0.4rem 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.result-card {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-primary);
  overflow: hidden;
}
.result-card.expanded {
  border-color: var(--accent-color);
}

.result-card-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.45rem 0.6rem;
  cursor: pointer;
  transition: background 0.1s;
}
.result-card-header:hover {
  background: var(--bg-tertiary);
}

.lib-type-icon {
  font-size: 1rem;
  color: var(--text-secondary);
  flex-shrink: 0;
}
.result-nick {
  flex: 1;
  font-weight: 600;
  font-size: 0.82rem;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-chips {
  display: flex;
  gap: 0.2rem;
  flex-shrink: 0;
}
.chip {
  display: flex;
  align-items: center;
  gap: 0.1rem;
  padding: 0.1rem 0.35rem;
  border-radius: 8px;
  font-size: 0.68rem;
  font-weight: 700;
}
.chip.error   { background: rgba(229,57,53,0.12); color: #e53935; }
.chip.warning { background: rgba(251,140,0,0.12); color: #fb8c00; }
.chip.info    { background: rgba(66,165,245,0.12); color: #42a5f5; }
.chip.fixable { background: rgba(67,160,71,0.12); color: #43a047; }
.chip.ok      { background: rgba(67,160,71,0.12); color: #43a047; }

.expand-chevron {
  font-size: 1.1rem;
  color: var(--text-secondary);
  flex-shrink: 0;
}

/* Detail (expanded) */
.result-detail {
  padding: 0.5rem 0.7rem 0.6rem 2.2rem;
  border-top: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

/* Filter buttons */
.detail-filter {
  display: flex;
  gap: 0.2rem;
}
.filter-btn {
  padding: 0.15rem 0.5rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-primary);
  color: var(--text-secondary);
  font-size: 0.72rem;
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
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.issue-row {
  display: flex;
  align-items: flex-start;
  gap: 0.3rem;
  padding: 0.25rem 0;
  font-size: 0.78rem;
}
.issue-icon { font-size: 0.95rem; flex-shrink: 0; margin-top: 1px; }
.issue-body {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
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
  font-size: 0.62rem;
  font-weight: 800;
  color: #43a047;
  background: rgba(67,160,71,0.1);
  padding: 0.05rem 0.3rem;
  border-radius: 3px;
  white-space: nowrap;
}
.no-issues {
  font-size: 0.78rem;
  color: var(--text-secondary);
  padding: 0.5rem;
  text-align: center;
}

/* Spinner */
.spinner-sm {
  display: inline-block;
  width: 12px; height: 12px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
