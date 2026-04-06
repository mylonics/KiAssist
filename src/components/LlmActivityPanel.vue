<script setup lang="ts">
import { ref, computed, nextTick, watch, onMounted, onUnmounted } from 'vue';

export interface LLMLogEntry {
  id: string;
  timestamp: number;
  provider: string;
  model: string;
  system_prompt: string;
  messages: Array<{
    role: string;
    content: string;
    tool_calls?: Array<{ id: string; name: string; arguments: Record<string, unknown> }>;
    tool_results?: Array<{ tool_call_id: string; content: string; is_error: boolean }>;
  }>;
  tool_count: number;
  tool_names: string[];
  response_text: string;
  response_tool_calls: Array<{ id: string; name: string; arguments: Record<string, unknown> }>;
  usage: Record<string, number>;
  duration_ms: number;
  is_stream: boolean;
  error: string;
  done: boolean;
}

const entries = ref<LLMLogEntry[]>([]);
const expandedIds = ref<Set<string>>(new Set());
const expandedSections = ref<Set<string>>(new Set());
const scrollContainer = ref<HTMLElement | null>(null);
const autoScroll = ref(true);
const filterText = ref('');
const polling = ref(false);
let pollTimer: ReturnType<typeof setInterval> | null = null;
let lastSeenId: string | null = null;

const filteredEntries = computed(() => {
  let result = entries.value;
  if (filterText.value.trim()) {
    const q = filterText.value.toLowerCase();
    result = result.filter(
      e =>
        e.provider.toLowerCase().includes(q) ||
        e.model.toLowerCase().includes(q) ||
        e.response_text.toLowerCase().includes(q) ||
        e.messages.some(m => m.content.toLowerCase().includes(q))
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
  expandedIds.value = new Set(expandedIds.value);
}

function toggleSection(key: string) {
  if (expandedSections.value.has(key)) {
    expandedSections.value.delete(key);
  } else {
    expandedSections.value.add(key);
  }
  expandedSections.value = new Set(expandedSections.value);
}

function clearEntries() {
  entries.value = [];
  expandedIds.value = new Set();
  expandedSections.value = new Set();
  lastSeenId = null;
  if (window.pywebview?.api) {
    window.pywebview.api.clear_llm_log();
  }
}

function scrollToBottom() {
  if (!autoScroll.value) return;
  nextTick(() => {
    if (scrollContainer.value) {
      scrollContainer.value.scrollTop = scrollContainer.value.scrollHeight;
    }
  });
}

async function fetchLog() {
  if (!window.pywebview?.api) return;
  try {
    // Determine the fetch point: if there are pending (not-done) entries we
    // already have, re-fetch from the entry *before* the first pending one
    // so we receive updates for it.  Otherwise use the normal lastSeenId.
    let fetchSinceId = lastSeenId;
    const firstPending = entries.value.find(e => !e.done);
    if (firstPending) {
      // Find the entry immediately before this pending entry
      const idx = entries.value.indexOf(firstPending);
      fetchSinceId = idx > 0 ? entries.value[idx - 1].id : null;
    }

    const result = await window.pywebview.api.get_llm_log(fetchSinceId);
    if (result.success && result.entries && result.entries.length > 0) {
      for (const entry of result.entries) {
        // Check if we already have this entry (dedup/update)
        const existing = entries.value.find(e => e.id === entry.id);
        if (existing) {
          // Update in place (for streaming entries that get updated)
          Object.assign(existing, entry);
        } else {
          entries.value.push(entry);
        }
      }
      // Only advance lastSeenId to the last *completed* entry so pending
      // ones keep getting refreshed on the next poll.
      const lastDone = [...result.entries].reverse().find(e => e.done);
      if (lastDone) {
        lastSeenId = lastDone.id;
      }
      // Keep max 200 entries
      if (entries.value.length > 200) {
        entries.value = entries.value.slice(-200);
      }
      scrollToBottom();
    }
  } catch (e) {
    // Silently ignore poll errors
  }
}

function startPolling() {
  if (pollTimer) return;
  polling.value = true;
  pollTimer = setInterval(fetchLog, 2000);
  fetchLog(); // Immediate first fetch
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  polling.value = false;
}

onMounted(() => {
  startPolling();
});

onUnmounted(() => {
  stopPolling();
});

function formatTime(timestamp: number): string {
  const d = new Date(timestamp * 1000);
  return d.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatDuration(ms: number): string {
  if (ms <= 0) return '…';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTokens(usage: Record<string, number>): string {
  if (!usage || Object.keys(usage).length === 0) return '';
  const parts: string[] = [];
  if (usage.input_tokens) parts.push(`↑${usage.input_tokens}`);
  if (usage.output_tokens) parts.push(`↓${usage.output_tokens}`);
  return parts.join(' ');
}

function truncate(text: string, maxLen: number = 120): string {
  if (!text) return '';
  if (text.length <= maxLen) return text;
  return text.substring(0, maxLen) + '…';
}

function formatMessages(messages: LLMLogEntry['messages']): string {
  return messages
    .map(m => {
      let line = `[${m.role}] ${m.content || '(empty)'}`;
      if (m.tool_calls?.length) {
        line += `\n  → Tool calls: ${m.tool_calls.map(tc => tc.name).join(', ')}`;
      }
      if (m.tool_results?.length) {
        line += `\n  ← Tool results: ${m.tool_results.length} result(s)`;
      }
      return line;
    })
    .join('\n\n');
}

function messagesSummary(messages: LLMLogEntry['messages']): string {
  const counts: Record<string, number> = {};
  for (const m of messages) {
    counts[m.role] = (counts[m.role] || 0) + 1;
  }
  return Object.entries(counts).map(([role, n]) => `${n} ${role}`).join(', ');
}

const copiedField = ref<string | null>(null);

async function copyContent(entryId: string, field: string, content: string) {
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
  <div class="llm-panel">
    <div class="llm-header">
      <h3 class="llm-title">
        <span class="material-icons llm-title-icon">psychology</span>
        LLM Activity
      </h3>
      <div class="llm-controls">
        <label class="toggle-label" title="Auto-scroll to latest">
          <input type="checkbox" v-model="autoScroll" />
          <span class="toggle-text">Auto</span>
        </label>
        <button
          class="icon-btn-sm"
          @click="fetchLog"
          title="Refresh now"
        >
          <span class="material-icons">refresh</span>
        </button>
        <button
          class="icon-btn-sm"
          @click="clearEntries"
          title="Clear LLM log"
          :disabled="entries.length === 0"
        >
          <span class="material-icons">delete_sweep</span>
        </button>
      </div>
    </div>

    <div class="llm-filter">
      <input
        v-model="filterText"
        type="text"
        placeholder="Filter by provider, model, or content…"
        class="filter-input"
      />
    </div>

    <div class="llm-list" ref="scrollContainer">
      <div v-if="filteredEntries.length === 0" class="llm-empty">
        <span class="material-icons empty-icon">smart_toy</span>
        <p>No LLM calls yet</p>
        <p class="empty-hint">Send a message to see backend ↔ LLM interactions</p>
      </div>

      <div
        v-for="entry in filteredEntries"
        :key="entry.id"
        :class="['llm-entry', { expanded: expandedIds.has(entry.id), error: !!entry.error, pending: !entry.done }]"
        @click="toggleExpanded(entry.id)"
      >
        <div class="entry-header">
          <span :class="['entry-status', entry.error ? 'err' : entry.done ? 'ok' : 'pending']">
            {{ entry.error ? '✗' : entry.done ? '✓' : '⟳' }}
          </span>
          <span class="entry-provider">{{ entry.provider }}</span>
          <span class="entry-model">{{ entry.model }}</span>
          <span v-if="entry.is_stream" class="entry-badge stream">stream</span>
          <span class="entry-duration">{{ formatDuration(entry.duration_ms) }}</span>
          <span v-if="formatTokens(entry.usage)" class="entry-tokens">{{ formatTokens(entry.usage) }}</span>
          <span class="entry-time">{{ formatTime(entry.timestamp) }}</span>
        </div>

        <!-- Collapsed preview -->
        <div v-if="!expandedIds.has(entry.id)" class="entry-preview">
          <span class="preview-label">msgs</span>
          <span class="preview-text">{{ messagesSummary(entry.messages) }}</span>
        </div>
        <div v-if="!expandedIds.has(entry.id) && entry.response_text" class="entry-preview response-preview">
          <span class="preview-label">←</span>
          <span class="preview-text">{{ truncate(entry.response_text, 100) }}</span>
        </div>
        <div v-if="!expandedIds.has(entry.id) && entry.error" class="entry-preview error-preview">
          <span class="preview-label">err</span>
          <span class="preview-text">{{ truncate(entry.error, 100) }}</span>
        </div>

        <!-- Expanded detail -->
        <div v-if="expandedIds.has(entry.id)" class="entry-detail" @click.stop>
          <!-- System Prompt -->
          <div v-if="entry.system_prompt" class="detail-section">
            <div class="detail-header clickable" @click="toggleSection(`${entry.id}-sys`)">
              <span class="detail-label">
                <span class="material-icons section-icon">{{ expandedSections.has(`${entry.id}-sys`) ? 'expand_more' : 'chevron_right' }}</span>
                System Prompt ({{ entry.system_prompt.length }} chars)
              </span>
              <button class="copy-detail-btn" @click.stop="copyContent(entry.id, 'system', entry.system_prompt)" :title="copiedField === `${entry.id}-system` ? 'Copied!' : 'Copy'">
                <span class="material-icons">{{ copiedField === `${entry.id}-system` ? 'check' : 'content_copy' }}</span>
              </button>
            </div>
            <pre v-if="expandedSections.has(`${entry.id}-sys`)" class="detail-content system-content">{{ entry.system_prompt }}</pre>
          </div>

          <!-- Messages -->
          <div class="detail-section">
            <div class="detail-header clickable" @click="toggleSection(`${entry.id}-msgs`)">
              <span class="detail-label">
                <span class="material-icons section-icon">{{ expandedSections.has(`${entry.id}-msgs`) ? 'expand_more' : 'chevron_right' }}</span>
                Messages ({{ entry.messages.length }})
              </span>
              <button class="copy-detail-btn" @click.stop="copyContent(entry.id, 'messages', formatMessages(entry.messages))" :title="copiedField === `${entry.id}-messages` ? 'Copied!' : 'Copy'">
                <span class="material-icons">{{ copiedField === `${entry.id}-messages` ? 'check' : 'content_copy' }}</span>
              </button>
            </div>
            <div v-if="expandedSections.has(`${entry.id}-msgs`)" class="messages-list">
              <div v-for="(msg, mi) in entry.messages" :key="mi" :class="['msg-item', `msg-${msg.role}`]">
                <span class="msg-role">{{ msg.role }}</span>
                <pre class="msg-content">{{ msg.content || '(empty)' }}</pre>
                <div v-if="msg.tool_calls?.length" class="msg-tools">
                  <div v-for="tc in msg.tool_calls" :key="tc.id" class="tool-call-item">
                    <span class="tool-icon">→</span>
                    <span class="tool-name">{{ tc.name }}</span>
                    <pre class="tool-args">{{ JSON.stringify(tc.arguments, null, 2) }}</pre>
                  </div>
                </div>
                <div v-if="msg.tool_results?.length" class="msg-tools">
                  <div v-for="tr in msg.tool_results" :key="tr.tool_call_id" :class="['tool-result-item', { 'tool-error': tr.is_error }]">
                    <span class="tool-icon">←</span>
                    <pre class="tool-result-content">{{ tr.content }}</pre>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Tool Names (if any) -->
          <div v-if="entry.tool_names.length > 0" class="detail-section">
            <div class="detail-header clickable" @click="toggleSection(`${entry.id}-tools`)">
              <span class="detail-label">
                <span class="material-icons section-icon">{{ expandedSections.has(`${entry.id}-tools`) ? 'expand_more' : 'chevron_right' }}</span>
                Available Tools ({{ entry.tool_count }})
              </span>
            </div>
            <div v-if="expandedSections.has(`${entry.id}-tools`)" class="tool-names-list">
              <span v-for="name in entry.tool_names" :key="name" class="tool-name-badge">{{ name }}</span>
            </div>
          </div>

          <!-- Response -->
          <div class="detail-section">
            <div class="detail-header">
              <span class="detail-label">
                <span class="material-icons section-icon">chat_bubble</span>
                Response{{ entry.is_stream ? ' (streamed)' : '' }}
              </span>
              <button v-if="entry.response_text" class="copy-detail-btn" @click.stop="copyContent(entry.id, 'response', entry.response_text)" :title="copiedField === `${entry.id}-response` ? 'Copied!' : 'Copy'">
                <span class="material-icons">{{ copiedField === `${entry.id}-response` ? 'check' : 'content_copy' }}</span>
              </button>
            </div>
            <pre v-if="entry.response_text" class="detail-content response-content">{{ entry.response_text }}</pre>
            <div v-else-if="!entry.done" class="pending-indicator">
              <span class="material-icons spinning">sync</span> Waiting for response…
            </div>
            <pre v-else class="detail-content empty-response">(no response)</pre>
          </div>

          <!-- Response Tool Calls -->
          <div v-if="entry.response_tool_calls.length > 0" class="detail-section">
            <div class="detail-header">
              <span class="detail-label">
                <span class="material-icons section-icon">build</span>
                Response Tool Calls ({{ entry.response_tool_calls.length }})
              </span>
            </div>
            <div class="msg-tools">
              <div v-for="tc in entry.response_tool_calls" :key="tc.id" class="tool-call-item">
                <span class="tool-name">{{ tc.name }}</span>
                <pre class="tool-args">{{ JSON.stringify(tc.arguments, null, 2) }}</pre>
              </div>
            </div>
          </div>

          <!-- Error -->
          <div v-if="entry.error" class="detail-section">
            <div class="detail-header">
              <span class="detail-label error-label">
                <span class="material-icons section-icon">error</span>
                Error
              </span>
            </div>
            <pre class="detail-content error-content">{{ entry.error }}</pre>
          </div>

          <!-- Usage / Meta -->
          <div class="detail-meta">
            <span v-if="entry.duration_ms > 0" class="meta-item">
              <span class="material-icons meta-icon">timer</span>
              {{ formatDuration(entry.duration_ms) }}
            </span>
            <span v-if="entry.usage.input_tokens" class="meta-item">
              <span class="material-icons meta-icon">arrow_upward</span>
              {{ entry.usage.input_tokens }} in
            </span>
            <span v-if="entry.usage.output_tokens" class="meta-item">
              <span class="material-icons meta-icon">arrow_downward</span>
              {{ entry.usage.output_tokens }} out
            </span>
          </div>
        </div>
      </div>
    </div>

    <div class="llm-footer">
      <span class="entry-count">{{ filteredEntries.length }} call{{ filteredEntries.length !== 1 ? 's' : '' }}</span>
      <span v-if="polling" class="polling-indicator" title="Polling every 2s">
        <span class="material-icons polling-icon">radio_button_checked</span>
        live
      </span>
    </div>
  </div>
</template>

<style scoped>
.llm-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background-color: var(--bg-primary);
  overflow: hidden;
}

.llm-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.llm-title {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.llm-title-icon {
  font-size: 1.125rem;
  color: #9b59b6;
}

.llm-controls {
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
  color: #9b59b6;
}

.icon-btn-sm:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.llm-filter {
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
  border-color: #9b59b6;
}

.filter-input::placeholder {
  color: var(--text-secondary);
  opacity: 0.6;
}

.llm-list {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  min-height: 0;
}

.llm-empty {
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

.llm-empty p {
  font-size: 0.8125rem;
  margin: 0;
}

.empty-hint {
  font-size: 0.6875rem !important;
  margin-top: 0.25rem !important;
}

.llm-entry {
  padding: 0.375rem 0.75rem;
  border-bottom: 1px solid var(--border-color);
  cursor: pointer;
  transition: background-color 0.1s ease;
}

.llm-entry:hover {
  background-color: var(--bg-tertiary);
}

.llm-entry.error {
  border-left: 3px solid #ef4444;
}

.llm-entry.pending {
  border-left: 3px solid #f59e0b;
}

.llm-entry.expanded {
  background-color: var(--bg-secondary);
}

.entry-header {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  flex-wrap: wrap;
}

.entry-status {
  font-size: 0.6875rem;
  font-weight: 700;
  width: 14px;
  text-align: center;
  flex-shrink: 0;
}

.entry-status.ok { color: #22c55e; }
.entry-status.err { color: #ef4444; }
.entry-status.pending { color: #f59e0b; }

.entry-provider {
  font-weight: 600;
  color: #9b59b6;
  font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
  font-size: 0.6875rem;
}

.entry-model {
  font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
  font-size: 0.6875rem;
  color: var(--text-secondary);
}

.entry-badge {
  font-size: 0.5625rem;
  padding: 0.0625rem 0.25rem;
  border-radius: 3px;
  font-weight: 600;
  text-transform: uppercase;
}

.entry-badge.stream {
  background-color: rgba(155, 89, 182, 0.15);
  color: #9b59b6;
}

.entry-duration {
  font-size: 0.625rem;
  color: var(--text-secondary);
  flex-shrink: 0;
  margin-left: auto;
}

.entry-tokens {
  font-size: 0.625rem;
  color: var(--text-secondary);
  font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
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

.response-preview { opacity: 0.8; }
.error-preview .preview-text { color: #ef4444; }

.preview-label {
  flex-shrink: 0;
  font-weight: 600;
  color: #9b59b6;
  opacity: 0.7;
  font-size: 0.625rem;
}

.preview-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

/* Detail sections */
.entry-detail {
  margin-top: 0.375rem;
  padding: 0.375rem;
  background-color: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
}

.detail-section {
  margin-bottom: 0.5rem;
}

.detail-section:last-child {
  margin-bottom: 0;
}

.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.125rem;
}

.detail-header.clickable {
  cursor: pointer;
  border-radius: var(--radius-sm);
  padding: 0.125rem 0.25rem;
  margin: -0.125rem -0.25rem 0.125rem -0.25rem;
}

.detail-header.clickable:hover {
  background-color: var(--bg-tertiary);
}

.detail-label {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.625rem;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.error-label {
  color: #ef4444;
}

.section-icon {
  font-size: 0.875rem;
  color: var(--text-secondary);
}

.detail-content {
  font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
  font-size: 0.625rem;
  color: var(--text-primary);
  background-color: var(--bg-tertiary);
  padding: 0.375rem 0.5rem;
  border-radius: var(--radius-sm);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 400px;
  overflow-y: auto;
  margin: 0;
  line-height: 1.4;
}

.system-content {
  border-left: 2px solid #9b59b6;
}

.response-content {
  border-left: 2px solid #22c55e;
}

.error-content {
  border-left: 2px solid #ef4444;
  color: #ef4444;
}

.empty-response {
  opacity: 0.4;
  font-style: italic;
}

/* Messages list */
.messages-list {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.msg-item {
  padding: 0.25rem 0.375rem;
  border-radius: var(--radius-sm);
  background-color: var(--bg-tertiary);
  border-left: 2px solid var(--border-color);
}

.msg-user { border-left-color: var(--accent-color); }
.msg-assistant { border-left-color: #22c55e; }
.msg-system { border-left-color: #9b59b6; }
.msg-tool { border-left-color: #f59e0b; }

.msg-role {
  font-size: 0.5625rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-secondary);
  display: block;
  margin-bottom: 0.125rem;
}

.msg-content {
  font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
  font-size: 0.625rem;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  max-height: 200px;
  overflow-y: auto;
  line-height: 1.4;
}

.msg-tools {
  margin-top: 0.25rem;
}

.tool-call-item, .tool-result-item {
  padding: 0.125rem 0.25rem;
  background-color: var(--bg-secondary);
  border-radius: var(--radius-sm);
  margin-top: 0.125rem;
}

.tool-error {
  border-left: 2px solid #ef4444;
}

.tool-icon {
  font-weight: 700;
  color: #f59e0b;
  font-size: 0.625rem;
}

.tool-name {
  font-weight: 600;
  font-size: 0.625rem;
  color: #f59e0b;
  font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
}

.tool-args, .tool-result-content {
  font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
  font-size: 0.5625rem;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0.125rem 0 0 0;
  max-height: 150px;
  overflow-y: auto;
  line-height: 1.3;
}

.tool-names-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  padding: 0.25rem;
}

.tool-name-badge {
  font-size: 0.5625rem;
  padding: 0.0625rem 0.375rem;
  border-radius: 3px;
  background-color: rgba(245, 158, 11, 0.12);
  color: #f59e0b;
  font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
  font-weight: 500;
}

/* Meta row */
.detail-meta {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding-top: 0.375rem;
  border-top: 1px solid var(--border-color);
  margin-top: 0.375rem;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 0.125rem;
  font-size: 0.625rem;
  color: var(--text-secondary);
}

.meta-icon {
  font-size: 0.75rem;
}

/* Pending indicator */
.pending-indicator {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.6875rem;
  color: #f59e0b;
  padding: 0.25rem;
}

.spinning {
  animation: spin 1s linear infinite;
  font-size: 0.875rem;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Copy button */
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
  color: #9b59b6;
  border-color: #9b59b6;
}

.copy-detail-btn .material-icons {
  font-size: 0.75rem;
}

/* Footer */
.llm-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.25rem 0.75rem;
  border-top: 1px solid var(--border-color);
  flex-shrink: 0;
}

.entry-count {
  font-size: 0.6875rem;
  color: var(--text-secondary);
}

.polling-indicator {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.5625rem;
  color: #22c55e;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.polling-icon {
  font-size: 0.625rem;
  animation: pulse 2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

/* Scrollbar styling */
.llm-list::-webkit-scrollbar {
  width: 6px;
}

.llm-list::-webkit-scrollbar-track {
  background: transparent;
}

.llm-list::-webkit-scrollbar-thumb {
  background: var(--border-color);
  border-radius: 3px;
}

.llm-list::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}

.detail-content::-webkit-scrollbar,
.msg-content::-webkit-scrollbar,
.tool-args::-webkit-scrollbar,
.tool-result-content::-webkit-scrollbar {
  width: 4px;
}

.detail-content::-webkit-scrollbar-track,
.msg-content::-webkit-scrollbar-track,
.tool-args::-webkit-scrollbar-track,
.tool-result-content::-webkit-scrollbar-track {
  background: transparent;
}

.detail-content::-webkit-scrollbar-thumb,
.msg-content::-webkit-scrollbar-thumb,
.tool-args::-webkit-scrollbar-thumb,
.tool-result-content::-webkit-scrollbar-thumb {
  background: var(--border-color);
  border-radius: 2px;
}
</style>
