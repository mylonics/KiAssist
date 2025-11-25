<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import type { WizardQuestion } from '../types/pywebview';

// Props
const props = defineProps<{
  projectDir: string;
  projectName: string;
  visible: boolean;
}>();

// Emits
const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'saved', files: string[]): void;
}>();

// Wizard state
const currentStep = ref(0);
const isLoading = ref(false);
const error = ref<string>('');
const questions = ref<WizardQuestion[]>([]);
const answers = ref<Record<string, string>>({});
const synthesizedRequirements = ref<string>('');
const synthesizedTodo = ref<string>('');

// Initial questions (first two for refinement)
const INITIAL_QUESTIONS: WizardQuestion[] = [
  {
    id: 'objectives',
    category: 'General',
    question: 'What are the general requirements and objectives of this PCB project?',
    placeholder: 'Describe what this board should accomplish...',
    multiline: true
  },
  {
    id: 'known_parts',
    category: 'General',
    question: 'Are there any specific details or known parts that should be used?',
    placeholder: 'List any specific components, chips, or constraints...',
    multiline: true
  }
];

// Steps: initial questions, refined questions, review/generate
const steps = ['Project Overview', 'Detailed Requirements', 'Review & Generate'];

// Computed properties
const isInitialPhase = computed(() => currentStep.value === 0);
const isDetailedPhase = computed(() => currentStep.value === 1);
const isReviewPhase = computed(() => currentStep.value === 2);

const currentQuestions = computed(() => {
  if (isInitialPhase.value) {
    return INITIAL_QUESTIONS;
  }
  return questions.value;
});

const canProceed = computed(() => {
  if (isLoading.value) return false;
  
  if (isInitialPhase.value) {
    // At least objectives should be filled
    return (answers.value['objectives']?.trim().length ?? 0) > 10;
  }
  
  if (isDetailedPhase.value) {
    // At least 3 questions should be answered
    const answeredCount = questions.value.filter(q => 
      (answers.value[q.id]?.trim().length ?? 0) > 0
    ).length;
    return answeredCount >= 3;
  }
  
  return true;
});

const progress = computed(() => {
  return Math.round(((currentStep.value + 1) / steps.length) * 100);
});

const loadingMessage = computed(() => {
  if (isInitialPhase.value) {
    return 'Analyzing your requirements...';
  }
  if (isDetailedPhase.value) {
    return 'Generating your documents...';
  }
  return 'Saving files...';
});

// Methods
async function loadQuestions() {
  try {
    if (window.pywebview?.api) {
      const result = await window.pywebview.api.get_wizard_questions();
      if (result.success && result.questions) {
        // Skip the first two (they are initial questions)
        questions.value = result.questions.slice(2);
      }
    }
  } catch (err) {
    console.error('Error loading questions:', err);
  }
}

async function refineQuestions() {
  isLoading.value = true;
  error.value = '';
  
  try {
    if (window.pywebview?.api) {
      const initialAnswers = {
        objectives: answers.value['objectives'] || '',
        known_parts: answers.value['known_parts'] || ''
      };
      
      const result = await window.pywebview.api.refine_wizard_questions(initialAnswers);
      
      if (result.success && result.questions) {
        questions.value = result.questions;
      } else {
        error.value = result.error || 'Failed to refine questions';
        // Fall back to default questions
        await loadQuestions();
      }
    }
  } catch (err) {
    error.value = `Error: ${err}`;
    await loadQuestions();
  } finally {
    isLoading.value = false;
  }
}

async function synthesizeRequirements() {
  isLoading.value = true;
  error.value = '';
  
  try {
    if (window.pywebview?.api) {
      // Combine initial questions with refined questions
      const allQuestions = [...INITIAL_QUESTIONS, ...questions.value];
      
      const result = await window.pywebview.api.synthesize_requirements(
        allQuestions,
        answers.value,
        props.projectName
      );
      
      if (result.success) {
        synthesizedRequirements.value = result.requirements || '';
        synthesizedTodo.value = result.todo || '';
      } else {
        error.value = result.error || 'Failed to synthesize requirements';
      }
    }
  } catch (err) {
    error.value = `Error: ${err}`;
  } finally {
    isLoading.value = false;
  }
}

async function saveRequirements() {
  isLoading.value = true;
  error.value = '';
  
  try {
    if (window.pywebview?.api) {
      const result = await window.pywebview.api.save_requirements(
        props.projectDir,
        synthesizedRequirements.value,
        synthesizedTodo.value || undefined
      );
      
      if (result.success && result.saved_files) {
        emit('saved', result.saved_files);
      } else {
        error.value = result.error || 'Failed to save requirements';
      }
    }
  } catch (err) {
    error.value = `Error: ${err}`;
  } finally {
    isLoading.value = false;
  }
}

async function nextStep() {
  if (currentStep.value === 0) {
    // Refine questions based on initial answers
    await refineQuestions();
    currentStep.value = 1;
  } else if (currentStep.value === 1) {
    // Synthesize requirements
    await synthesizeRequirements();
    currentStep.value = 2;
  } else {
    // Save and close
    await saveRequirements();
  }
}

function prevStep() {
  if (currentStep.value > 0) {
    currentStep.value--;
  }
}

function close() {
  emit('close');
}

// Initialize
watch(() => props.visible, async (newVal) => {
  if (newVal) {
    currentStep.value = 0;
    answers.value = {};
    synthesizedRequirements.value = '';
    synthesizedTodo.value = '';
    error.value = '';
    await loadQuestions();
  }
}, { immediate: true });
</script>

<template>
  <div v-if="visible" class="wizard-overlay">
    <div class="wizard-modal">
      <!-- Header -->
      <div class="wizard-header">
        <h2>Project Requirements Wizard</h2>
        <button @click="close" class="close-btn" title="Close">
          <span class="material-icons">close</span>
        </button>
      </div>
      
      <!-- Progress -->
      <div class="progress-container">
        <div class="progress-steps">
          <div 
            v-for="(step, index) in steps" 
            :key="index"
            class="progress-step"
            :class="{ active: index === currentStep, completed: index < currentStep }"
          >
            <div class="step-number">{{ index + 1 }}</div>
            <div class="step-label">{{ step }}</div>
          </div>
        </div>
        <div class="progress-bar">
          <div class="progress-fill" :style="{ width: `${progress}%` }"></div>
        </div>
      </div>
      
      <!-- Content -->
      <div class="wizard-content">
        <!-- Error banner -->
        <div v-if="error" class="error-banner">
          <span class="material-icons">warning</span>
          <span>{{ error }}</span>
          <button @click="error = ''" class="dismiss-btn">
            <span class="material-icons">close</span>
          </button>
        </div>
        
        <!-- Loading state -->
        <div v-if="isLoading" class="loading-state">
          <div class="spinner"></div>
          <p>{{ loadingMessage }}</p>
        </div>
        
        <!-- Questions form -->
        <div v-else-if="!isReviewPhase" class="questions-form">
          <p class="section-intro">
            {{ isInitialPhase 
              ? 'Start by describing your project objectives and any known requirements.' 
              : 'Please provide details for the following categories. Leave blank any that don\'t apply.' 
            }}
          </p>
          
          <div 
            v-for="question in currentQuestions" 
            :key="question.id"
            class="question-group"
          >
            <label :for="question.id">
              <span class="category-badge">{{ question.category }}</span>
              {{ question.question }}
            </label>
            <textarea
              v-if="question.multiline"
              :id="question.id"
              v-model="answers[question.id]"
              :placeholder="question.placeholder"
              rows="3"
            ></textarea>
            <input
              v-else
              :id="question.id"
              v-model="answers[question.id]"
              :placeholder="question.placeholder"
              type="text"
            />
          </div>
        </div>
        
        <!-- Review phase -->
        <div v-else class="review-phase">
          <div class="document-preview">
            <h3>requirements.md</h3>
            <div class="preview-content">
              <pre>{{ synthesizedRequirements || 'No content generated' }}</pre>
            </div>
          </div>
          
          <div v-if="synthesizedTodo" class="document-preview">
            <h3>todo.md</h3>
            <div class="preview-content">
              <pre>{{ synthesizedTodo }}</pre>
            </div>
          </div>
          
          <p class="save-note">
            These files will be saved to: <code>{{ projectDir }}</code>
          </p>
        </div>
      </div>
      
      <!-- Footer -->
      <div class="wizard-footer">
        <button 
          v-if="currentStep > 0" 
          @click="prevStep" 
          class="btn-secondary"
          :disabled="isLoading"
        >
          <span class="material-icons">arrow_back</span>
          Back
        </button>
        <div class="spacer"></div>
        <button @click="close" class="btn-secondary" :disabled="isLoading">
          Cancel
        </button>
        <button 
          @click="nextStep" 
          class="btn-primary"
          :disabled="!canProceed || isLoading"
        >
          {{ isReviewPhase ? 'Save Requirements' : 'Continue' }}
          <span v-if="!isReviewPhase" class="material-icons">arrow_forward</span>
          <span v-else class="material-icons">save</span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wizard-overlay {
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

.wizard-modal {
  background-color: var(--bg-primary);
  border-radius: var(--radius-lg);
  width: 90%;
  max-width: 700px;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
  border: 1px solid var(--border-color);
}

.wizard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1.25rem 1.5rem;
  border-bottom: 1px solid var(--border-color);
}

.wizard-header h2 {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--text-primary);
}

.close-btn {
  padding: 0.375rem;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--text-secondary);
  transition: all 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.close-btn:hover {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}

/* Progress */
.progress-container {
  padding: 1rem 1.5rem;
  border-bottom: 1px solid var(--border-color);
  background-color: var(--bg-secondary);
}

.progress-steps {
  display: flex;
  justify-content: space-between;
  margin-bottom: 0.75rem;
}

.progress-step {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
}

.step-number {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background-color: var(--bg-tertiary);
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.8125rem;
  font-weight: 600;
  margin-bottom: 0.375rem;
  transition: all 0.2s ease;
}

.progress-step.active .step-number {
  background: linear-gradient(135deg, var(--accent-color) 0%, var(--accent-hover) 100%);
  color: white;
}

.progress-step.completed .step-number {
  background-color: #22c55e;
  color: white;
}

.step-label {
  font-size: 0.75rem;
  color: var(--text-secondary);
  text-align: center;
}

.progress-step.active .step-label {
  color: var(--accent-color);
  font-weight: 500;
}

.progress-bar {
  height: 4px;
  background-color: var(--bg-tertiary);
  border-radius: 2px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(135deg, var(--accent-color) 0%, var(--accent-hover) 100%);
  transition: width 0.3s ease;
}

/* Content */
.wizard-content {
  flex: 1;
  overflow-y: auto;
  padding: 1.5rem;
  min-height: 300px;
}

.section-intro {
  color: var(--text-secondary);
  margin-bottom: 1.25rem;
  font-size: 0.9375rem;
  line-height: 1.5;
}

.questions-form {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.question-group {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.question-group label {
  font-size: 0.875rem;
  color: var(--text-primary);
  font-weight: 500;
  line-height: 1.4;
}

.category-badge {
  display: inline-block;
  padding: 0.125rem 0.5rem;
  background-color: var(--bg-tertiary);
  color: var(--accent-color);
  border-radius: 0.75rem;
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-right: 0.5rem;
}

.question-group textarea,
.question-group input {
  padding: 0.75rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  font-family: inherit;
  font-size: 0.875rem;
  background-color: var(--bg-input);
  color: var(--text-primary);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
  resize: vertical;
}

.question-group textarea:focus,
.question-group input:focus {
  outline: none;
  border-color: var(--accent-color);
  box-shadow: 0 0 0 2px rgba(88, 101, 242, 0.15);
}

.question-group textarea::placeholder,
.question-group input::placeholder {
  color: var(--text-secondary);
  opacity: 0.7;
}

/* Loading state */
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 3rem;
  gap: 1rem;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 3px solid var(--border-color);
  border-top-color: var(--accent-color);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.loading-state p {
  color: var(--text-secondary);
  font-size: 0.9375rem;
}

/* Error banner */
.error-banner {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.875rem 1rem;
  background-color: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: var(--radius-md);
  color: #dc2626;
  font-size: 0.875rem;
  margin-bottom: 1rem;
}

.error-banner .material-icons {
  font-size: 1.25rem;
  flex-shrink: 0;
}

.dismiss-btn {
  margin-left: auto;
  padding: 0.25rem;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: #dc2626;
  display: flex;
  align-items: center;
  justify-content: center;
}

.dismiss-btn .material-icons {
  font-size: 1rem;
}

/* Review phase */
.review-phase {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.document-preview h3 {
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 0.5rem 0;
}

.preview-content {
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  padding: 1rem;
  max-height: 200px;
  overflow-y: auto;
}

.preview-content pre {
  margin: 0;
  font-family: 'SF Mono', 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 0.8125rem;
  line-height: 1.5;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

.save-note {
  font-size: 0.8125rem;
  color: var(--text-secondary);
  margin-top: 0.5rem;
}

.save-note code {
  background-color: var(--bg-tertiary);
  padding: 0.125rem 0.375rem;
  border-radius: var(--radius-sm);
  font-family: 'SF Mono', 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 0.75rem;
}

/* Footer */
.wizard-footer {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem 1.5rem;
  border-top: 1px solid var(--border-color);
  background-color: var(--bg-secondary);
}

.spacer {
  flex: 1;
}

.btn-primary,
.btn-secondary {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.625rem 1.25rem;
  border-radius: var(--radius-md);
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-primary {
  background: linear-gradient(135deg, var(--accent-color) 0%, var(--accent-hover) 100%);
  color: white;
  border: none;
}

.btn-primary:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-secondary {
  background: transparent;
  color: var(--text-primary);
  border: 1px solid var(--border-color);
}

.btn-secondary:hover:not(:disabled) {
  background-color: var(--bg-tertiary);
  border-color: var(--text-secondary);
}

.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-primary .material-icons,
.btn-secondary .material-icons {
  font-size: 1.125rem;
}

/* Scrollbar styling */
.wizard-content::-webkit-scrollbar,
.preview-content::-webkit-scrollbar {
  width: 8px;
}

.wizard-content::-webkit-scrollbar-track,
.preview-content::-webkit-scrollbar-track {
  background: transparent;
}

.wizard-content::-webkit-scrollbar-thumb,
.preview-content::-webkit-scrollbar-thumb {
  background: var(--border-color);
  border-radius: 4px;
}

.wizard-content::-webkit-scrollbar-thumb:hover,
.preview-content::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}
</style>
