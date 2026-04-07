<script setup lang="ts">
import { ref } from 'vue';
import { marked } from 'marked';
import '../types/pywebview';
import type { ComponentSearchResult, WebSearchResult } from '../types/pywebview';

// Emitted when the user wants to insert a query into the chat box.
const emit = defineEmits<{
  (e: 'insert-in-chat', text: string): void;
}>();

const query = ref('');
const isLoading = ref(false);
const errorMessage = ref('');
const aiResponse = ref('');
const searchResults = ref<WebSearchResult[]>([]);
const lastQuery = ref('');
const showSources = ref(false);
const groundingBackend = ref<'google' | 'duckduckgo' | null>(null);

/**
 * Sanitize HTML produced by marked to prevent XSS.
 * Uses the browser's DOMParser to strip dangerous elements and event attributes.
 * Legitimate markdown output (headings, paragraphs, lists, code blocks) is preserved.
 */
function sanitizeHtml(html: string): string {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  // Remove elements that can execute scripts or load external resources
  doc.querySelectorAll('script, iframe, object, embed, form, base').forEach(el => el.remove());
  // Remove event-handler attributes (on*) and dangerous attribute values from all elements
  doc.querySelectorAll('*').forEach(el => {
    Array.from(el.attributes).forEach(attr => {
      if (attr.name.startsWith('on')) {
        el.removeAttribute(attr.name);
      } else if (attr.name === 'href' || attr.name === 'src' || attr.name === 'action') {
        const val = attr.value.trim().toLowerCase();
        if (
          !val.startsWith('http:') &&
          !val.startsWith('https:') &&
          !val.startsWith('//') &&   // protocol-relative (inherits https: in context)
          !val.startsWith('#')
        ) {
          el.removeAttribute(attr.name);
        }
      }
    });
  });
  return doc.body.innerHTML;
}

function renderMarkdown(text: string): string {
  if (!text) return '';
  const raw = marked.parse(text) as string;
  return sanitizeHtml(raw);
}

/**
 * Validate that a URL from an external search result uses a safe scheme.
 * Returns '#' for any non-http(s) URL to prevent javascript: and data: injections.
 */
function safeUrl(url: string | undefined): string {
  if (!url) return '#';
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return url;
    }
  } catch {
    // Malformed URL
  }
  return '#';
}

async function search() {
  const q = query.value.trim();
  if (!q) return;

  isLoading.value = true;
  errorMessage.value = '';
  aiResponse.value = '';
  searchResults.value = [];
  lastQuery.value = q;
  showSources.value = false;
  groundingBackend.value = null;

  try {
    const api = window.pywebview?.api;
    if (!api) {
      errorMessage.value = 'Backend API not available.';
      return;
    }

    const result: ComponentSearchResult = await api.web_search_components(q);

    if (result.success) {
      aiResponse.value = result.response ?? '';
      searchResults.value = result.search_results ?? [];
      groundingBackend.value = result.grounding ?? null;
    } else {
      errorMessage.value = result.error ?? 'Component search failed.';
    }
  } catch (err) {
    errorMessage.value = `Error: ${err instanceof Error ? err.message : String(err)}`;
  } finally {
    isLoading.value = false;
  }
}

function insertInChat() {
  if (lastQuery.value) {
    emit('insert-in-chat', lastQuery.value);
  }
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    search();
  }
}
</script>

<template>
  <div class="component-search">
    <div class="search-header">
      <span class="material-icons search-icon">search</span>
      <span class="search-title">Component Search</span>
    </div>

    <div class="search-input-row">
      <input
        v-model="query"
        class="search-input"
        type="text"
        placeholder="e.g. logic level converter 3.3V to 5V"
        :disabled="isLoading"
        @keydown="handleKeydown"
      />
      <button
        class="search-btn"
        :disabled="isLoading || !query.trim()"
        @click="search"
        title="Search for components"
      >
        <span v-if="isLoading" class="material-icons spin">sync</span>
        <span v-else class="material-icons">search</span>
      </button>
    </div>

    <!-- Loading state -->
    <div v-if="isLoading" class="search-loading">
      <span class="material-icons spin">sync</span>
      <span>Searching and analyzing…</span>
    </div>

    <!-- Error state -->
    <div v-else-if="errorMessage" class="search-error">
      <span class="material-icons">error_outline</span>
      <span>{{ errorMessage }}</span>
    </div>

    <!-- Results -->
    <div v-else-if="aiResponse" class="search-results">
      <div class="results-toolbar">
        <span class="results-label">Results for: <em>{{ lastQuery }}</em></span>
        <span v-if="groundingBackend === 'google'" class="grounding-badge google" title="Results grounded with Google Search via Gemini API">
          <span class="material-icons badge-icon">public</span>
          Google Search
        </span>
        <span v-else-if="groundingBackend === 'duckduckgo'" class="grounding-badge ddg" title="Results from DuckDuckGo web search">
          <span class="material-icons badge-icon">search</span>
          DuckDuckGo
        </span>
        <button class="chat-btn" @click="insertInChat" title="Ask this in the chat">
          <span class="material-icons">chat</span>
          Ask in Chat
        </button>
      </div>

      <!-- AI-synthesized response -->
      <div class="ai-response markdown-body" v-html="renderMarkdown(aiResponse)"></div>

      <!-- Collapsible web sources -->
      <div v-if="searchResults.length" class="sources-section">
        <button class="sources-toggle" @click="showSources = !showSources">
          <span class="material-icons">{{ showSources ? 'expand_less' : 'expand_more' }}</span>
          {{ showSources ? 'Hide' : 'Show' }} web sources ({{ searchResults.length }})
        </button>
        <ul v-if="showSources" class="sources-list">
          <li v-for="(r, idx) in searchResults" :key="idx" class="source-item">
            <a :href="safeUrl(r.url)" target="_blank" rel="noopener noreferrer" class="source-title">
              {{ r.title }}
            </a>
            <span v-if="r.snippet" class="source-snippet">{{ r.snippet }}</span>
          </li>
        </ul>
      </div>
    </div>

    <!-- Empty state -->
    <div v-else class="search-empty">
      <span class="material-icons empty-icon">electrical_services</span>
      <p>Describe the component you need and get AI-powered recommendations based on live web results.</p>
      <p class="search-example">Example: <em>bidirectional logic level shifter 3.3V to 5V I2C compatible</em></p>
    </div>
  </div>
</template>

<style scoped>
.component-search {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 0.75rem;
  gap: 0.5rem;
  overflow: hidden;
}

.search-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.8rem;
  font-weight: 700;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding-bottom: 0.4rem;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.search-icon {
  font-size: 1rem;
}

.search-title {
  font-size: 0.75rem;
}

.search-input-row {
  display: flex;
  gap: 0.3rem;
  flex-shrink: 0;
}

.search-input {
  flex: 1;
  padding: 0.4rem 0.5rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-input);
  color: var(--text-primary);
  font-size: 0.8rem;
  outline: none;
  transition: border-color 0.15s;
}

.search-input:focus {
  border-color: var(--accent-color);
}

.search-input:disabled {
  opacity: 0.6;
}

.search-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0.4rem 0.5rem;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--accent-color);
  color: #fff;
  cursor: pointer;
  transition: background 0.15s;
  flex-shrink: 0;
}

.search-btn:hover:not(:disabled) {
  background: var(--accent-hover);
}

.search-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.search-btn .material-icons {
  font-size: 1rem;
}

/* Spinner animation */
.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

.search-loading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: var(--text-secondary);
  font-size: 0.8rem;
  padding: 0.5rem;
}

.search-error {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  color: #e05252;
  font-size: 0.8rem;
  padding: 0.5rem;
  background: rgba(224, 82, 82, 0.08);
  border-radius: var(--radius-sm);
}

.search-error .material-icons {
  font-size: 1rem;
  flex-shrink: 0;
  margin-top: 0.05rem;
}

.search-results {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  overflow-y: auto;
  flex: 1;
  min-height: 0;
}

.results-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.results-label {
  font-size: 0.7rem;
  color: var(--text-secondary);
}

.results-label em {
  color: var(--text-primary);
  font-style: normal;
  font-weight: 600;
}

.grounding-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.15rem 0.45rem;
  border-radius: 999px;
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.grounding-badge.google {
  background: rgba(66, 133, 244, 0.12);
  color: #4285f4;
  border: 1px solid rgba(66, 133, 244, 0.3);
}

.grounding-badge.ddg {
  background: rgba(222, 88, 55, 0.1);
  color: #de5837;
  border: 1px solid rgba(222, 88, 55, 0.25);
}

.badge-icon {
  font-size: 0.75rem;
}

.chat-btn {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.25rem 0.5rem;
  border: 1px solid var(--accent-color);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--accent-color);
  font-size: 0.72rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}

.chat-btn:hover {
  background: var(--accent-color);
  color: #fff;
}

.chat-btn .material-icons {
  font-size: 0.9rem;
}

/* Markdown response area */
.ai-response {
  font-size: 0.78rem;
  line-height: 1.55;
  color: var(--text-primary);
  padding: 0.5rem;
  background: var(--bg-secondary);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color);
  overflow-y: auto;
  flex: 1;
  min-height: 0;
}

/* Basic markdown styling */
.ai-response :deep(h1),
.ai-response :deep(h2),
.ai-response :deep(h3) {
  margin: 0.6rem 0 0.3rem;
  color: var(--text-primary);
  font-weight: 700;
}

.ai-response :deep(h1) { font-size: 1rem; }
.ai-response :deep(h2) { font-size: 0.9rem; }
.ai-response :deep(h3) { font-size: 0.85rem; }

.ai-response :deep(p) { margin: 0.3rem 0; }

.ai-response :deep(ul),
.ai-response :deep(ol) {
  padding-left: 1.2rem;
  margin: 0.2rem 0;
}

.ai-response :deep(li) { margin: 0.15rem 0; }

.ai-response :deep(strong) { font-weight: 700; }

.ai-response :deep(code) {
  background: var(--bg-tertiary);
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
  font-family: monospace;
  font-size: 0.85em;
}

.ai-response :deep(pre) {
  background: var(--bg-tertiary);
  padding: 0.5rem;
  border-radius: var(--radius-sm);
  overflow-x: auto;
}

/* Sources section */
.sources-section {
  flex-shrink: 0;
}

.sources-toggle {
  display: flex;
  align-items: center;
  gap: 0.2rem;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 0.72rem;
  padding: 0.2rem 0;
  width: 100%;
  text-align: left;
}

.sources-toggle:hover {
  color: var(--text-primary);
}

.sources-toggle .material-icons {
  font-size: 0.9rem;
}

.sources-list {
  list-style: none;
  padding: 0;
  margin: 0.3rem 0 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.source-item {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}

.source-title {
  font-size: 0.73rem;
  color: var(--accent-color);
  text-decoration: none;
  word-break: break-all;
}

.source-title:hover { text-decoration: underline; }

.source-snippet {
  font-size: 0.68rem;
  color: var(--text-secondary);
  line-height: 1.4;
}

/* Empty state */
.search-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 0.5rem;
  color: var(--text-secondary);
  padding: 1.5rem 0.5rem;
}

.empty-icon {
  font-size: 2.5rem;
  opacity: 0.4;
}

.search-empty p {
  font-size: 0.75rem;
  line-height: 1.5;
  max-width: 200px;
}

.search-example {
  font-size: 0.7rem !important;
  color: var(--text-secondary);
  opacity: 0.8;
}
</style>
