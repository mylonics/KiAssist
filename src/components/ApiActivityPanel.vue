<script setup lang="ts">
import { ref, computed, nextTick, watch } from 'vue';

export interface ApiActivityEntry {
  id: string;
  timestamp: Date;
  method: string;
  /** Truncated request payload (for display) */
  requestSummary: string;
  /** Full request payload */
  requestFull: string;
  /** Truncated response payload (for display) */
  responseSummary: string;
  /** Full response payload */
  responseFull: string;
  durationMs: number;
  success: boolean;
  /** Whether this is a streaming poll (collapsed by default) */
  isStreamPoll?: boolean;
}

const entries = ref<ApiActivityEntry[]>([]);
const expandedIds = ref<Set<string>>(new Set());
const showStreamPolls = ref(false);
const scrollContainer = ref<HTMLElement | null>(null);
const autoScroll = ref(true);
const filterText = ref('');

const filteredEntries = computed(() => {
  let result = entries.value;
  if (!showStreamPolls.value) {
    result = result.filter(e => !e.isStreamPoll);
  }
  if (filterText.value.trim()) {
    const q = filterText.value.toLowerCase();
    result = result.filter(
      e =>
        e.method.toLowerCase().includes(q) ||
        e.requestSummary.toLowerCase().includes(q) ||
        e.responseSummary.toLowerCase().includes(q)
    );
  }
  return result;
});

function toggleExpanded(id: string) {
  if (expandedIds.value.has(id)) {
    expandedIds.value.delete(id);
  } else {
    expandedIds.value.add(id);
  }
  // Force reactivity
  expandedIds.value = new Set(expandedIds.value);
}

function clearEntries() {
  entries.value = [];
  expandedIds.value = new Set();
}

function scrollToBottom() {
  if (!autoScroll.value) return;
  nextTick(() => {
    if (scrollContainer.value) {
      scrollContainer.value.scrollTop = scrollContainer.value.scrollHeight;
    }
  });
}

function addEntry(entry: ApiActivityEntry) {
  entries.value.push(entry);
  // Keep max 500 entries
  if (entries.value.length > 500) {
    entries.value = entries.value.slice(-500);
  }
  scrollToBottom();
}

function truncate(text: string, maxLen: number = 120): string {
  if (!text) return '';
  if (text.length <= maxLen) return text;
  return text.substring(0, maxLen) + '…';
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTime(d: Date): string {
  return d.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    fractionalSecondDigits: 3,
  } as Intl.DateTimeFormatOptions);
}

// Expose addEntry for parent components
defineExpose({ addEntry, clearEntries });

const copiedField = ref<string | null>(null);

async function copyContent(entryId: string, field: 'request' | 'response', content: string) {
  try {
    await navigator.clipboard.writeText(content);
    copiedField.value = `${entryId}-${field}`;
    setTimeout(() => { copiedField.value = null; }, 2000);
  } catch (e) {
    console.error('Failed to copy:', e);
  }
}

watch(() => entries.value.length, () => {
  scrollToBottom();
});
</script>

<template>
  <div class="activity-panel">
    <div class="activity-header">
      <h3 class="activity-title">
        <span class="material-icons activity-title-icon">swap_horiz</span>
        API Activity
      </h3>
      <div class="activity-controls">
        <label class="toggle-label" title="Show poll_stream calls">
          <input type="checkbox" v-model="showStreamPolls" />
          <span class="toggle-text">Polls</span>
        </label>
        <label class="toggle-label" title="Auto-scroll to latest">
          <input type="checkbox" v-model="autoScroll" />
          <span class="toggle-text">Auto</span>
        </label>
        <button
          class="icon-btn-sm"
          @click="clearEntries"
          title="Clear activity log"
          :disabled="entries.length === 0"
        >
          <span class="material-icons">delete_sweep</span>
        </button>
      </div>
    </div>

    <div class="activity-filter">
      <input
        v-model="filterText"
        type="text"
        placeholder="Filter by method or content…"
        class="filter-input"
      />
    </div>

    <div class="activity-list" ref="scrollContainer">
      <div v-if="filteredEntries.length === 0" class="activity-empty">
        <span class="material-icons empty-icon">history</span>
        <p>No API calls yet</p>
      </div>

      <div
        v-for="entry in filteredEntries"
        :key="entry.id"
        :class="['activity-entry', { expanded: expandedIds.has(entry.id), error: !entry.success }]"
        @click="toggleExpanded(entry.id)"
      >
        <div class="entry-header">
          <span :class="['entry-status', entry.success ? 'ok' : 'err']">
            {{ entry.success ? '✓' : '✗' }}
          </span>
          <span class="entry-method">{{ entry.method }}</span>
          <span class="entry-duration">{{ formatDuration(entry.durationMs) }}</span>
          <span class="entry-time">{{ formatTime(entry.timestamp) }}</span>
        </div>

        <div v-if="!expandedIds.has(entry.id)" class="entry-preview">
          <span class="preview-label">→</span>
          <span class="preview-text">{{ truncate(entry.requestSummary, 80) }}</span>
        </div>
        <div v-if="!expandedIds.has(entry.id) && entry.responseSummary" class="entry-preview response-preview">
          <span class="preview-label">←</span>
          <span class="preview-text">{{ truncate(entry.responseSummary, 80) }}</span>
        </div>

        <div v-if="expandedIds.has(entry.id)" class="entry-detail" @click.stop>
          <div class="detail-section">
            <div class="detail-header">
              <span class="detail-label">Request (full context):</span>
              <button class="copy-detail-btn" @click="copyContent(entry.id, 'request', entry.requestFull)" :title="copiedField === `${entry.id}-request` ? 'Copied!' : 'Copy request'">
                <span class="material-icons">{{ copiedField === `${entry.id}-request` ? 'check' : 'content_copy' }}</span>
              </button>
            </div>
            <pre class="detail-content">{{ entry.requestFull }}</pre>
          </div>
          <div class="detail-section">
            <div class="detail-header">
              <span class="detail-label">Response (full context):</span>
              <button class="copy-detail-btn" @click="copyContent(entry.id, 'response', entry.responseFull)" :title="copiedField === `${entry.id}-response` ? 'Copied!' : 'Copy response'">
                <span class="material-icons">{{ copiedField === `${entry.id}-response` ? 'check' : 'content_copy' }}</span>
              </button>
            </div>
            <pre class="detail-content">{{ entry.responseFull }}</pre>
          </div>
        </div>
      </div>
    </div>

    <div class="activity-footer">
      <span class="entry-count">{{ filteredEntries.length }} call{{ filteredEntries.length !== 1 ? 's' : '' }}</span>
    </div>
  </div>
</template>

<style scoped>
.activity-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background-color: var(--bg-primary);
  overflow: hidden;
}

.activity-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.activity-title {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.activity-title-icon {
  font-size: 1.125rem;
  color: var(--accent-color);
}

.activity-controls {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.toggle-label {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  cursor: pointer;
  font-size: 0.6875rem;
  color: var(--text-secondary);
}

.toggle-label input[type="checkbox"] {
  width: 12px;
  height: 12px;
  cursor: pointer;
}

.toggle-text {
  font-size: 0.6875rem;
}

.icon-btn-sm {
  padding: 0.1875rem;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
  transition: all 0.15s ease;
}

.icon-btn-sm .material-icons {
  font-size: 1rem;
}

.icon-btn-sm:hover {
  background-color: var(--bg-tertiary);
  color: var(--accent-color);
}

.icon-btn-sm:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.activity-filter {
  padding: 0.375rem 0.75rem;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.filter-input {
  width: 100%;
  padding: 0.3125rem 0.5rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
  background-color: var(--bg-input);
  color: var(--text-primary);
  transition: border-color 0.15s ease;
}

.filter-input:focus {
  outline: none;
  border-color: var(--accent-color);
}

.filter-input::placeholder {
  color: var(--text-secondary);
  opacity: 0.6;
}

.activity-list {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  min-height: 0;
}

.activity-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 2rem 1rem;
  color: var(--text-secondary);
  opacity: 0.5;
}

.empty-icon {
  font-size: 2rem;
  margin-bottom: 0.5rem;
}

.activity-empty p {
  font-size: 0.8125rem;
  margin: 0;
}

.activity-entry {
  padding: 0.375rem 0.75rem;
  border-bottom: 1px solid var(--border-color);
  cursor: pointer;
  transition: background-color 0.1s ease;
}

.activity-entry:hover {
  background-color: var(--bg-tertiary);
}

.activity-entry.error {
  border-left: 3px solid #ef4444;
}

.activity-entry.expanded {
  background-color: var(--bg-secondary);
}

.entry-header {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
}

.entry-status {
  font-size: 0.6875rem;
  font-weight: 700;
  width: 14px;
  text-align: center;
  flex-shrink: 0;
}

.entry-status.ok {
  color: #22c55e;
}

.entry-status.err {
  color: #ef4444;
}

.entry-method {
  font-weight: 600;
  color: var(--text-primary);
  font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
  font-size: 0.6875rem;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entry-duration {
  font-size: 0.625rem;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.entry-time {
  font-size: 0.625rem;
  color: var(--text-secondary);
  opacity: 0.7;
  flex-shrink: 0;
}

.entry-preview {
  display: flex;
  align-items: flex-start;
  gap: 0.25rem;
  margin-top: 0.125rem;
  font-size: 0.6875rem;
  color: var(--text-secondary);
  overflow: hidden;
}

.response-preview {
  opacity: 0.8;
}

.preview-label {
  flex-shrink: 0;
  font-weight: 600;
  color: var(--accent-color);
  opacity: 0.7;
}

.preview-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.entry-detail {
  margin-top: 0.375rem;
  padding: 0.375rem;
  background-color: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
}

.detail-section {
  margin-bottom: 0.375rem;
}

.detail-section:last-child {
  margin-bottom: 0;
}

.detail-label {
  display: block;
  font-size: 0.625rem;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 0.125rem;
}

.detail-content {
  font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
  font-size: 0.625rem;
  color: var(--text-primary);
  background-color: var(--bg-tertiary);
  padding: 0.375rem 0.5rem;
  border-radius: var(--radius-sm);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 600px;
  overflow-y: auto;
  margin: 0;
  line-height: 1.4;
}

.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.125rem;
}

.copy-detail-btn {
  background: none;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  cursor: pointer;
  padding: 0.0625rem 0.25rem;
  display: flex;
  align-items: center;
  font-size: 0.625rem;
  transition: color 0.15s, border-color 0.15s;
}

.copy-detail-btn:hover {
  color: var(--accent-color);
  border-color: var(--accent-color);
}

.copy-detail-btn .material-icons {
  font-size: 0.75rem;
}

.activity-footer {
  padding: 0.25rem 0.75rem;
  border-top: 1px solid var(--border-color);
  flex-shrink: 0;
}

.entry-count {
  font-size: 0.6875rem;
  color: var(--text-secondary);
}

/* Scrollbar styling */
.activity-list::-webkit-scrollbar {
  width: 6px;
}

.activity-list::-webkit-scrollbar-track {
  background: transparent;
}

.activity-list::-webkit-scrollbar-thumb {
  background: var(--border-color);
  border-radius: 3px;
}

.activity-list::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}

.detail-content::-webkit-scrollbar {
  width: 4px;
}

.detail-content::-webkit-scrollbar-track {
  background: transparent;
}

.detail-content::-webkit-scrollbar-thumb {
  background: var(--border-color);
  border-radius: 2px;
}
</style>
