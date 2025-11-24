<script setup lang="ts">
import { ref, onMounted } from 'vue';
import '../types/pywebview';

interface Message {
  id: string;
  text: string;
  sender: 'user' | 'assistant';
  timestamp: Date;
}

const copiedMessageId = ref<string | null>(null);

const messages = ref<Message[]>([]);
const inputMessage = ref('');
const selectedModel = ref('2.5-flash');
const hasApiKey = ref(false);
const showApiKeyPrompt = ref(false);
const apiKeyInput = ref('');
const isLoading = ref(false);
const apiKeyWarning = ref<string>('');

const availableModels = [
  { value: '2.5-flash', label: 'Gemini 2.5 Flash' },
  { value: '2.5-pro', label: 'Gemini 2.5 Pro' },
  { value: '3-flash', label: 'Gemini 3.0 Flash' },
  { value: '3-pro', label: 'Gemini 3.0 Pro' },
];

function generateMessageId(): string {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

async function checkApiKey() {
  try {
    if (window.pywebview?.api) {
      console.log('[UI] Checking API key...');
      hasApiKey.value = await window.pywebview.api.check_api_key();
      console.log('[UI] Has API key:', hasApiKey.value);
      if (!hasApiKey.value) {
        showApiKeyPrompt.value = true;
      }
    } else {
      console.error('[UI] pywebview API not available');
    }
  } catch (error) {
    console.error('[UI] Error checking API key:', error);
  }
}

// Wait for pywebview API to be ready before checking API key
async function waitForPywebviewAndCheckApiKey() {
  console.log('[UI] Waiting for pywebview API...');
  
  // Try up to 10 times with 100ms delays
  for (let i = 0; i < 10; i++) {
    if (window.pywebview?.api) {
      console.log('[UI] pywebview API ready!');
      await checkApiKey();
      return;
    }
    console.log(`[UI] Waiting for API... attempt ${i + 1}/10`);
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  
  console.error('[UI] pywebview API not available after waiting');
  // Still show the API key prompt since we need it
  showApiKeyPrompt.value = true;
}

const apiKeyError = ref<string>('');

async function saveApiKey() {
  if (!apiKeyInput.value.trim()) return;
  
  apiKeyError.value = ''; // Clear previous errors
  apiKeyWarning.value = ''; // Clear previous warnings
  
  const trimmedKey = apiKeyInput.value.trim();
  
  // Validate API key format
  if (trimmedKey.length < 30) {
    apiKeyError.value = 'API key seems too short. Gemini API keys are typically 39 characters long.';
    return;
  }
  
  if (!trimmedKey.startsWith('AIza')) {
    apiKeyError.value = 'This doesn\'t look like a valid Gemini API key. Keys should start with "AIza". Get your key from https://aistudio.google.com/apikey';
    return;
  }
  
  try {
    if (window.pywebview?.api) {
      console.log('[UI] Saving API key...');
      const result = await window.pywebview.api.set_api_key(trimmedKey);
      console.log('[UI] Save result:', result);
      
      if (result.success) {
        hasApiKey.value = true;
        showApiKeyPrompt.value = false;
        apiKeyInput.value = '';
        console.log('[UI] API key saved successfully!');
        
        // Show warning as a message if there was one
        if (result.warning) {
          apiKeyWarning.value = result.warning;
          console.warn('[UI] Save warning:', result.warning);
          // Also add a message to inform the user
          const warningMessage: Message = {
            id: generateMessageId(),
            text: `Note: ${result.warning}`,
            sender: 'assistant',
            timestamp: new Date(),
          };
          messages.value.push(warningMessage);
        }
      } else {
        console.error('[UI] Failed to save API key:', result.error);
        apiKeyError.value = result.error || 'Unknown error occurred';
      }
    } else {
      const errorMsg = 'Application backend not available. Please restart the application.';
      console.error('[UI]', errorMsg);
      apiKeyError.value = errorMsg;
    }
  } catch (error) {
    console.error('[UI] Error saving API key:', error);
    apiKeyError.value = 'Failed to save API key. Please try again.';
  }
}

async function sendMessage() {
  if (!inputMessage.value.trim()) return;
  
  if (!hasApiKey.value) {
    showApiKeyPrompt.value = true;
    return;
  }

  // Add user message
  const userMessage: Message = {
    id: generateMessageId(),
    text: inputMessage.value,
    sender: 'user',
    timestamp: new Date(),
  };
  messages.value.push(userMessage);

  const messageText = inputMessage.value;
  inputMessage.value = '';
  isLoading.value = true;

  // Call backend to send message to Gemini
  try {
    if (window.pywebview?.api) {
      const result = await window.pywebview.api.send_message(messageText, selectedModel.value);
      
      if (result.success && result.response) {
        // Add assistant response
        const assistantMessage: Message = {
          id: generateMessageId(),
          text: result.response,
          sender: 'assistant',
          timestamp: new Date(),
        };
        messages.value.push(assistantMessage);
      } else {
        // Add error message to UI
        const errorMessage: Message = {
          id: generateMessageId(),
          text: `Sorry, I encountered an error: ${result.error || 'Unknown error'}. Please check your API key and try again.`,
          sender: 'assistant',
          timestamp: new Date(),
        };
        messages.value.push(errorMessage);
      }
    } else {
      const errorMessage: Message = {
        id: generateMessageId(),
        text: 'Application backend not available. Please restart the application.',
        sender: 'assistant',
        timestamp: new Date(),
      };
      messages.value.push(errorMessage);
    }
  } catch (error) {
    console.error('Error sending message:', error);
    // Add error message to UI
    const errorMessage: Message = {
      id: generateMessageId(),
      text: `Sorry, I encountered an error: ${error}. Please check your API key and try again.`,
      sender: 'assistant',
      timestamp: new Date(),
    };
    messages.value.push(errorMessage);
  } finally {
    isLoading.value = false;
  }
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
    setTimeout(() => {
      copiedMessageId.value = null;
    }, 2000);
  } catch (error) {
    console.error('Failed to copy message:', error);
  }
}

onMounted(() => {
  // Use the wait function to ensure pywebview API is ready
  waitForPywebviewAndCheckApiKey();
});
</script>

<template>
  <div class="chat-container">
    <!-- API Key Prompt Modal -->
    <div v-if="showApiKeyPrompt" class="modal-overlay">
      <div class="modal-content">
        <h3>Configure Gemini API Key</h3>
        <p class="modal-description">
          Please enter your Google Gemini API key to enable AI-powered chat functionality.
          You can get your API key from the 
          <a href="https://makersuite.google.com/app/apikey" target="_blank">Google AI Studio</a>.
        </p>
        <input
          v-model="apiKeyInput"
          type="password"
          placeholder="Enter your Gemini API key..."
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

    <div class="chat-header">
      <div class="header-controls">
        <label for="model-select" class="model-label">Model:</label>
        <select id="model-select" v-model="selectedModel" class="model-select">
          <option v-for="model in availableModels" :key="model.value" :value="model.value">
            {{ model.label }}
          </option>
        </select>
        <button @click="showApiKeyPrompt = true" class="settings-btn" title="Configure API Key">
          <span class="material-icons">settings</span>
        </button>
      </div>
    </div>
    
    <div class="chat-messages">
      <div v-if="messages.length === 0" class="welcome-message">
        <p>Welcome to KiAssist!</p>
        <p class="hint">Ask me anything about KiCAD or PCB design. Powered by Google Gemini.</p>
      </div>
      
      <div
        v-for="message in messages"
        :key="message.id"
        :class="['message', message.sender]"
      >
        <div class="message-content">
          <div class="message-header">
            <button 
              @click="copyMessage(message.id, message.text)"
              class="copy-btn"
              :title="copiedMessageId === message.id ? 'Copied!' : 'Copy message'"
            >
              <span class="material-icons">{{ copiedMessageId === message.id ? 'check' : 'content_copy' }}</span>
            </button>
          </div>
          <div class="message-text">{{ message.text }}</div>
          <div class="message-time">
            {{ message.timestamp.toLocaleTimeString() }}
          </div>
        </div>
      </div>
      
      <div v-if="isLoading" class="loading-indicator">
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
      <button @click="sendMessage" :disabled="!inputMessage.trim() || isLoading">
        {{ isLoading ? 'Sending...' : 'Send' }}
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

.model-label {
  font-size: 0.8125rem;
  color: var(--text-secondary);
  font-weight: 500;
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

.settings-btn {
  padding: 0.375rem;
  background-color: var(--bg-input);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.settings-btn .material-icons {
  font-size: 1.25rem;
  color: var(--text-secondary);
}

.settings-btn:hover {
  background-color: var(--bg-tertiary);
  border-color: var(--accent-color);
}

.settings-btn:hover .material-icons {
  color: var(--accent-color);
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

.welcome-message p:first-child {
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
  margin-bottom: 1rem;
  animation: fadeIn 0.2s ease-out;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.message.user {
  justify-content: flex-end;
}

.message.assistant {
  justify-content: flex-start;
}

.message-content {
  max-width: min(75%, 700px);
  padding: 0.75rem 1rem;
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  position: relative;
}

.message-header {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 0.25rem;
}

.copy-btn {
  padding: 0.25rem;
  background: rgba(0, 0, 0, 0.06);
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.copy-btn .material-icons {
  font-size: 1rem;
  color: var(--text-secondary);
}

.copy-btn:hover {
  background: rgba(0, 0, 0, 0.12);
}

.message.user .copy-btn {
  background: rgba(255, 255, 255, 0.15);
}

.message.user .copy-btn .material-icons {
  color: rgba(255, 255, 255, 0.9);
}

.message.user .copy-btn:hover {
  background: rgba(255, 255, 255, 0.25);
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
  -moz-user-select: text;
  -ms-user-select: text;
  cursor: text;
  max-width: 100%;
  font-size: 0.9375rem;
}

.message-time {
  font-size: 0.6875rem;
  margin-top: 0.375rem;
  opacity: 0.6;
  text-align: right;
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
  padding: 0.625rem 1.25rem;
  background: linear-gradient(135deg, var(--accent-color) 0%, var(--accent-hover) 100%);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 0.9375rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  box-shadow: var(--shadow-sm);
}

button:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}

button:active:not(:disabled) {
  transform: translateY(0);
}

button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
  background-color: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: var(--radius-md);
  color: #dc2626;
  font-size: 0.875rem;
}

.error-icon {
  font-size: 1.25rem;
  flex-shrink: 0;
  color: #dc2626;
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
</style>
