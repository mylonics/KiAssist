<script setup lang="ts">
import { ref, computed, onMounted, nextTick, watch } from 'vue';
import { marked } from 'marked';
import hljs from 'highlight.js';
import '../types/pywebview';
import type { ProviderInfo, SessionInfo } from '../types/pywebview';

// Configure marked with code highlighting
marked.use({
  renderer: {
    code({ text, lang }: { text: string; lang?: string }): string {
      const validLang = lang && hljs.getLanguage(lang) ? lang : undefined;
      const highlighted = validLang
        ? hljs.highlight(text, { language: validLang }).value
        : hljs.highlightAuto(text).value;
      return `<pre><code class="hljs${validLang ? ` language-${validLang}` : ''}">${highlighted}</code></pre>`;
    }
  },
  breaks: true,
});

interface Message {
  id: string;
  text: string;
  sender: 'user' | 'assistant';
  timestamp: Date;
  isStreaming?: boolean;
}

const STORAGE_KEY = 'kiassist-chat-messages';
const PROVIDER_KEY = 'kiassist-provider';
const MODEL_KEY = 'kiassist-model';

const copiedMessageId = ref<string | null>(null);
const messages = ref<Message[]>([]);
const inputMessage = ref('');
const selectedProvider = ref('gemini');
const selectedModel = ref('3-flash');
const hasApiKey = ref(false);
const showApiKeyPrompt = ref(false);
const showSessionsModal = ref(false);
const apiKeyInput = ref('');
// Which provider's key is being configured in the settings modal
const configuringProvider = ref('gemini');
const isLoading = ref(false);
const apiKeyWarning = ref<string>('');
const apiKeyError = ref<string>('');
const messagesContainer = ref<HTMLElement | null>(null);
const editingMessageId = ref<string | null>(null);
const editingText = ref('');

// Provider list is populated from the backend via get_providers()
const providers = ref<ProviderInfo[]>([]);

const sessions = ref<SessionInfo[]>([]);

const currentProviderInfo = computed<ProviderInfo | undefined>(() =>
  providers.value.find(p => p.id === selectedProvider.value)
);

const configuringProviderInfo = computed<ProviderInfo | undefined>(() =>
  providers.value.find(p => p.id === configuringProvider.value)
);

const availableModels = computed(() =>
  currentProviderInfo.value?.models ?? []
);

function generateMessageId(): string {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

function renderMarkdown(text: string, isUser: boolean = false): string {
  if (!text) return '';
  if (isUser) {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\n/g, '<br>');
  }
  return marked.parse(text) as string;
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
    }
  });
}

// Persistence
function saveMessages() {
  try {
    const serializable = messages.value
      .filter(m => !m.isStreaming)
      .map(m => ({
        id: m.id,
        text: m.text,
        sender: m.sender,
        timestamp: m.timestamp.toISOString(),
      }));
    localStorage.setItem(STORAGE_KEY, JSON.stringify(serializable));
  } catch (e) {
    console.error('Failed to save messages:', e);
  }
}

function loadMessages() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      messages.value = parsed.map((m: any) => ({
        ...m,
        timestamp: new Date(m.timestamp),
        isStreaming: false,
      }));
    }
  } catch (e) {
    console.error('Failed to load messages:', e);
  }
}

function clearMessages() {
  if (isLoading.value) return;
  messages.value = [];
  localStorage.removeItem(STORAGE_KEY);
}

// Provider switching
async function onProviderChange() {
  const info = currentProviderInfo.value;
  if (info) {
    // Switch to the default model for the new provider
    selectedModel.value = info.default_model;
  }
  localStorage.setItem(PROVIDER_KEY, selectedProvider.value);
  localStorage.setItem(MODEL_KEY, selectedModel.value);

  if (window.pywebview?.api) {
    await window.pywebview.api.set_provider(selectedProvider.value, selectedModel.value);
  }
  // Derive hasApiKey from the in-memory providers list (no extra IPC call)
  hasApiKey.value = currentProviderInfo.value?.has_key ?? false;
}

async function onModelChange() {
  localStorage.setItem(MODEL_KEY, selectedModel.value);
  if (window.pywebview?.api) {
    await window.pywebview.api.set_provider(selectedProvider.value, selectedModel.value);
  }
}

// Load provider state from backend; localStorage is the source of truth for
// provider/model selection — backend defaults are only used when no saved
// preference exists.
async function loadProviders() {
  if (!window.pywebview?.api) return;
  try {
    const result = await window.pywebview.api.get_providers();
    if (result.success && result.providers) {
      // Backend is the single source of truth for provider metadata
      providers.value = result.providers;

      // Determine which provider/model to use, honouring localStorage first
      const storedProvider = localStorage.getItem(PROVIDER_KEY);
      const storedModel = localStorage.getItem(MODEL_KEY);

      const storedProviderInfo = storedProvider
        ? result.providers.find((p: ProviderInfo) => p.id === storedProvider)
        : undefined;

      const nextProvider =
        storedProviderInfo?.id ??
        result.current_provider ??
        selectedProvider.value;

      const nextProviderInfo =
        result.providers.find((p: ProviderInfo) => p.id === nextProvider);

      const nextModel =
        storedModel ??
        result.current_model ??
        nextProviderInfo?.default_model ??
        selectedModel.value;

      selectedProvider.value = nextProvider;
      selectedModel.value = nextModel ?? selectedModel.value;

      // Persist the resolved selection and sync the backend
      localStorage.setItem(PROVIDER_KEY, selectedProvider.value);
      localStorage.setItem(MODEL_KEY, selectedModel.value);
      await window.pywebview.api.set_provider(selectedProvider.value, selectedModel.value);

      // Derive hasApiKey from the providers list — no extra round-trips needed
      const active = result.providers.find((p: ProviderInfo) => p.id === selectedProvider.value);
      hasApiKey.value = active?.has_key ?? false;
    }
  } catch (e) {
    console.error('[UI] Failed to load providers:', e);
  }
}

async function waitForPywebviewAndCheckApiKey() {
  if (!window.pywebview?.api) {
    await new Promise<void>((resolve) => {
      const onReady = () => resolve();
      window.addEventListener('pywebviewready', onReady, { once: true });
      // Fallback timeout in case the event was already fired
      setTimeout(() => {
        window.removeEventListener('pywebviewready', onReady);
        resolve();
      }, 3000);
    });
  }
  try {
    // loadProviders handles provider/model sync and derives hasApiKey
    await loadProviders();
  } catch (err) {
    // Ignore stale callback errors from pywebview (e.g. during HMR reload)
    if (String(err).includes('_returnValuesCallbacks')) {
      console.warn('[ChatBox] Stale pywebview callback (likely HMR reload), ignoring.');
    } else {
      console.error('[UI] Error during initial provider load:', err);
    }
  }
}

function openSettings(provider?: string) {
  configuringProvider.value = provider || selectedProvider.value;
  apiKeyInput.value = '';
  apiKeyError.value = '';
  apiKeyWarning.value = '';
  showApiKeyPrompt.value = true;
}

function selectProviderForConfig(providerId: string) {
  configuringProvider.value = providerId;
  apiKeyInput.value = '';
  apiKeyError.value = '';
  apiKeyWarning.value = '';
}

function validateApiKeyFormat(key: string, providerInfo: ProviderInfo): string | null {
  if (key.length < providerInfo.key_min_length) {
    return `API key seems too short (minimum ${providerInfo.key_min_length} characters).`;
  }
  if (providerInfo.key_prefix && !key.startsWith(providerInfo.key_prefix)) {
    return `This doesn't look like a valid ${providerInfo.name} API key. Keys should start with "${providerInfo.key_prefix}". Get your key from ${providerInfo.key_url}`;
  }
  return null;
}

async function saveApiKey() {
  if (!apiKeyInput.value.trim()) return;
  apiKeyError.value = '';
  apiKeyWarning.value = '';
  const trimmedKey = apiKeyInput.value.trim();
  const info = configuringProviderInfo.value;

  if (info) {
    const validationError = validateApiKeyFormat(trimmedKey, info);
    if (validationError) {
      apiKeyError.value = validationError;
      return;
    }
  }

  try {
    if (window.pywebview?.api) {
      const result = await window.pywebview.api.set_api_key(trimmedKey, configuringProvider.value);
      if (result.success) {
        // Update local has_key status
        const localProvider = providers.value.find(p => p.id === configuringProvider.value);
        if (localProvider) localProvider.has_key = true;

        // If we just configured the active provider, mark as having key
        if (configuringProvider.value === selectedProvider.value) {
          hasApiKey.value = true;
        }
        showApiKeyPrompt.value = false;
        apiKeyInput.value = '';
        if (result.warning) {
          apiKeyWarning.value = result.warning;
          messages.value.push({
            id: generateMessageId(),
            text: `Note: ${result.warning}`,
            sender: 'assistant',
            timestamp: new Date(),
          });
        }
      } else {
        apiKeyError.value = result.error || 'Unknown error occurred';
      }
    } else {
      apiKeyError.value = 'Application backend not available. Please restart the application.';
    }
  } catch (error) {
    apiKeyError.value = 'Failed to save API key. Please try again.';
  }
}

// Streaming send
async function sendMessageWithText(messageText: string) {
  if (!messageText.trim()) return;
  if (!hasApiKey.value) {
    openSettings();
    return;
  }

  isLoading.value = true;

  try {
    if (window.pywebview?.api) {
      const startResult = await window.pywebview.api.start_stream_message(messageText, selectedModel.value);

      if (!startResult.success) {
        messages.value.push({
          id: generateMessageId(),
          text: `Sorry, I encountered an error: ${startResult.error || 'Unknown error'}. Please check your API key and try again.`,
          sender: 'assistant',
          timestamp: new Date(),
        });
        isLoading.value = false;
        return;
      }

      // Create streaming assistant message
      messages.value.push({
        id: generateMessageId(),
        text: '',
        sender: 'assistant',
        timestamp: new Date(),
        isStreaming: true,
      });
      const streamIdx = messages.value.length - 1;

      // Poll for streaming chunks
      const pollInterval = setInterval(async () => {
        try {
          const poll = await window.pywebview!.api.poll_stream();
          if (poll.success && messages.value[streamIdx]) {
            messages.value[streamIdx].text = poll.text || '';

            if (poll.done) {
              clearInterval(pollInterval);
              messages.value[streamIdx].isStreaming = false;
              isLoading.value = false;

              if (poll.error) {
                messages.value[streamIdx].text = `Sorry, I encountered an error: ${poll.error}`;
              } else if (!messages.value[streamIdx].text.trim()) {
                messages.value[streamIdx].text = 'No response received.';
              }
              saveMessages();
            }
          }
        } catch (e) {
          clearInterval(pollInterval);
          if (messages.value[streamIdx]) {
            messages.value[streamIdx].isStreaming = false;
            messages.value[streamIdx].text = `Sorry, I encountered an error: ${e}`;
          }
          isLoading.value = false;
        }
      }, 100);

    } else {
      messages.value.push({
        id: generateMessageId(),
        text: 'Application backend not available. Please restart the application.',
        sender: 'assistant',
        timestamp: new Date(),
      });
      isLoading.value = false;
    }
  } catch (error) {
    messages.value.push({
      id: generateMessageId(),
      text: `Sorry, I encountered an error: ${error}. Please check your API key and try again.`,
      sender: 'assistant',
      timestamp: new Date(),
    });
    isLoading.value = false;
  }
}

async function sendMessage() {
  if (!inputMessage.value.trim()) return;
  const userMessage: Message = {
    id: generateMessageId(),
    text: inputMessage.value,
    sender: 'user',
    timestamp: new Date(),
  };
  messages.value.push(userMessage);
  const messageText = inputMessage.value;
  inputMessage.value = '';
  await sendMessageWithText(messageText);
}

// Message actions
async function regenerateMessage(messageIndex: number) {
  const msg = messages.value[messageIndex];
  if (!msg || msg.sender !== 'assistant' || isLoading.value) return;
  let userText = '';
  for (let i = messageIndex - 1; i >= 0; i--) {
    if (messages.value[i].sender === 'user') {
      userText = messages.value[i].text;
      break;
    }
  }
  if (!userText) return;
  messages.value.splice(messageIndex, 1);
  await sendMessageWithText(userText);
}

function startEdit(messageId: string, text: string) {
  editingMessageId.value = messageId;
  editingText.value = text;
}

function cancelEdit() {
  editingMessageId.value = null;
  editingText.value = '';
}

async function saveEdit(messageIndex: number) {
  const msg = messages.value[messageIndex];
  if (!msg || msg.sender !== 'user') return;
  const newText = editingText.value.trim();
  if (!newText) return;
  msg.text = newText;
  messages.value.splice(messageIndex + 1);
  cancelEdit();
  await sendMessageWithText(newText);
}

function handleKeyPress(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
}

async function copyMessage(messageId: string, text: string) {
  try {
    await navigator.clipboard.writeText(text);
    copiedMessageId.value = messageId;
    setTimeout(() => { copiedMessageId.value = null; }, 2000);
  } catch (error) {
    console.error('Failed to copy message:', error);
  }
}

// Session management
async function openSessionsModal() {
  if (window.pywebview?.api) {
    try {
      const result = await window.pywebview.api.get_sessions();
      if (result.success) {
        sessions.value = result.sessions ?? [];
      }
    } catch (e) {
      console.error('[UI] Failed to load sessions:', e);
    }
  }
  showSessionsModal.value = true;
}

async function resumeSession(sessionId: string) {
  if (!window.pywebview?.api) return;
  try {
    const result = await window.pywebview.api.resume_session(sessionId);
    if (result.success && result.messages) {
      messages.value = result.messages.map((m: any) => ({
        id: generateMessageId(),
        text: m.content,
        sender: m.role === 'user' ? 'user' : 'assistant',
        timestamp: new Date(),
        isStreaming: false,
      }));
      saveMessages();
    }
  } catch (e) {
    console.error('[UI] Failed to resume session:', e);
  }
  showSessionsModal.value = false;
}

async function exportSession(sessionId: string) {
  if (!window.pywebview?.api) return;
  try {
    const result = await window.pywebview.api.export_session(sessionId);
    if (result.success && result.content) {
      const blob = new Blob([result.content], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `session-${sessionId.substring(0, 8)}.txt`;
      a.click();
      URL.revokeObjectURL(url);
    }
  } catch (e) {
    console.error('[UI] Failed to export session:', e);
  }
}

function formatSessionDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// Watchers
watch(() => messages.value.length, () => { scrollToBottom(); });
watch(() => {
  const last = messages.value[messages.value.length - 1];
  return last?.text?.length ?? 0;
}, () => { scrollToBottom(); });
watch(messages, () => {
  if (!messages.value.some(m => m.isStreaming)) {
    saveMessages();
  }
}, { deep: true });

onMounted(() => {
  loadMessages();
  waitForPywebviewAndCheckApiKey();
  scrollToBottom();
});
</script>

<template>
  <div class="chat-container">
    <!-- API Key Prompt Modal -->
    <div v-if="showApiKeyPrompt" class="modal-overlay">
      <div class="modal-content">
        <h3>Configure API Key</h3>

        <!-- Provider selector inside settings modal -->
        <div class="modal-provider-selector">
          <label class="modal-label">Provider:</label>
          <div class="provider-tabs">
            <button
              v-for="p in providers"
              :key="p.id"
              :class="['provider-tab', { active: configuringProvider === p.id, 'has-key': p.has_key }]"
              @click="selectProviderForConfig(p.id)"
            >
              {{ p.name }}
              <span v-if="p.has_key" class="key-indicator" title="Key configured">✓</span>
            </button>
          </div>
        </div>

        <p class="modal-description" v-if="configuringProviderInfo">
          Enter your {{ configuringProviderInfo.name }} API key.
          Get your key from
          <a :href="configuringProviderInfo.key_url" target="_blank">{{ configuringProviderInfo.key_url }}</a>.
        </p>
        <input
          v-model="apiKeyInput"
          type="password"
          :placeholder="`Enter your ${configuringProviderInfo?.name ?? ''} API key...`"
          class="api-key-input"
          @keypress.enter="saveApiKey"
        />
        <div v-if="apiKeyError" class="error-banner">
          <span class="material-icons error-icon">warning</span>
          <span>{{ apiKeyError }}</span>
        </div>
        <div class="modal-actions">
          <button @click="saveApiKey" class="btn-primary" :disabled="!apiKeyInput.trim()">
            Save API Key
          </button>
          <button @click="showApiKeyPrompt = false" class="btn-secondary">
            Cancel
          </button>
        </div>
      </div>
    </div>

    <!-- Sessions Modal -->
    <div v-if="showSessionsModal" class="modal-overlay">
      <div class="modal-content modal-wide">
        <h3>Conversation Sessions</h3>
        <div v-if="sessions.length === 0" class="sessions-empty">
          No saved sessions found.
        </div>
        <div v-else class="sessions-list">
          <div
            v-for="session in sessions"
            :key="session.session_id"
            class="session-item"
          >
            <div class="session-info">
              <span class="session-id">{{ session.session_id.substring(0, 8) }}…</span>
              <span class="session-date">{{ formatSessionDate(session.started_at) }}</span>
              <span class="session-count">{{ session.message_count }} messages</span>
            </div>
            <div class="session-actions">
              <button @click="resumeSession(session.session_id)" class="btn-primary btn-sm">
                Resume
              </button>
              <button @click="exportSession(session.session_id)" class="btn-secondary btn-sm">
                Export
              </button>
            </div>
          </div>
        </div>
        <div class="modal-actions">
          <button @click="showSessionsModal = false" class="btn-secondary">Close</button>
        </div>
      </div>
    </div>

    <div class="chat-header">
      <div class="header-controls">
        <!-- Provider selector — always visible so users can switch/configure providers -->
        <template v-if="providers.length > 0">
          <select
            id="provider-select"
            v-model="selectedProvider"
            class="model-select"
            title="Select AI Provider"
            aria-label="AI Provider"
            @change="onProviderChange"
          >
            <option v-for="p in providers" :key="p.id" :value="p.id">
              {{ p.name }}{{ p.has_key ? '' : ' ⚠' }}
            </option>
          </select>

          <!-- Model selector (provider-specific) -->
          <select
            id="model-select"
            v-model="selectedModel"
            class="model-select"
            title="Select Model"
            aria-label="AI Model"
            @change="onModelChange"
          >
            <option v-for="model in availableModels" :key="model.id" :value="model.id">
              {{ model.name }}
            </option>
          </select>
        </template>
        <div class="header-spacer"></div>
        <button v-if="messages.length > 0" @click="clearMessages" class="icon-btn" title="Clear chat" :disabled="isLoading">
          <span class="material-icons">delete_sweep</span>
        </button>
        <button @click="openSessionsModal" class="icon-btn" title="Conversation sessions">
          <span class="material-icons">history</span>
        </button>
        <button @click="openSettings()" class="icon-btn" title="Configure API Keys">
          <span class="material-icons">settings</span>
        </button>
      </div>
    </div>

    <div class="chat-messages" ref="messagesContainer">
      <div v-if="messages.length === 0" class="welcome-message">
        <span class="material-icons welcome-icon">smart_toy</span>
        <p>Welcome to KiAssist!</p>
        <p class="hint">Ask me anything about KiCAD or PCB design. Powered by {{ currentProviderInfo?.name ?? 'AI' }}.</p>
      </div>

      <div
        v-for="(message, index) in messages"
        :key="message.id"
        :class="['message', message.sender]"
      >
        <div class="message-avatar">
          <span class="material-icons">{{ message.sender === 'user' ? 'person' : 'smart_toy' }}</span>
        </div>
        <div class="message-content">
          <!-- Editing mode -->
          <div v-if="editingMessageId === message.id" class="edit-area">
            <textarea v-model="editingText" class="edit-textarea" rows="3"></textarea>
            <div class="edit-actions">
              <button @click="saveEdit(index)" class="btn-primary btn-sm">Save &amp; Resend</button>
              <button @click="cancelEdit" class="btn-secondary btn-sm">Cancel</button>
            </div>
          </div>
          <!-- Normal display -->
          <template v-else>
            <div
              v-if="message.sender === 'assistant'"
              class="message-text markdown-content"
              v-html="renderMarkdown(message.text)"
            ></div>
            <div v-else class="message-text" v-html="renderMarkdown(message.text, true)"></div>
            <div class="message-footer">
              <span class="message-time">{{ message.timestamp.toLocaleTimeString() }}</span>
              <span v-if="message.isStreaming" class="streaming-indicator">
                <span class="streaming-dot"></span>
              </span>
              <div class="message-actions">
                <button
                  @click="copyMessage(message.id, message.text)"
                  class="action-btn"
                  :title="copiedMessageId === message.id ? 'Copied!' : 'Copy'"
                >
                  <span class="material-icons">{{ copiedMessageId === message.id ? 'check' : 'content_copy' }}</span>
                </button>
                <button
                  v-if="message.sender === 'assistant' && !message.isStreaming"
                  @click="regenerateMessage(index)"
                  class="action-btn"
                  title="Regenerate"
                  :disabled="isLoading"
                >
                  <span class="material-icons">refresh</span>
                </button>
                <button
                  v-if="message.sender === 'user'"
                  @click="startEdit(message.id, message.text)"
                  class="action-btn"
                  title="Edit"
                  :disabled="isLoading"
                >
                  <span class="material-icons">edit</span>
                </button>
              </div>
            </div>
          </template>
        </div>
      </div>

      <div v-if="isLoading && !messages.some(m => m.isStreaming)" class="loading-indicator">
        <div class="typing-dots">
          <span></span>
          <span></span>
          <span></span>
        </div>
      </div>
    </div>

    <div class="chat-input">
      <textarea
        v-model="inputMessage"
        @keypress="handleKeyPress"
        placeholder="Type your message here... (Press Enter to send)"
        rows="2"
        :disabled="isLoading"
      />
      <button @click="sendMessage" :disabled="!inputMessage.trim() || isLoading" class="send-btn" :title="isLoading ? 'Sending...' : 'Send message'">
        <span class="material-icons">{{ isLoading ? 'hourglass_empty' : 'send' }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.chat-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  max-height: 100%;
  overflow: hidden;
  background-color: var(--bg-primary);
}

.chat-header {
  padding: 0.625rem 1rem;
  background-color: var(--bg-primary);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
  box-shadow: var(--shadow-sm);
}

.header-controls {
  display: flex;
  align-items: center;
  gap: 0.625rem;
}

.header-spacer {
  flex: 1;
}

.model-select {
  padding: 0.375rem 0.625rem;
  background-color: var(--bg-input);
  color: var(--text-primary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  font-size: 0.8125rem;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.model-select:hover {
  border-color: var(--accent-color);
}

.model-select:focus {
  outline: none;
  border-color: var(--accent-color);
  box-shadow: 0 0 0 2px rgba(88, 101, 242, 0.2);
}

.icon-btn {
  padding: 0.375rem;
  background: transparent;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
}

.icon-btn .material-icons {
  font-size: 1.375rem;
  color: var(--text-secondary);
}

.icon-btn:hover {
  background-color: var(--bg-tertiary);
}

.icon-btn:hover .material-icons {
  color: var(--accent-color);
}

.icon-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 1.25rem;
  background-color: var(--bg-primary);
  min-height: 0;
}

.welcome-message {
  text-align: center;
  padding: 3rem 1.5rem;
  color: var(--text-secondary);
}

.welcome-icon {
  font-size: 3rem;
  opacity: 0.3;
  margin-bottom: 0.75rem;
}

.welcome-message p:first-of-type {
  font-size: 1.375rem;
  margin-bottom: 0.625rem;
  font-weight: 500;
}

.hint {
  font-size: 0.875rem;
  opacity: 0.85;
}

.message {
  display: flex;
  gap: 0.625rem;
  margin-bottom: 1rem;
  animation: fadeIn 0.2s ease-out;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

.message.user {
  flex-direction: row-reverse;
}

.message.assistant {
  justify-content: flex-start;
}

/* Avatar */
.message-avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 2px;
}

.message-avatar .material-icons {
  font-size: 1.125rem;
}

.message.user .message-avatar {
  background: linear-gradient(135deg, var(--accent-color) 0%, var(--accent-hover) 100%);
  color: white;
}

.message.assistant .message-avatar {
  background-color: var(--bg-tertiary);
  color: var(--text-secondary);
  border: 1px solid var(--border-color);
}

/* Message Content */
.message-content {
  max-width: min(75%, 700px);
  padding: 0.75rem 1rem;
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  position: relative;
  min-width: 0;
}

.message.user .message-content {
  background: linear-gradient(135deg, var(--accent-color) 0%, var(--accent-hover) 100%);
  color: white;
  border-bottom-right-radius: var(--radius-sm);
}

.message.assistant .message-content {
  background-color: var(--bg-primary);
  color: var(--text-primary);
  border: 1px solid var(--border-color);
  border-bottom-left-radius: var(--radius-sm);
}

.message-text {
  word-wrap: break-word;
  word-break: break-word;
  overflow-wrap: break-word;
  line-height: 1.5;
  user-select: text;
  -webkit-user-select: text;
  max-width: 100%;
  font-size: 0.9375rem;
}

/* Message Footer */
.message-footer {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.375rem;
}

.message-time {
  font-size: 0.6875rem;
  opacity: 0.6;
}

.message-actions {
  display: flex;
  gap: 0.125rem;
  margin-left: auto;
  opacity: 0;
  transition: opacity 0.15s ease;
}

.message:hover .message-actions {
  opacity: 1;
}

.action-btn {
  padding: 0.1875rem;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
}

.action-btn .material-icons {
  font-size: 0.9375rem;
}

.action-btn:hover {
  background-color: rgba(0, 0, 0, 0.08);
  color: var(--accent-color);
}

.message.user .action-btn {
  color: rgba(255, 255, 255, 0.7);
}

.message.user .action-btn:hover {
  background-color: rgba(255, 255, 255, 0.15);
  color: white;
}

.action-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

/* Streaming Indicator */
.streaming-indicator {
  display: flex;
  align-items: center;
}

.streaming-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: var(--accent-color);
  animation: pulse 1s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}

/* Edit Area */
.edit-area {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.edit-textarea {
  width: 100%;
  padding: 0.5rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  font-family: inherit;
  font-size: 0.875rem;
  resize: vertical;
  background-color: var(--bg-input);
  color: var(--text-primary);
}

.edit-textarea:focus {
  outline: none;
  border-color: var(--accent-color);
}

.edit-actions {
  display: flex;
  gap: 0.5rem;
  justify-content: flex-end;
}

.btn-sm {
  padding: 0.25rem 0.75rem !important;
  font-size: 0.75rem !important;
}

/* Markdown Content Styles */
.markdown-content :deep(p) {
  margin: 0 0 0.5em 0;
}

.markdown-content :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-content :deep(pre) {
  margin: 0.75em 0;
  border-radius: var(--radius-md);
  overflow-x: auto;
}

.markdown-content :deep(pre code) {
  display: block;
  padding: 0.75rem 1rem;
  font-size: 0.8125rem;
  line-height: 1.5;
  font-family: 'SF Mono', 'Consolas', 'Monaco', 'Courier New', monospace;
}

.markdown-content :deep(code:not(pre code)) {
  background-color: var(--bg-tertiary);
  padding: 0.125rem 0.375rem;
  border-radius: 3px;
  font-size: 0.85em;
  font-family: 'SF Mono', 'Consolas', 'Monaco', 'Courier New', monospace;
}

.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  margin: 0.5em 0;
  padding-left: 1.5em;
}

.markdown-content :deep(li) {
  margin-bottom: 0.25em;
}

.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3),
.markdown-content :deep(h4) {
  margin: 0.75em 0 0.25em;
  font-weight: 600;
}

.markdown-content :deep(h1) { font-size: 1.25em; }
.markdown-content :deep(h2) { font-size: 1.125em; }
.markdown-content :deep(h3) { font-size: 1em; }

.markdown-content :deep(blockquote) {
  border-left: 3px solid var(--accent-color);
  padding-left: 0.75em;
  margin: 0.5em 0;
  opacity: 0.85;
}

.markdown-content :deep(table) {
  border-collapse: collapse;
  margin: 0.5em 0;
  width: 100%;
}

.markdown-content :deep(th),
.markdown-content :deep(td) {
  border: 1px solid var(--border-color);
  padding: 0.375rem 0.625rem;
  font-size: 0.875rem;
}

.markdown-content :deep(th) {
  background-color: var(--bg-tertiary);
  font-weight: 600;
}

.markdown-content :deep(a) {
  color: var(--accent-color);
  text-decoration: none;
}

.markdown-content :deep(a:hover) {
  text-decoration: underline;
}

.markdown-content :deep(hr) {
  border: none;
  border-top: 1px solid var(--border-color);
  margin: 0.75em 0;
}

.chat-input {
  display: flex;
  gap: 0.75rem;
  padding: 1rem;
  background-color: var(--bg-primary);
  border-top: 1px solid var(--border-color);
  flex-shrink: 0;
  box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.04);
}

textarea {
  flex: 1;
  padding: 0.625rem 0.875rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  font-family: inherit;
  font-size: 0.9375rem;
  resize: none;
  background-color: var(--bg-input);
  color: var(--text-primary);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

textarea:focus {
  outline: none;
  border-color: var(--accent-color);
  box-shadow: 0 0 0 2px rgba(88, 101, 242, 0.15);
}

button {
  cursor: pointer;
  border: none;
  border-radius: var(--radius-md);
  font-family: inherit;
  font-size: 0.9375rem;
  font-weight: 500;
  transition: all 0.15s ease;
}

button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.send-btn {
  padding: 0.625rem;
  background: linear-gradient(135deg, var(--accent-color) 0%, var(--accent-hover) 100%);
  color: white;
  box-shadow: var(--shadow-sm);
  display: flex;
  align-items: center;
  justify-content: center;
}

.send-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}

.send-btn:active:not(:disabled) {
  transform: translateY(0);
}

.send-btn .material-icons {
  font-size: 1.375rem;
}

/* Modal styles */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(4px);
}

.modal-content {
  background-color: var(--bg-primary);
  padding: 1.75rem;
  border-radius: var(--radius-lg);
  max-width: 450px;
  width: 90%;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  border: 1px solid var(--border-color);
}

.modal-content h3 {
  margin: 0 0 1rem 0;
  color: var(--text-primary);
  font-size: 1.125rem;
  font-weight: 600;
}

.modal-description {
  color: var(--text-secondary);
  margin-bottom: 1.25rem;
  line-height: 1.5;
  font-size: 0.9375rem;
}

.modal-description a {
  color: var(--accent-color);
  text-decoration: none;
  font-weight: 500;
}

.modal-description a:hover {
  text-decoration: underline;
}

.api-key-input {
  width: 100%;
  padding: 0.625rem 0.875rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  font-family: inherit;
  font-size: 0.9375rem;
  background-color: var(--bg-input);
  color: var(--text-primary);
  margin-bottom: 1rem;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.api-key-input:focus {
  outline: none;
  border-color: var(--accent-color);
  box-shadow: 0 0 0 2px rgba(88, 101, 242, 0.15);
}

.error-banner {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  padding: 0.75rem;
  margin-bottom: 1rem;
  background-color: rgba(220, 38, 38, 0.08);
  border: 1px solid rgba(220, 38, 38, 0.2);
  border-radius: var(--radius-md);
  color: #ef4444;
  font-size: 0.875rem;
}

.error-icon {
  font-size: 1.25rem;
  flex-shrink: 0;
  color: #ef4444;
}

.modal-actions {
  display: flex;
  gap: 0.75rem;
  justify-content: flex-end;
}

.btn-primary {
  padding: 0.625rem 1.25rem;
  background: linear-gradient(135deg, var(--accent-color) 0%, var(--accent-hover) 100%);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 0.9375rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-primary:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}

.btn-secondary {
  padding: 0.625rem 1.25rem;
  background: transparent;
  color: var(--text-primary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  font-size: 0.9375rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-secondary:hover {
  background-color: var(--bg-tertiary);
  border-color: var(--text-secondary);
}

/* Loading indicator */
.loading-indicator {
  display: flex;
  justify-content: flex-start;
  margin-bottom: 1rem;
  padding-left: 42px;
}

.typing-dots {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.75rem 1rem;
  background-color: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-sm);
}

.typing-dots span {
  width: 8px;
  height: 8px;
  background-color: var(--accent-color);
  border-radius: 50%;
  animation: typing 1.4s infinite;
}

.typing-dots span:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-dots span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes typing {
  0%, 60%, 100% {
    transform: translateY(0);
    opacity: 0.5;
  }
  30% {
    transform: translateY(-8px);
    opacity: 1;
  }
}

/* Scrollbar styling */
.chat-messages::-webkit-scrollbar {
  width: 8px;
}

.chat-messages::-webkit-scrollbar-track {
  background: transparent;
}

.chat-messages::-webkit-scrollbar-thumb {
  background: var(--border-color);
  border-radius: 4px;
}

.chat-messages::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}

/* Multi-provider modal styles */
.modal-provider-selector {
  margin-bottom: 1rem;
}

.modal-label {
  display: block;
  font-size: 0.8125rem;
  color: var(--text-secondary);
  font-weight: 500;
  margin-bottom: 0.375rem;
}

.provider-tabs {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.provider-tab {
  padding: 0.375rem 0.75rem;
  background: transparent;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  font-size: 0.8125rem;
  cursor: pointer;
  color: var(--text-secondary);
  transition: all 0.15s ease;
  display: flex;
  align-items: center;
  gap: 0.25rem;
}

.provider-tab.active {
  border-color: var(--accent-color);
  color: var(--accent-color);
  background-color: rgba(88, 101, 242, 0.08);
}

.provider-tab.has-key {
  color: var(--text-primary);
}

.provider-tab:hover:not(.active) {
  border-color: var(--text-secondary);
  color: var(--text-primary);
}

.key-indicator {
  font-size: 0.75rem;
  color: #22c55e;
  font-weight: 700;
}

/* Wide modal for sessions */
.modal-wide {
  max-width: 560px;
}

/* Sessions list */
.sessions-empty {
  color: var(--text-secondary);
  font-size: 0.9375rem;
  margin: 1rem 0;
}

.sessions-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  max-height: 320px;
  overflow-y: auto;
  margin-bottom: 1rem;
}

.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.625rem 0.75rem;
  background-color: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  gap: 0.75rem;
}

.session-info {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  min-width: 0;
}

.session-id {
  font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
  font-size: 0.8125rem;
  color: var(--text-primary);
}

.session-date {
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.session-count {
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.session-actions {
  display: flex;
  gap: 0.5rem;
  flex-shrink: 0;
}
</style>
