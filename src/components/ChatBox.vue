<script setup lang="ts">
import { ref, computed, onMounted, nextTick, watch } from 'vue';
import { marked } from 'marked';
import hljs from 'highlight.js';
import '../types/pywebview';
import type { ProviderInfo, SessionInfo, ProviderModel, GemmaModelInfo, GemmaServerStatus, GemmaDownloadProgress } from '../types/pywebview';
import type ApiActivityPanel from './ApiActivityPanel.vue';
import type { ApiActivityEntry } from './ApiActivityPanel.vue';

// Props: parent passes the activity panel ref
const props = defineProps<{
  activityPanel?: InstanceType<typeof ApiActivityPanel> | null;
}>();

// Helper to log API calls to the activity panel
function generateEntryId(): string {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

function summarize(val: any, maxLen = 120): string {
  if (val === undefined || val === null) return '';
  const s = typeof val === 'string' ? val : JSON.stringify(val);
  return s.length > maxLen ? s.substring(0, maxLen) + '…' : s;
}

function fullString(val: any): string {
  if (val === undefined || val === null) return '';
  return typeof val === 'string' ? val : JSON.stringify(val, null, 2);
}

async function trackedApiCall<T>(
  method: string,
  args: any[],
  fn: () => Promise<T>,
  isStreamPoll = false,
): Promise<T> {
  const start = Date.now();
  let result: T;
  let success = true;
  try {
    result = await fn();
    return result;
  } catch (e) {
    success = false;
    result = { error: String(e) } as any;
    throw e;
  } finally {
    const duration = Date.now() - start;
    const entry: ApiActivityEntry = {
      id: generateEntryId(),
      timestamp: new Date(),
      method,
      requestSummary: summarize(args.length === 1 ? args[0] : args),
      requestFull: fullString(args.length === 1 ? args[0] : args),
      responseSummary: summarize(result!),
      responseFull: fullString(result!),
      durationMs: duration,
      success,
      isStreamPoll,
    };
    props.activityPanel?.addEntry(entry);
  }
}

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
  thinking?: string;
}

const STORAGE_KEY = 'kiassist-chat-messages';
const PROVIDER_KEY = 'kiassist-provider';
const MODEL_KEY = 'kiassist-model';
const SECONDARY_PROVIDER_KEY = 'kiassist-secondary-provider';
const SECONDARY_MODEL_KEY = 'kiassist-secondary-model';

const copiedMessageId = ref<string | null>(null);
const copiedChatHistory = ref(false);
const messages = ref<Message[]>([]);
const inputMessage = ref('');
const queuedMessage = ref<string | null>(null);
const selectedProvider = ref('gemma4');
const selectedModel = ref('gemma4-e2b-q4_k_m');
// Quick (lightweight/cheap) model
const selectedSecondaryProvider = ref('gemma4');
const selectedSecondaryModel = ref('gemma4-e2b-q4_k_m');
const hasApiKey = ref(false);
const showApiKeyPrompt = ref(false);
const showSessionsModal = ref(false);
const apiKeyInput = ref('');
// Which provider's key is being configured in the settings modal
const configuringProvider = ref('gemma4');
const isLoading = ref(false);
const apiKeyWarning = ref<string>('');
const apiKeyError = ref<string>('');
const messagesContainer = ref<HTMLElement | null>(null);
const editingMessageId = ref<string | null>(null);
const editingText = ref('');
// Local model server
const localBaseUrl = ref('http://localhost:11434/v1');
const localBaseUrlInput = ref('');
const localBaseUrlError = ref<string>('');
const isLoadingLocalModels = ref(false);
const detectedLocalModels = ref<ProviderModel[]>([]);
// Gemma 4 local model state
const gemmaModels = ref<GemmaModelInfo[]>([]);
const gemmaServerStatus = ref<GemmaServerStatus>({ running: false, url: null, model_id: null, port: 8741 });
const gemmaDownloadProgress = ref<GemmaDownloadProgress | null>(null);
const gemmaDownloadPollTimer = ref<ReturnType<typeof setInterval> | null>(null);
const gemmaError = ref('');
const isLoadingGemmaModels = ref(false);
const gemmaModelsLoaded = ref(false);

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

const secondaryProviderInfo = computed<ProviderInfo | undefined>(() =>
  providers.value.find(p => p.id === selectedSecondaryProvider.value)
);

const availableSecondaryModels = computed(() => {
  const base = secondaryProviderInfo.value?.models ?? [];
  // Merge in any locally-detected models when the secondary provider is "local"
  if (selectedSecondaryProvider.value === 'local' && detectedLocalModels.value.length > 0) {
    const baseIds = new Set(base.map(m => m.id));
    const extra = detectedLocalModels.value.filter(m => !baseIds.has(m.id));
    return [...base, ...extra];
  }
  return base;
});

function generateMessageId(): string {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/** Returns a status suffix for a provider dropdown option. */
function providerWarning(p: ProviderInfo): string {
  if (p.id === 'local') return '';
  if (p.id === 'gemma4') {
    if (!gemmaModelsLoaded.value) return '';  // Don't warn until we know
    return gemmaHasDownloadedModel.value ? '' : ' (no model)';
  }
  return p.has_key ? '' : ' (no key)';
}

/** Whether any Gemma model has been downloaded. */
const gemmaHasDownloadedModel = computed(() =>
  gemmaModels.value.some(m => m.downloaded)
);

/** Whether the Gemma provider is ready (has downloaded model and server running or downloadable). */
const gemmaReady = computed(() =>
  gemmaHasDownloadedModel.value
);

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
        ...(m.thinking ? { thinking: m.thinking } : {}),
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
        thinking: m.thinking || undefined,
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
  // Start a fresh backend session so history isn't carried over
  if (window.pywebview?.api) {
    trackedApiCall('new_chat_session', [], () =>
      window.pywebview!.api.new_chat_session()
    ).catch(() => {});
  }
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
    const result = await trackedApiCall('set_provider', [selectedProvider.value, selectedModel.value], () =>
      window.pywebview!.api.set_provider(selectedProvider.value, selectedModel.value)
    );
    if (!result.success) {
      console.error('[UI] set_provider failed:', result.error);
    } else if (result.warning) {
      console.warn('[UI] set_provider warning:', result.warning);
    }
  }
  // Derive hasApiKey from the in-memory providers list (no extra IPC call)
  hasApiKey.value = (selectedProvider.value === 'local' || selectedProvider.value === 'gemma4')
    ? true
    : (currentProviderInfo.value?.has_key ?? false);
}

async function onModelChange() {
  localStorage.setItem(MODEL_KEY, selectedModel.value);
  if (window.pywebview?.api) {
    await trackedApiCall('set_provider', [selectedProvider.value, selectedModel.value], () =>
      window.pywebview!.api.set_provider(selectedProvider.value, selectedModel.value)
    );
  }
}

async function onSecondaryProviderChange() {
  const info = secondaryProviderInfo.value;
  if (info) {
    selectedSecondaryModel.value = info.default_model;
  }
  localStorage.setItem(SECONDARY_PROVIDER_KEY, selectedSecondaryProvider.value);
  localStorage.setItem(SECONDARY_MODEL_KEY, selectedSecondaryModel.value);
  if (window.pywebview?.api) {
    const result = await trackedApiCall('set_secondary_model', [selectedSecondaryProvider.value, selectedSecondaryModel.value], () =>
      window.pywebview!.api.set_secondary_model(selectedSecondaryProvider.value, selectedSecondaryModel.value)
    );
    if (!result.success) {
      console.error('[UI] set_secondary_model failed:', result.error);
    } else if (result.warning) {
      console.warn('[UI] set_secondary_model warning:', result.warning);
    }
  }
}

async function onSecondaryModelChange() {
  localStorage.setItem(SECONDARY_MODEL_KEY, selectedSecondaryModel.value);
  if (window.pywebview?.api) {
    const result = await trackedApiCall('set_secondary_model', [selectedSecondaryProvider.value, selectedSecondaryModel.value], () =>
      window.pywebview!.api.set_secondary_model(selectedSecondaryProvider.value, selectedSecondaryModel.value)
    );
    if (!result.success) {
      console.error('[UI] set_secondary_model failed:', result.error);
    } else if (result.warning) {
      console.warn('[UI] set_secondary_model warning:', result.warning);
    }
  }
}

// Load provider state from backend; localStorage is the source of truth for
// provider/model selection — backend defaults are only used when no saved
// preference exists.
async function loadProviders() {
  if (!window.pywebview?.api) return;
  try {
    const result = await trackedApiCall('get_providers', [], () =>
      window.pywebview!.api.get_providers()
    );
    if (result.success && result.providers) {
      // Backend is the single source of truth for provider metadata
      providers.value = result.providers;

      // Sync local base URL from backend
      const localInfo = result.providers.find((p: ProviderInfo) => p.id === 'local');
      if (localInfo?.base_url) {
        localBaseUrl.value = localInfo.base_url;
        localBaseUrlInput.value = localInfo.base_url;
      }

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
      await trackedApiCall('set_provider', [selectedProvider.value, selectedModel.value], () =>
        window.pywebview!.api.set_provider(selectedProvider.value, selectedModel.value)
      );

      // Derive hasApiKey from the providers list — no extra round-trips needed
      const active = result.providers.find((p: ProviderInfo) => p.id === selectedProvider.value);
      hasApiKey.value = (selectedProvider.value === 'local' || selectedProvider.value === 'gemma4') ? true : (active?.has_key ?? false);

      // --- Secondary model ---
      const storedSecondaryProvider = localStorage.getItem(SECONDARY_PROVIDER_KEY);
      const storedSecondaryModel = localStorage.getItem(SECONDARY_MODEL_KEY);

      const nextSecondaryProvider =
        (storedSecondaryProvider &&
          result.providers.find((p: ProviderInfo) => p.id === storedSecondaryProvider)?.id) ??
        result.secondary_provider ??
        selectedSecondaryProvider.value;

      const nextSecondaryProviderInfo =
        result.providers.find((p: ProviderInfo) => p.id === nextSecondaryProvider);

      const nextSecondaryModel =
        storedSecondaryModel ??
        result.secondary_model ??
        nextSecondaryProviderInfo?.default_model ??
        selectedSecondaryModel.value;

      selectedSecondaryProvider.value = nextSecondaryProvider;
      selectedSecondaryModel.value = nextSecondaryModel ?? selectedSecondaryModel.value;

      localStorage.setItem(SECONDARY_PROVIDER_KEY, selectedSecondaryProvider.value);
      localStorage.setItem(SECONDARY_MODEL_KEY, selectedSecondaryModel.value);
      await trackedApiCall('set_secondary_model', [selectedSecondaryProvider.value, selectedSecondaryModel.value], () =>
        window.pywebview!.api.set_secondary_model(selectedSecondaryProvider.value, selectedSecondaryModel.value)
      );

      // Pre-load Gemma model info so status indicators are accurate from the start
      loadGemmaModels();
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
  // Auto-load Gemma 4 model list when opening settings on that tab
  if (configuringProvider.value === 'gemma4') {
    loadGemmaModels();
  }
}

function selectProviderForConfig(providerId: string) {
  configuringProvider.value = providerId;
  apiKeyInput.value = '';
  apiKeyError.value = '';
  apiKeyWarning.value = '';
  // Auto-load Gemma 4 model list when switching to that tab
  if (providerId === 'gemma4') {
    loadGemmaModels();
  }
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
      const result = await trackedApiCall('set_api_key', [configuringProvider.value], () =>
        window.pywebview!.api.set_api_key(trimmedKey, configuringProvider.value)
      );
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

async function saveLocalBaseUrl() {
  const url = localBaseUrlInput.value.trim();
  if (!url) {
    localBaseUrlError.value = 'Server URL cannot be empty.';
    return;
  }
  localBaseUrlError.value = '';
  if (!window.pywebview?.api) return;
  try {
    const result = await trackedApiCall('set_local_base_url', [url], () =>
      window.pywebview!.api.set_local_base_url(url)
    );
    if (result.success) {
      localBaseUrl.value = url;
      // Update the local provider entry in the providers list
      const lp = providers.value.find(p => p.id === 'local');
      if (lp) lp.base_url = url;
      if (result.warning) {
        localBaseUrlError.value = result.warning;
      }
    } else {
      localBaseUrlError.value = result.error || 'Failed to save server URL.';
    }
  } catch (e) {
    localBaseUrlError.value = 'Failed to save server URL.';
  }
}

async function detectLocalModels() {
  if (!window.pywebview?.api) return;
  isLoadingLocalModels.value = true;
  localBaseUrlError.value = '';
  try {
    const result = await trackedApiCall('get_local_models', [], () =>
      window.pywebview!.api.get_local_models()
    );
    if (result.success) {
      detectedLocalModels.value = result.models ?? [];
      // Merge detected models into the local provider's model list
      const lp = providers.value.find(p => p.id === 'local');
      if (lp && result.models.length > 0) {
        const existingIds = new Set(lp.models.map((m: ProviderModel) => m.id));
        const newModels = result.models.filter((m: ProviderModel) => !existingIds.has(m.id));
        lp.models = [...lp.models, ...newModels];
      }
      if (result.base_url) {
        localBaseUrl.value = result.base_url;
        localBaseUrlInput.value = result.base_url;
      }
    } else {
      localBaseUrlError.value = result.error || 'Could not connect to local server.';
    }
  } catch (e) {
    const errorMessage = e instanceof Error ? e.message : String(e);
    localBaseUrlError.value = `Error: ${errorMessage}`;
  } finally {
    isLoadingLocalModels.value = false;
  }
}

// ---- Gemma 4 local model management ----

async function loadGemmaModels() {
  if (!window.pywebview?.api) return;
  isLoadingGemmaModels.value = true;
  try {
    const result = await trackedApiCall('get_gemma_models', [], () =>
      window.pywebview!.api.get_gemma_models()
    );
    if (result.success) {
      gemmaModels.value = result.models ?? [];
      gemmaServerStatus.value = result.server_status;
    } else {
      gemmaError.value = result.error || 'Failed to load Gemma models.';
    }
  } catch (e) {
    console.error('[Gemma] Failed to load models:', e);
    gemmaError.value = `Failed to load Gemma models: ${e instanceof Error ? e.message : String(e)}`;
  } finally {
    isLoadingGemmaModels.value = false;
    gemmaModelsLoaded.value = true;
  }
}

async function downloadGemmaModel(modelId: string) {
  if (!window.pywebview?.api) return;
  gemmaError.value = '';
  try {
    const result = await trackedApiCall('download_gemma_model', [modelId], () =>
      window.pywebview!.api.download_gemma_model(modelId)
    );
    if (!result.success) {
      gemmaError.value = result.error || 'Download failed.';
      return;
    }
    // Start polling download progress
    startGemmaDownloadPoll();
  } catch (e) {
    gemmaError.value = `Download error: ${e instanceof Error ? e.message : String(e)}`;
  }
}

function startGemmaDownloadPoll() {
  stopGemmaDownloadPoll();
  gemmaDownloadPollTimer.value = setInterval(async () => {
    if (!window.pywebview?.api) return;
    try {
      const progress = await window.pywebview.api.get_gemma_download_progress();
      gemmaDownloadProgress.value = progress;
      if (progress.status === 'completed' || progress.status === 'error' || progress.status === 'cancelled' || progress.status === 'idle') {
        stopGemmaDownloadPoll();
        if (progress.status === 'completed') {
          await loadGemmaModels();
        }
      }
    } catch (e) {
      stopGemmaDownloadPoll();
    }
  }, 500);
}

function stopGemmaDownloadPoll() {
  if (gemmaDownloadPollTimer.value) {
    clearInterval(gemmaDownloadPollTimer.value);
    gemmaDownloadPollTimer.value = null;
  }
}

async function cancelGemmaDownload() {
  if (!window.pywebview?.api) return;
  await trackedApiCall('cancel_gemma_download', [], () =>
    window.pywebview!.api.cancel_gemma_download()
  );
}

async function deleteGemmaModel(modelId: string) {
  if (!window.pywebview?.api) return;
  gemmaError.value = '';
  const result = await trackedApiCall('delete_gemma_model', [modelId], () =>
    window.pywebview!.api.delete_gemma_model(modelId)
  );
  if (result.success) {
    await loadGemmaModels();
  } else {
    gemmaError.value = result.error || 'Failed to delete model.';
  }
}

async function startGemmaServer(modelId: string) {
  if (!window.pywebview?.api) return;
  gemmaError.value = '';
  const result = await trackedApiCall('start_gemma_server', [modelId], () =>
    window.pywebview!.api.start_gemma_server(modelId)
  );
  if (result.success) {
    gemmaServerStatus.value = { running: true, url: result.url ?? null, model_id: result.model_id ?? modelId, port: 8741 };
  } else {
    gemmaError.value = result.error || 'Failed to start server.';
  }
}

async function stopGemmaServer() {
  if (!window.pywebview?.api) return;
  gemmaError.value = '';
  const result = await trackedApiCall('stop_gemma_server', [], () =>
    window.pywebview!.api.stop_gemma_server()
  );
  if (result.success) {
    gemmaServerStatus.value = { running: false, url: null, model_id: null, port: 8741 };
  } else {
    gemmaError.value = result.error || 'Failed to stop server.';
  }
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${sizes[i]}`;
}

function formatEta(seconds: number): string {
  if (seconds <= 0) return '';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
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
      const startResult = await trackedApiCall('start_stream_message', [messageText, selectedModel.value], () =>
        window.pywebview!.api.start_stream_message(messageText, selectedModel.value)
      );

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
          const poll = await trackedApiCall('poll_stream', [], () =>
            window.pywebview!.api.poll_stream(), true
          );
          if (poll.success && messages.value[streamIdx]) {
            messages.value[streamIdx].text = poll.text || '';
            if (poll.thinking) {
              messages.value[streamIdx].thinking = poll.thinking;
            }

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
              processQueue();
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

// Steer: interrupt the current stream and redirect the model with a new message
async function steerMessage() {
  if (!inputMessage.value.trim() || !isLoading.value) return;
  if (!window.pywebview?.api) return;

  const steerText = inputMessage.value;
  inputMessage.value = '';

  // Show the steer message as a user message in the chat
  messages.value.push({
    id: generateMessageId(),
    text: steerText,
    sender: 'user',
    timestamp: new Date(),
  });

  // The backend will cancel the current stream, persist partial response,
  // and start a new stream with the steer message
  try {
    const result = await trackedApiCall('steer_stream', [steerText, selectedModel.value], () =>
      window.pywebview!.api.steer_stream(steerText, selectedModel.value)
    );
    if (!result.success) {
      messages.value.push({
        id: generateMessageId(),
        text: `Steer failed: ${result.error || 'Unknown error'}`,
        sender: 'assistant',
        timestamp: new Date(),
      });
      return;
    }

    // Mark any current streaming message as done (partial)
    const currentStreamMsg = messages.value.find(m => m.isStreaming);
    if (currentStreamMsg) {
      currentStreamMsg.isStreaming = false;
    }

    // Create new streaming assistant message for the steered response
    messages.value.push({
      id: generateMessageId(),
      text: '',
      sender: 'assistant',
      timestamp: new Date(),
      isStreaming: true,
    });
    const streamIdx = messages.value.length - 1;

    // Poll for new streamed response
    const pollInterval = setInterval(async () => {
      try {
        const poll = await trackedApiCall('poll_stream', [], () =>
          window.pywebview!.api.poll_stream(), true
        );
        if (poll.success && messages.value[streamIdx]) {
          messages.value[streamIdx].text = poll.text || '';
          if (poll.thinking) {
            messages.value[streamIdx].thinking = poll.thinking;
          }
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
            processQueue();
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

  } catch (error) {
    messages.value.push({
      id: generateMessageId(),
      text: `Steer error: ${error}`,
      sender: 'assistant',
      timestamp: new Date(),
    });
  }
}

// Queue: enqueue a message to be sent automatically after the current stream completes
function queueMessage() {
  if (!inputMessage.value.trim()) return;
  queuedMessage.value = inputMessage.value;
  inputMessage.value = '';
}

// Process queued messages after a stream finishes
async function processQueue() {
  if (queuedMessage.value && !isLoading.value) {
    const queued = queuedMessage.value;
    queuedMessage.value = null;
    const userMessage: Message = {
      id: generateMessageId(),
      text: queued,
      sender: 'user',
      timestamp: new Date(),
    };
    messages.value.push(userMessage);
    await sendMessageWithText(queued);
  }
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
    if (isLoading.value) {
      // While streaming: Ctrl+Enter = steer, plain Enter = queue
      if (event.ctrlKey) {
        steerMessage();
      } else {
        queueMessage();
      }
    } else {
      sendMessage();
    }
  }
}

async function copyMessage(messageId: string, text: string, timestamp: Date, sender: 'user' | 'assistant') {
  try {
    const timeStr = timestamp.toLocaleString();
    const senderLabel = sender === 'user' ? 'User' : 'Assistant';
    const formatted = `[${timeStr}] ${senderLabel}:\n${text}`;
    await navigator.clipboard.writeText(formatted);
    copiedMessageId.value = messageId;
    setTimeout(() => { copiedMessageId.value = null; }, 2000);
  } catch (error) {
    console.error('Failed to copy message:', error);
  }
}

async function copyChatHistory() {
  try {
    const history = messages.value
      .map(m => `[${m.timestamp.toLocaleString()}] ${m.sender === 'user' ? 'User' : 'Assistant'}:\n${m.text}`)
      .join('\n\n---\n\n');
    await navigator.clipboard.writeText(history);
    copiedChatHistory.value = true;
    setTimeout(() => { copiedChatHistory.value = false; }, 2000);
  } catch (error) {
    console.error('Failed to copy chat history:', error);
  }
}

// Session management
async function openSessionsModal() {
  if (window.pywebview?.api) {
    try {
      const result = await trackedApiCall('get_sessions', [], () =>
        window.pywebview!.api.get_sessions()
      );
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
    const result = await trackedApiCall('resume_session', [sessionId], () =>
      window.pywebview!.api.resume_session(sessionId)
    );
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
    const result = await trackedApiCall('export_session', [sessionId], () =>
      window.pywebview!.api.export_session(sessionId)
    );
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
    <!-- Settings Modal -->
    <div v-if="showApiKeyPrompt" class="modal-overlay" @click.self="showApiKeyPrompt = false">
      <div class="modal-content modal-wide settings-modal">
        <div class="modal-header-row">
          <h3>Settings</h3>
          <button class="modal-close-btn" @click="showApiKeyPrompt = false" title="Close">
            <span class="material-icons">close</span>
          </button>
        </div>
        <div class="settings-scroll-area">

        <!-- Model Selection Section -->
        <div class="settings-section">
          <h4 class="settings-section-title">Model Selection</h4>

          <!-- Deep provider/model -->
          <div class="settings-model-row">
            <label class="modal-label">Deep Model</label>
            <p class="settings-hint">Used for complex, in-depth tasks</p>
            <div class="settings-model-selects">
              <select
                v-model="selectedProvider"
                class="model-select"
                :class="{ 'select-warning': providers.find(p => p.id === selectedProvider)?.id !== 'local' && providers.find(p => p.id === selectedProvider)?.id !== 'gemma4' && !providers.find(p => p.id === selectedProvider)?.has_key }"
                title="Select Deep AI Provider"
                aria-label="Deep AI Provider"
                @change="onProviderChange"
              >
                <option v-for="p in providers" :key="p.id" :value="p.id">
                  {{ p.name }}{{ providerWarning(p) }}
                </option>
              </select>
              <select
                v-model="selectedModel"
                class="model-select"
                title="Select Deep Model"
                aria-label="Deep AI Model"
                @change="onModelChange"
              >
                <option v-for="model in availableModels" :key="model.id" :value="model.id">
                  {{ model.name }}
                </option>
              </select>
            </div>
            <!-- Inline status for deep model -->
            <div v-if="selectedProvider !== 'local' && selectedProvider !== 'gemma4' && !currentProviderInfo?.has_key" class="model-status-warning">
              <span class="material-icons">warning_amber</span>
              <span>No API key configured for {{ currentProviderInfo?.name }}.</span>
              <button class="btn-link" @click="selectProviderForConfig(selectedProvider)">Add key</button>
            </div>
            <div v-else-if="selectedProvider === 'gemma4' && gemmaModelsLoaded && !gemmaReady" class="model-status-warning">
              <span class="material-icons">warning_amber</span>
              <span>No Gemma model downloaded.</span>
              <button class="btn-link" @click="selectProviderForConfig('gemma4')">Download</button>
            </div>
          </div>

          <!-- Quick provider/model -->
          <div class="settings-model-row">
            <label class="modal-label">Quick Model</label>
            <p class="settings-hint">Used for fast, lightweight tasks</p>
            <div class="settings-model-selects">
              <select
                v-model="selectedSecondaryProvider"
                class="model-select"
                :class="{ 'select-warning': providers.find(p => p.id === selectedSecondaryProvider)?.id !== 'local' && providers.find(p => p.id === selectedSecondaryProvider)?.id !== 'gemma4' && !providers.find(p => p.id === selectedSecondaryProvider)?.has_key }"
                title="Select Quick AI Provider"
                aria-label="Quick AI Provider"
                @change="onSecondaryProviderChange"
              >
                <option v-for="p in providers" :key="p.id" :value="p.id">
                  {{ p.name }}{{ providerWarning(p) }}
                </option>
              </select>
              <select
                v-model="selectedSecondaryModel"
                class="model-select"
                title="Select Quick Model"
                aria-label="Quick AI Model"
                @change="onSecondaryModelChange"
              >
                <option v-for="model in availableSecondaryModels" :key="model.id" :value="model.id">
                  {{ model.name }}
                </option>
              </select>
            </div>
            <!-- Inline status for quick model -->
            <div v-if="selectedSecondaryProvider !== 'local' && selectedSecondaryProvider !== 'gemma4' && !secondaryProviderInfo?.has_key" class="model-status-warning">
              <span class="material-icons">warning_amber</span>
              <span>No API key configured for {{ secondaryProviderInfo?.name }}.</span>
              <button class="btn-link" @click="selectProviderForConfig(selectedSecondaryProvider)">Add key</button>
            </div>
            <div v-else-if="selectedSecondaryProvider === 'gemma4' && gemmaModelsLoaded && !gemmaReady" class="model-status-warning">
              <span class="material-icons">warning_amber</span>
              <span>No Gemma model downloaded.</span>
              <button class="btn-link" @click="selectProviderForConfig('gemma4')">Download</button>
            </div>
          </div>
        </div>

        <!-- API Key Configuration Section -->
        <div class="settings-section">
          <h4 class="settings-section-title">API Keys</h4>

          <div class="modal-provider-selector">
            <label class="modal-label">Configure provider:</label>
            <div class="provider-tabs">
              <button
                v-for="p in providers"
                :key="p.id"
                :class="['provider-tab', {
                  active: configuringProvider === p.id,
                  'has-key': p.id === 'local' || (p.id === 'gemma4' && gemmaReady) || (p.id !== 'local' && p.id !== 'gemma4' && p.has_key),
                  'needs-attention': (p.id !== 'local' && p.id !== 'gemma4' && !p.has_key) || (p.id === 'gemma4' && !gemmaReady)
                }]"
                @click="selectProviderForConfig(p.id)"
              >
                {{ p.name }}
                <span v-if="p.id === 'local'" class="key-indicator key-local" title="Local server">&#11044;</span>
                <span v-else-if="p.id === 'gemma4' && gemmaReady" class="key-indicator key-ok" title="Model downloaded">&#10003;</span>
                <span v-else-if="p.id === 'gemma4'" class="key-indicator key-missing" title="No model downloaded">&#10007;</span>
                <span v-else-if="p.has_key" class="key-indicator key-ok" title="Key configured">&#10003;</span>
                <span v-else class="key-indicator key-missing" title="No API key">&#10007;</span>
              </button>
            </div>
          </div>

        <!-- Cloud provider: API key input -->
        <template v-if="configuringProvider !== 'local' && configuringProvider !== 'gemma4'">
          <p class="modal-description" v-if="configuringProviderInfo">
            Enter your {{ configuringProviderInfo.name }} API key.
            <template v-if="configuringProviderInfo.key_url">
              Get your key from
              <a :href="configuringProviderInfo.key_url" target="_blank">{{ configuringProviderInfo.key_url }}</a>.
            </template>
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
          </div>
        </template>

        <!-- Gemma 4 provider: local model management -->
        <template v-else-if="configuringProvider === 'gemma4'">
          <p class="modal-description">
            Gemma 4 models run entirely on your machine. Download a model variant
            below, then start the inference server. No API key or internet required
            for inference.
          </p>

          <!-- Server status -->
          <div class="gemma-server-status" :class="{ 'server-running': gemmaServerStatus.running }">
            <span class="material-icons status-icon">{{ gemmaServerStatus.running ? 'check_circle' : 'radio_button_unchecked' }}</span>
            <span v-if="gemmaServerStatus.running">
              Server running &mdash; <code>{{ gemmaServerStatus.url }}</code>
              (model: <strong>{{ gemmaServerStatus.model_id }}</strong>)
            </span>
            <span v-else>Server not running</span>
            <button
              v-if="gemmaServerStatus.running"
              class="btn-secondary btn-sm gemma-stop-btn"
              @click="stopGemmaServer"
            >Stop Server</button>
          </div>

          <!-- Download progress -->
          <div v-if="gemmaDownloadProgress && gemmaDownloadProgress.status === 'downloading'" class="gemma-download-progress">
            <div class="progress-info">
              <span>Downloading <strong>{{ gemmaDownloadProgress.filename }}</strong></span>
              <span>{{ gemmaDownloadProgress.percent.toFixed(1) }}%</span>
            </div>
            <div class="progress-bar-bg">
              <div class="progress-bar-fill" :style="{ width: gemmaDownloadProgress.percent + '%' }"></div>
            </div>
            <div class="progress-details">
              <span>{{ formatBytes(gemmaDownloadProgress.downloaded_bytes) }} / {{ formatBytes(gemmaDownloadProgress.total_bytes) }}</span>
              <span v-if="gemmaDownloadProgress.speed_bytes_per_sec > 0">
                {{ formatBytes(gemmaDownloadProgress.speed_bytes_per_sec) }}/s
              </span>
              <span v-if="gemmaDownloadProgress.eta_seconds > 0">
                ETA: {{ formatEta(gemmaDownloadProgress.eta_seconds) }}
              </span>
              <button class="btn-secondary btn-sm" @click="cancelGemmaDownload">Cancel</button>
            </div>
          </div>
          <div v-else-if="gemmaDownloadProgress && gemmaDownloadProgress.status === 'completed'" class="gemma-download-done success-banner">
            <span class="material-icons">check_circle</span>
            <span><strong>{{ gemmaDownloadProgress.filename }}</strong> downloaded successfully.</span>
          </div>
          <div v-else-if="gemmaDownloadProgress && gemmaDownloadProgress.status === 'cancelled'" class="gemma-download-done warning-banner">
            <span class="material-icons">cancel</span>
            <span>Download of <strong>{{ gemmaDownloadProgress.filename }}</strong> was cancelled.</span>
          </div>
          <div v-else-if="gemmaDownloadProgress && gemmaDownloadProgress.status === 'error'" class="error-banner">
            <span class="material-icons error-icon">warning</span>
            <span>Download failed: {{ gemmaDownloadProgress.error }}</span>
          </div>

          <!-- Error banner -->
          <div v-if="gemmaError" class="error-banner">
            <span class="material-icons error-icon">warning</span>
            <span>{{ gemmaError }}</span>
          </div>

          <!-- Loading state -->
          <div v-if="isLoadingGemmaModels" class="gemma-loading">
            <span class="material-icons spinning">sync</span>
            <span>Loading available models…</span>
          </div>

          <!-- Empty state: no models found -->
          <div v-else-if="gemmaModelsLoaded && gemmaModels.length === 0 && !gemmaError" class="gemma-empty-state">
            <span class="material-icons">cloud_off</span>
            <p>No model variants found.</p>
            <p class="settings-hint">Check your internet connection and try refreshing.</p>
            <button @click="loadGemmaModels" class="btn-primary btn-sm">
              <span class="material-icons btn-icon">refresh</span>
              Retry
            </button>
          </div>

          <!-- Model cards -->
          <div v-else class="gemma-models-grid">
            <div
              v-for="model in gemmaModels"
              :key="model.id"
              class="gemma-model-card"
              :class="{ 'downloaded': model.downloaded, 'active': gemmaServerStatus.model_id === model.id }"
            >
              <div class="gemma-model-header">
                <strong>{{ model.name }}</strong>
                <span class="gemma-size-badge">{{ model.size_label }}</span>
              </div>
              <p class="gemma-model-desc">{{ model.description }}</p>
              <div class="gemma-model-actions">
                <template v-if="!model.downloaded">
                  <button
                    class="btn-primary btn-sm"
                    @click="downloadGemmaModel(model.id)"
                    :disabled="gemmaDownloadProgress?.status === 'downloading'"
                  >
                    <span class="material-icons btn-icon">download</span>
                    Download
                  </button>
                </template>
                <template v-else>
                  <button
                    v-if="gemmaServerStatus.model_id !== model.id"
                    class="btn-primary btn-sm"
                    @click="startGemmaServer(model.id)"
                    :disabled="gemmaServerStatus.running"
                  >
                    <span class="material-icons btn-icon">play_arrow</span>
                    Start
                  </button>
                  <span
                    v-else
                    class="gemma-active-label"
                  >
                    <span class="material-icons btn-icon">check</span>
                    Active
                  </span>
                  <button
                    class="btn-secondary btn-sm btn-danger"
                    @click="deleteGemmaModel(model.id)"
                    :disabled="gemmaServerStatus.model_id === model.id"
                    title="Delete downloaded model"
                  >
                    <span class="material-icons btn-icon">delete</span>
                  </button>
                </template>
              </div>
            </div>
          </div>

          <!-- Hint when no models downloaded -->
          <div v-if="gemmaModels.length > 0 && !gemmaHasDownloadedModel && !isLoadingGemmaModels" class="gemma-no-model-hint">
            <span class="material-icons">info</span>
            <span>Download at least one model above to use the Gemma 4 provider.</span>
          </div>

          <div class="modal-actions">
            <button @click="loadGemmaModels" class="btn-secondary">
              <span class="material-icons btn-icon">refresh</span>
              Refresh
            </button>
          </div>
        </template>

        <!-- Local provider: server URL + model detection -->
        <template v-else>
          <p class="modal-description">
            Configure the URL of your local OpenAI-compatible server.
            Supported servers: <strong>Ollama</strong> (default <code>http://localhost:11434/v1</code>)
            and <strong>LM Studio</strong> (default <code>http://localhost:1234/v1</code>).
            No API key is required. Model detection uses the OpenAI-compatible
            <code>/v1/models</code> endpoint (works with both servers) and falls back
            to Ollama's <code>/api/tags</code> endpoint.
          </p>
          <div class="local-url-row">
            <input
              v-model="localBaseUrlInput"
              type="text"
              placeholder="http://localhost:11434/v1"
              class="api-key-input"
              @keypress.enter="saveLocalBaseUrl"
            />
            <button @click="saveLocalBaseUrl" class="btn-primary btn-sm" :disabled="!localBaseUrlInput.trim()">
              Save
            </button>
          </div>
          <div v-if="localBaseUrlError" class="error-banner">
            <span class="material-icons error-icon">warning</span>
            <span>{{ localBaseUrlError }}</span>
          </div>
          <div class="local-detect-row">
            <button
              @click="detectLocalModels"
              class="btn-secondary"
              :disabled="isLoadingLocalModels"
            >
              <span class="material-icons btn-icon">search</span>
              {{ isLoadingLocalModels ? 'Detecting…' : 'Detect Models' }}
            </button>
            <span v-if="detectedLocalModels.length > 0" class="local-models-found">
              {{ detectedLocalModels.length }} model{{ detectedLocalModels.length !== 1 ? 's' : '' }} found
            </span>
          </div>
          <div v-if="detectedLocalModels.length > 0" class="local-models-list">
            <span
              v-for="m in detectedLocalModels"
              :key="m.id"
              class="local-model-chip"
            >{{ m.name }}</span>
          </div>
          <div class="modal-actions"></div>
        </template>
        </div>
        </div><!-- end settings-scroll-area -->
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
        <!-- Compact model summary -->
        <button class="model-summary-btn" @click="openSettings()" title="Change model settings">
          <span class="material-icons model-summary-icon">smart_toy</span>
          <span class="model-summary-text">
            {{ currentProviderInfo?.name ?? 'AI' }}
            <span class="model-summary-name">{{ availableModels.find(m => m.id === selectedModel)?.name ?? selectedModel }}</span>
          </span>
        </button>
        <div class="header-spacer"></div>
        <button v-if="messages.length > 0" @click="copyChatHistory" class="icon-btn" :title="copiedChatHistory ? 'Copied!' : 'Copy chat history'">
          <span class="material-icons">{{ copiedChatHistory ? 'check' : 'copy_all' }}</span>
        </button>
        <button v-if="messages.length > 0" @click="clearMessages" class="icon-btn" title="Clear chat" :disabled="isLoading">
          <span class="material-icons">delete_sweep</span>
        </button>
        <button @click="openSessionsModal" class="icon-btn" title="Conversation sessions">
          <span class="material-icons">history</span>
        </button>
        <button @click="openSettings()" class="icon-btn" title="Settings">
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
            <!-- Thinking/reasoning block (collapsible) -->
            <details
              v-if="message.thinking && message.sender === 'assistant'"
              class="thinking-block"
              :open="message.isStreaming"
            >
              <summary class="thinking-summary">
                <span class="material-icons thinking-icon">psychology</span>
                <span>{{ message.isStreaming ? 'Thinking...' : 'Thought process' }}</span>
                <span v-if="message.isStreaming" class="thinking-pulse"></span>
              </summary>
              <div class="thinking-content" v-html="renderMarkdown(message.thinking)"></div>
            </details>
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
                  @click="copyMessage(message.id, message.text, message.timestamp, message.sender)"
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

    <div v-if="queuedMessage" class="queued-banner">
      <span class="material-icons queued-icon">schedule</span>
      <span class="queued-text">Queued: {{ queuedMessage.length > 60 ? queuedMessage.substring(0, 60) + '…' : queuedMessage }}</span>
      <button class="queued-cancel" @click="queuedMessage = null" title="Cancel queued message">
        <span class="material-icons">close</span>
      </button>
    </div>

    <div class="chat-input">
      <textarea
        v-model="inputMessage"
        @keypress="handleKeyPress"
        :placeholder="isLoading ? 'Enter to queue · Ctrl+Enter to steer' : 'Type your message here... (Press Enter to send)'"
        rows="2"
      />
      <div v-if="!isLoading" class="send-actions">
        <button @click="sendMessage" :disabled="!inputMessage.trim()" class="send-btn" title="Send message">
          <span class="material-icons">send</span>
        </button>
      </div>
      <div v-else class="send-actions">
        <button @click="steerMessage()" :disabled="!inputMessage.trim()" class="send-btn steer-btn" title="Steer: interrupt and redirect the model (Ctrl+Enter)">
          <span class="material-icons">alt_route</span>
        </button>
        <button @click="queueMessage()" :disabled="!inputMessage.trim()" class="send-btn queue-btn" title="Queue: send after current response finishes (Enter)">
          <span class="material-icons">queue</span>
        </button>
      </div>
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

/* Compact model summary button in header */
.model-summary-btn {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.3125rem 0.625rem;
  background-color: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: border-color 0.15s ease, background-color 0.15s ease;
  color: var(--text-primary);
  font-size: 0.8125rem;
}

.model-summary-btn:hover {
  border-color: var(--accent-color);
  background-color: var(--bg-input);
}

.model-summary-icon {
  font-size: 1.125rem;
  color: var(--accent-color);
}

.model-summary-text {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  color: var(--text-secondary);
  font-size: 0.75rem;
}

.model-summary-name {
  color: var(--text-primary);
  font-weight: 500;
}

/* Settings modal sections */
.settings-section {
  margin-bottom: 1.25rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--border-color);
}

.settings-section:last-of-type {
  border-bottom: none;
  margin-bottom: 0;
  padding-bottom: 0;
}

.settings-section-title {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 0.75rem 0;
}

.settings-model-row {
  margin-bottom: 0.75rem;
}

.settings-model-row:last-child {
  margin-bottom: 0;
}

.settings-hint {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin: 0 0 0.5rem 0;
}

.settings-model-selects {
  display: flex;
  gap: 0.5rem;
}

.settings-model-selects .model-select {
  flex: 1;
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

/* Thinking/Reasoning Block */
.thinking-block {
  margin-bottom: 0.5rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background-color: var(--bg-tertiary);
  overflow: hidden;
}

.thinking-summary {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.4rem 0.6rem;
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--text-secondary);
  user-select: none;
}

.thinking-summary:hover {
  color: var(--text-primary);
}

.thinking-icon {
  font-size: 1rem;
  color: var(--accent-color);
}

.thinking-pulse {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: var(--accent-color);
  animation: pulse 1s infinite;
  margin-left: 0.25rem;
}

.thinking-content {
  padding: 0.4rem 0.6rem 0.6rem;
  font-size: 0.8rem;
  line-height: 1.5;
  color: var(--text-secondary);
  border-top: 1px solid var(--border-color);
  max-height: 300px;
  overflow-y: auto;
}

.thinking-content :deep(p) {
  margin: 0.25rem 0;
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

.send-actions {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.steer-btn {
  background: linear-gradient(135deg, #e67e22 0%, #d35400 100%);
}

.queue-btn {
  background: linear-gradient(135deg, #27ae60 0%, #1e8449 100%);
}

.queued-banner {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 1rem;
  background-color: rgba(39, 174, 96, 0.1);
  border-top: 1px solid rgba(39, 174, 96, 0.3);
  font-size: 0.8125rem;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.queued-icon {
  font-size: 1rem;
  color: #27ae60;
}

.queued-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.queued-cancel {
  padding: 0.125rem;
  background: none;
  color: var(--text-secondary);
  opacity: 0.7;
}

.queued-cancel:hover {
  opacity: 1;
  color: #e74c3c;
}

.queued-cancel .material-icons {
  font-size: 1rem;
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
  max-height: 90vh;
  display: flex;
  flex-direction: column;
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
  font-size: 0.6875rem;
  font-weight: 700;
  line-height: 1;
}

.key-ok {
  color: #22c55e;
}

.key-missing {
  color: #ef4444;
}

.key-local {
  color: var(--text-secondary);
  font-size: 0.5rem;
}

.provider-tab.needs-attention {
  border-color: rgba(251, 188, 4, 0.4);
  color: var(--text-secondary);
}

.provider-tab.needs-attention:not(.active):hover {
  border-color: rgba(251, 188, 4, 0.7);
}

/* Wide modal for settings / sessions */
.modal-wide {
  max-width: 600px;
}

.settings-modal {
  max-height: 85vh;
}

.settings-scroll-area {
  overflow-y: auto;
  flex: 1;
  min-height: 0;
  padding-right: 0.25rem;
}

.settings-scroll-area::-webkit-scrollbar {
  width: 6px;
}

.settings-scroll-area::-webkit-scrollbar-track {
  background: transparent;
}

.settings-scroll-area::-webkit-scrollbar-thumb {
  background: var(--border-color);
  border-radius: 3px;
}

.settings-scroll-area::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}

.modal-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.75rem;
  flex-shrink: 0;
}

.modal-header-row h3 {
  margin: 0;
}

.modal-close-btn {
  background: transparent;
  border: none;
  padding: 0.25rem;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: color 0.15s ease, background-color 0.15s ease;
}

.modal-close-btn:hover {
  color: var(--text-primary);
  background-color: var(--bg-tertiary);
}

.modal-close-btn .material-icons {
  font-size: 1.25rem;
}

/* Provider status indicators */
.model-status-warning {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  margin-top: 0.375rem;
  padding: 0.375rem 0.625rem;
  border-radius: var(--radius-sm);
  background-color: rgba(251, 188, 4, 0.08);
  border: 1px solid rgba(251, 188, 4, 0.25);
  font-size: 0.75rem;
  color: #f9a825;
}

.model-status-warning .material-icons {
  font-size: 0.9375rem;
  flex-shrink: 0;
}

.btn-link {
  background: none;
  border: none;
  color: var(--accent-color);
  font-size: 0.75rem;
  font-weight: 600;
  cursor: pointer;
  padding: 0;
  text-decoration: underline;
  margin-left: auto;
}

.btn-link:hover {
  color: var(--accent-hover);
}

.select-warning {
  border-color: rgba(251, 188, 4, 0.5) !important;
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

/* Local model settings */
.local-url-row {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 0.75rem;
}

.local-url-row .api-key-input {
  flex: 1;
}

.local-detect-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
}

.local-models-found {
  font-size: 0.8125rem;
  color: #22c55e;
  font-weight: 500;
}

.local-models-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  margin-bottom: 0.75rem;
}

.local-model-chip {
  padding: 0.25rem 0.5rem;
  background-color: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
  color: var(--text-primary);
  font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
}

.btn-icon {
  font-size: 1rem;
  vertical-align: middle;
  margin-right: 0.25rem;
}

code {
  font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
  font-size: 0.8125rem;
  background-color: var(--bg-tertiary);
  padding: 0.1em 0.3em;
  border-radius: 3px;
}

/* ---- Gemma 4 local model styles ---- */

.gemma-server-status {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  border-radius: var(--radius-sm);
  background-color: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  margin-bottom: 0.75rem;
  font-size: 0.8125rem;
}

.gemma-server-status.server-running {
  border-color: #34a853;
  background-color: rgba(52, 168, 83, 0.08);
}

.gemma-server-status .status-icon {
  font-size: 1.125rem;
  color: var(--text-secondary);
}

.gemma-server-status.server-running .status-icon {
  color: #34a853;
}

.gemma-stop-btn {
  margin-left: auto;
}

.gemma-download-progress {
  margin-bottom: 0.75rem;
  padding: 0.5rem 0.75rem;
  background-color: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
}

.gemma-download-progress .progress-info {
  display: flex;
  justify-content: space-between;
  font-size: 0.8125rem;
  margin-bottom: 0.375rem;
}

.progress-bar-bg {
  width: 100%;
  height: 6px;
  background-color: var(--border-color);
  border-radius: 3px;
  overflow: hidden;
}

.progress-bar-fill {
  height: 100%;
  background-color: var(--accent-color, #4285f4);
  border-radius: 3px;
  transition: width 0.3s ease;
}

.progress-details {
  display: flex;
  gap: 0.75rem;
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-top: 0.375rem;
  align-items: center;
}

.gemma-models-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.gemma-model-card {
  padding: 0.75rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background-color: var(--bg-secondary);
  transition: border-color 0.2s;
}

.gemma-model-card.downloaded {
  border-color: var(--accent-color, #4285f4);
}

.gemma-model-card.active {
  border-color: #34a853;
  background-color: rgba(52, 168, 83, 0.05);
}

.gemma-model-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.375rem;
  font-size: 0.8125rem;
}

.gemma-size-badge {
  font-size: 0.6875rem;
  padding: 0.125rem 0.375rem;
  border-radius: 9999px;
  background-color: var(--bg-tertiary);
  color: var(--text-secondary);
  white-space: nowrap;
}

.gemma-model-desc {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin: 0 0 0.5rem;
  line-height: 1.35;
}

.gemma-model-actions {
  display: flex;
  gap: 0.375rem;
  align-items: center;
}

.gemma-active-label {
  display: inline-flex;
  align-items: center;
  gap: 0.125rem;
  font-size: 0.75rem;
  color: #34a853;
  font-weight: 600;
}

.btn-danger {
  color: #ea4335;
  border-color: #ea4335;
}

.btn-danger:hover:not(:disabled) {
  background-color: rgba(234, 67, 53, 0.1);
}

.gemma-download-done {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  border-radius: var(--radius-sm);
  font-size: 0.8125rem;
  margin-bottom: 0.75rem;
}

.success-banner {
  background-color: rgba(52, 168, 83, 0.08);
  border: 1px solid #34a853;
  color: #34a853;
}

.warning-banner {
  background-color: rgba(251, 188, 4, 0.08);
  border: 1px solid #fbbc04;
  color: #f9a825;
}

.gemma-no-model-hint {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  border-radius: var(--radius-sm);
  background-color: rgba(66, 133, 244, 0.08);
  border: 1px solid rgba(66, 133, 244, 0.25);
  font-size: 0.8125rem;
  color: var(--accent-color);
  margin-bottom: 0.75rem;
}

.gemma-no-model-hint .material-icons {
  font-size: 1rem;
  flex-shrink: 0;
}

.gemma-loading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 1.5rem;
  justify-content: center;
  color: var(--text-secondary);
  font-size: 0.875rem;
}

.gemma-loading .material-icons {
  font-size: 1.25rem;
  color: var(--accent-color);
}

.spinning {
  animation: spin 1.2s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.gemma-empty-state {
  text-align: center;
  padding: 1.5rem 1rem;
  color: var(--text-secondary);
}

.gemma-empty-state .material-icons {
  font-size: 2.5rem;
  opacity: 0.35;
  margin-bottom: 0.5rem;
  display: block;
}

.gemma-empty-state p {
  margin: 0 0 0.375rem 0;
  font-size: 0.875rem;
}

.gemma-empty-state .btn-primary {
  margin-top: 0.75rem;
}
</style>
