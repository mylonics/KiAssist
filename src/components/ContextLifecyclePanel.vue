<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue';

const emit = defineEmits<{
  (e: 'context-questions-ready', questions: Array<{ question: string; suggestions: string[] }>): void;
  (e: 'context-complete', data: { requirements: string; synthesized_context: string }): void;
}>();

// Lifecycle state mirror
const state = ref<string>('idle');
const questions = ref<string[]>([]);
const currentQuestionIndex = ref(0);
const answers = ref<Array<{ question: string; answer: string }>>([]);
const requirements = ref('');
const synthesizedContext = ref('');
const rawContext = ref('');
const errorMsg = ref('');
const loading = ref(false);

// UI display toggles
const showRequirements = ref(true);
const showSynthesized = ref(false);
const showRawContext = ref(false);

// Progress: how many questions answered out of total
const questionProgress = computed(() => {
  if (questions.value.length === 0) return '';
  return `${currentQuestionIndex.value} / ${questions.value.length}`;
});

const isComplete = computed(() => state.value === 'done');
const isQuestioning = computed(() => state.value === 'questioning');
const isWorking = computed(() =>
  ['extracting', 'generating'].includes(state.value)
);

// Human-readable state labels
const stateLabel = computed(() => {
  switch (state.value) {
    case 'idle': return 'Ready';
    case 'extracting': return 'Extracting raw context...';
    case 'questioning': return 'Answering questions in chat';
    case 'generating': return 'Generating requirements & context...';
    case 'done': return 'Complete';
    default: return state.value;
  }
});

function applyState(data: any) {
  state.value = data.state ?? 'idle';
  questions.value = data.questions ?? [];
  currentQuestionIndex.value = data.current_question_index ?? 0;
  answers.value = data.answers ?? [];
  requirements.value = data.requirements ?? '';
  synthesizedContext.value = data.synthesized_context ?? '';
  rawContext.value = data.raw_context ?? '';
  errorMsg.value = data.error ?? '';
}

async function startLifecycle() {
  loading.value = true;
  errorMsg.value = '';
  questionsEmitted.value = false;
  try {
    const api = (window as any).pywebview?.api;
    if (!api) { errorMsg.value = 'Backend not available'; return; }
    const result = await api.start_context_lifecycle();
    if (result.success) {
      applyState(result);
      // The LLM call runs in the background — start polling to detect
      // when questions arrive or finalization completes.
      startPolling();
      // If questions are already ready (unlikely but possible on cache hit)
      checkAndEmitQuestions(result);
    } else {
      errorMsg.value = result.error || 'Failed to start context lifecycle';
    }
  } catch (e: any) {
    errorMsg.value = e.message || 'Unknown error';
  } finally {
    loading.value = false;
  }
}

/** Track whether we've already emitted questions for the current lifecycle. */
const questionsEmitted = ref(false);

function checkAndEmitQuestions(data: any) {
  if (questionsEmitted.value) {
    console.log('[ContextLifecycle] checkAndEmitQuestions skipped (already emitted)');
    return;
  }
  const qs = data.questions ?? [];
  console.log('[ContextLifecycle] checkAndEmitQuestions: state=', data.state, 'questions=', qs.length, 'questionsEmitted=', questionsEmitted.value);
  if (qs.length > 0 && data.state === 'questioning') {
    questionsEmitted.value = true;
    console.log('[ContextLifecycle] Emitting context-questions-ready, count:', qs.length, 'first Q:', qs[0]);
    emit('context-questions-ready', qs);
  }
  if (data.state === 'done') {
    emit('context-complete', {
      requirements: data.requirements ?? '',
      synthesized_context: data.synthesized_context ?? '',
    });
  }
}

async function loadCachedState() {
  try {
    const api = (window as any).pywebview?.api;
    if (!api) return;
    const result = await api.get_context_lifecycle_state();
    if (result.success && result.state !== 'idle') {
      applyState(result);
    }
  } catch {
    // ignore
  }
}

// Poll timer to sync state while Q&A happens in chat
let pollTimer: ReturnType<typeof setInterval> | null = null;

function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    try {
      const api = (window as any).pywebview?.api;
      if (!api) return;
      const result = await api.get_context_lifecycle_state();
      console.log('[ContextLifecycle] Poll result: state=', result.state, 'questions=', (result.questions ?? []).length, 'success=', result.success);
      if (result.success) {
        applyState(result);
        // Detect when questions arrive from background LLM call
        checkAndEmitQuestions(result);
        // Stop polling when done or idle
        if (result.state === 'done' || result.state === 'idle') {
          console.log('[ContextLifecycle] Stopping poll (state=', result.state, ')');
          stopPolling();
        }
      }
    } catch (e) {
      console.error('[ContextLifecycle] Poll error:', e);
    }
  }, 1000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

onMounted(() => {
  loadCachedState();
});

onUnmounted(() => {
  stopPolling();
});
</script>

<template>
  <div class="lifecycle-panel">
    <!-- Header -->
    <div class="lc-header">
      <span class="material-icons lc-header-icon">account_tree</span>
      <span class="lc-header-title">Project Context</span>
    </div>

    <!-- State Banner -->
    <div class="lc-state-banner" :class="state">
      <span class="material-icons lc-state-icon">
        {{ isComplete ? 'check_circle' : isQuestioning ? 'help_outline' : isWorking ? 'sync' : 'info' }}
      </span>
      <span class="lc-state-text">{{ stateLabel }}</span>
      <span v-if="isQuestioning" class="lc-progress">{{ questionProgress }}</span>
    </div>

    <!-- Error -->
    <div v-if="errorMsg" class="lc-error">
      <span class="material-icons">warning</span>
      <span>{{ errorMsg }}</span>
    </div>

    <!-- Idle / Start -->
    <div v-if="state === 'idle'" class="lc-idle">
      <p class="lc-hint">
        Build project context by extracting information from KiCad files
        and answering a few clarifying questions.
      </p>
      <button class="lc-btn primary" @click="startLifecycle" :disabled="loading">
        <span class="material-icons">play_arrow</span>
        Build Context
      </button>
    </div>

    <!-- Extracting / Generating spinner -->
    <div v-if="isWorking || loading" class="lc-loading">
      <span class="material-icons spinning">sync</span>
      <span>{{ stateLabel }}</span>
    </div>

    <!-- Question & Answer Flow — now routed through chat -->
    <div v-if="isQuestioning && !loading" class="lc-qa-status">
      <p class="lc-hint">
        Answer the questions in the chat panel.
      </p>
      <div class="lc-qa-progress-bar">
        <div class="lc-qa-progress-fill" :style="{ width: (questions.length ? (currentQuestionIndex / questions.length) * 100 : 0) + '%' }"></div>
      </div>
      <span class="lc-qa-progress-label">{{ questionProgress }} questions answered</span>
    </div>

    <!-- Results (done) -->
    <div v-if="isComplete" class="lc-results">
      <!-- Rebuild button -->
      <div class="lc-results-toolbar">
        <button class="lc-btn secondary small" @click="startLifecycle" :disabled="loading">
          <span class="material-icons">refresh</span>
          Rebuild
        </button>
      </div>

      <!-- Requirements -->
      <div class="lc-section" v-if="requirements">
        <button class="lc-section-toggle" @click="showRequirements = !showRequirements">
          <span class="material-icons">{{ showRequirements ? 'expand_less' : 'expand_more' }}</span>
          <span class="material-icons section-icon">description</span>
          Requirements
        </button>
        <pre v-if="showRequirements" class="lc-section-content">{{ requirements }}</pre>
      </div>

      <!-- Synthesized Context -->
      <div class="lc-section" v-if="synthesizedContext">
        <button class="lc-section-toggle" @click="showSynthesized = !showSynthesized">
          <span class="material-icons">{{ showSynthesized ? 'expand_less' : 'expand_more' }}</span>
          <span class="material-icons section-icon">auto_awesome</span>
          Synthesized Context
        </button>
        <pre v-if="showSynthesized" class="lc-section-content">{{ synthesizedContext }}</pre>
      </div>

      <!-- Raw Context -->
      <div class="lc-section" v-if="rawContext">
        <button class="lc-section-toggle" @click="showRawContext = !showRawContext">
          <span class="material-icons">{{ showRawContext ? 'expand_less' : 'expand_more' }}</span>
          <span class="material-icons section-icon">code</span>
          Raw Context
        </button>
        <pre v-if="showRawContext" class="lc-section-content">{{ rawContext }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lifecycle-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow-y: auto;
  padding: 0.5rem;
  gap: 0.5rem;
}

/* Header */
.lc-header {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.25rem 0;
  border-bottom: 1px solid var(--border-color);
}
.lc-header-icon {
  font-size: 1rem;
  color: var(--accent-color);
}
.lc-header-title {
  font-size: 0.8125rem;
  font-weight: 700;
  color: var(--text-primary);
}

/* State banner */
.lc-state-banner {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.375rem 0.5rem;
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
  font-weight: 600;
  background-color: var(--bg-tertiary);
  color: var(--text-secondary);
}
.lc-state-banner.done {
  background-color: rgba(34, 197, 94, 0.1);
  color: #22c55e;
}
.lc-state-banner.questioning {
  background-color: rgba(88, 101, 242, 0.1);
  color: var(--accent-color);
}
.lc-state-banner.extracting,
.lc-state-banner.generating {
  background-color: rgba(245, 158, 11, 0.1);
  color: #f59e0b;
}
.lc-state-icon {
  font-size: 0.9rem;
}
.lc-state-text {
  flex: 1;
}
.lc-progress {
  font-size: 0.6875rem;
  opacity: 0.8;
}

/* Error */
.lc-error {
  display: flex;
  align-items: flex-start;
  gap: 0.375rem;
  padding: 0.375rem 0.5rem;
  background-color: rgba(229, 62, 62, 0.1);
  border: 1px solid rgba(229, 62, 62, 0.2);
  border-radius: var(--radius-sm);
  color: #ef4444;
  font-size: 0.7rem;
}
.lc-error .material-icons {
  font-size: 0.875rem;
  flex-shrink: 0;
  margin-top: 1px;
}

/* Idle */
.lc-idle {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem 0.5rem;
}
.lc-hint {
  font-size: 0.75rem;
  color: var(--text-secondary);
  text-align: center;
  line-height: 1.5;
  margin: 0;
}

/* Loading */
.lc-loading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem;
  font-size: 0.75rem;
  color: var(--text-secondary);
}
.spinning {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Buttons */
.lc-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.35rem 0.75rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  font-size: 0.7rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}
.lc-btn.primary {
  background: linear-gradient(135deg, var(--accent-color), var(--accent-hover));
  color: #fff;
  border-color: transparent;
}
.lc-btn.primary:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}
.lc-btn.secondary {
  background-color: transparent;
  color: var(--text-secondary);
}
.lc-btn.secondary:hover:not(:disabled) {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}
.lc-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.lc-btn .material-icons {
  font-size: 0.85rem;
}
.lc-btn.small {
  padding: 0.25rem 0.5rem;
  font-size: 0.65rem;
}

/* Q&A status (questions routed through chat) */
.lc-qa-status {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 0.5rem;
}
.lc-qa-progress-bar {
  width: 100%;
  height: 4px;
  background-color: var(--bg-tertiary);
  border-radius: 2px;
  overflow: hidden;
}
.lc-qa-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent-color), var(--accent-hover));
  border-radius: 2px;
  transition: width 0.3s ease;
}
.lc-qa-progress-label {
  font-size: 0.65rem;
  color: var(--text-secondary);
}

/* Results */
.lc-results {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}
.lc-results-toolbar {
  display: flex;
  justify-content: flex-end;
}

/* Collapsible sections */
.lc-section {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.lc-section-toggle {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  width: 100%;
  padding: 0.375rem 0.5rem;
  background-color: var(--bg-secondary);
  border: none;
  cursor: pointer;
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--text-primary);
  text-align: left;
  transition: background-color 0.15s ease;
}
.lc-section-toggle:hover {
  background-color: var(--bg-tertiary);
}
.lc-section-toggle .material-icons {
  font-size: 0.9rem;
  color: var(--text-secondary);
}
.section-icon {
  font-size: 0.8rem !important;
  color: var(--accent-color) !important;
}
.lc-section-content {
  padding: 0.5rem;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 0.6rem;
  line-height: 1.4;
  color: var(--text-primary);
  background-color: var(--bg-primary);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  max-height: 300px;
  overflow-y: auto;
  border-top: 1px solid var(--border-color);
}
</style>
