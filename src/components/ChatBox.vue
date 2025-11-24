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
          <span class="error-icon">⚠️</span>
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
          ⚙️
        </button>
      </div>
    </div>
    
    <div class="chat-messages">
      <div v-if="messages.length === 0" class="welcome-message">
        <p>👋 Welcome to KiAssist!</p>
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
              {{ copiedMessageId === message.id ? 'Copied' : 'Copy' }}
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
  padding: 0.5rem 0.75rem;
  background-color: var(--bg-primary);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.header-controls {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.model-label {
  font-size: 0.8rem;
  color: var(--text-secondary);
}

.model-select {
  padding: 0.35rem 0.5rem;
  background-color: var(--bg-input);
  color: var(--text-primary);
  border: 1px solid var(--border-color);
  border-radius: 3px;
  font-size: 0.8rem;
  cursor: pointer;
}

.model-select:hover {
  border-color: var(--text-secondary);
}

.model-select:focus {
  outline: none;
  border-color: #667eea;
}

.settings-btn {
  padding: 0.35rem 0.5rem;
  background-color: var(--bg-input);
  border: 1px solid var(--border-color);
  border-radius: 3px;
  cursor: pointer;
  font-size: 1rem;
}

.settings-btn:hover {
  background-color: var(--bg-secondary);
  border-color: var(--text-secondary);
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 1rem;
  background-color: var(--bg-secondary);
  min-height: 0;
}

.welcome-message {
  text-align: center;
  padding: 2rem 1rem;
  color: var(--text-secondary);
}

.welcome-message p:first-child {
  font-size: 1.25rem;
  margin-bottom: 0.5rem;
}

.hint {
  font-size: 0.8rem;
  opacity: 0.8;
}

.message {
  display: flex;
  margin-bottom: 0.75rem;
  animation: fadeIn 0.2s ease-in;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(5px);
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
  padding: 0.5rem 0.75rem;
  border-radius: 3px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
  position: relative;
}

.message-header {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 0.2rem;
}

.copy-btn {
  padding: 0.15rem 0.35rem;
  background: rgba(0, 0, 0, 0.08);
  border: none;
  border-radius: 2px;
  font-size: 0.7rem;
  cursor: pointer;
  min-width: 2rem;
  height: 1.25rem;
  display: flex;
  align-items: center;
  justify-content: center;
}

.copy-btn:hover {
  background: rgba(0, 0, 0, 0.15);
}

.message.user .copy-btn {
  background: rgba(255, 255, 255, 0.2);
}

.message.user .copy-btn:hover {
  background: rgba(255, 255, 255, 0.3);
}

.message.user .message-content {
  background-color: #667eea;
  color: white;
}

.message.assistant .message-content {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}

.message-text {
  word-wrap: break-word;
  word-break: break-word;
  overflow-wrap: break-word;
  line-height: 1.4;
  user-select: text;
  -webkit-user-select: text;
  -moz-user-select: text;
  -ms-user-select: text;
  cursor: text;
  max-width: 100%;
  font-size: 0.9rem;
}

.message-time {
  font-size: 0.65rem;
  margin-top: 0.2rem;
  opacity: 0.6;
  text-align: right;
}

.chat-input {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem;
  background-color: var(--bg-primary);
  border-top: 1px solid var(--border-color);
  flex-shrink: 0;
}

textarea {
  flex: 1;
  padding: 0.5rem;
  border: 1px solid var(--border-color);
  border-radius: 3px;
  font-family: inherit;
  font-size: 0.85rem;
  resize: none;
  background-color: var(--bg-input);
  color: var(--text-primary);
}

textarea:focus {
  outline: none;
  border-color: #667eea;
}

button {
  padding: 0.5rem 1rem;
  background-color: #667eea;
  color: white;
  border: none;
  border-radius: 3px;
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;
}

button:hover:not(:disabled) {
  background-color: #5a6fd6;
}

button:active:not(:disabled) {
  background-color: #4e5fc4;
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
  background-color: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background-color: var(--bg-primary, #ffffff);
  padding: 1.5rem;
  border-radius: 3px;
  max-width: 450px;
  width: 90%;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
  border: 1px solid var(--border-color);
}

.modal-content h3 {
  margin: 0 0 0.75rem 0;
  color: var(--text-primary);
  font-size: 1rem;
}

.modal-description {
  color: var(--text-secondary);
  margin-bottom: 1rem;
  line-height: 1.4;
  font-size: 0.85rem;
}

.modal-description a {
  color: #667eea;
  text-decoration: none;
}

.modal-description a:hover {
  text-decoration: underline;
}

.api-key-input {
  width: 100%;
  padding: 0.5rem;
  border: 1px solid var(--border-color);
  border-radius: 3px;
  font-family: inherit;
  font-size: 0.85rem;
  background-color: var(--bg-input);
  color: var(--text-primary);
  margin-bottom: 0.75rem;
}

.api-key-input:focus {
  outline: none;
  border-color: #667eea;
}

.error-banner {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  margin-bottom: 0.75rem;
  background-color: #fee;
  border: 1px solid #fcc;
  border-radius: 3px;
  color: #c33;
  font-size: 0.8rem;
}

.error-icon {
  font-size: 1rem;
  flex-shrink: 0;
}

.modal-actions {
  display: flex;
  gap: 0.5rem;
  justify-content: flex-end;
}

.btn-primary {
  padding: 0.5rem 1rem;
  background-color: #667eea;
  color: white;
  border: none;
  border-radius: 3px;
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;
}

.btn-primary:hover:not(:disabled) {
  background-color: #5a6fd6;
}

.btn-secondary {
  padding: 0.5rem 1rem;
  background: transparent;
  color: var(--text-primary);
  border: 1px solid var(--border-color);
  border-radius: 3px;
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;
}

.btn-secondary:hover {
  background-color: var(--bg-secondary);
}

/* Loading indicator */
.loading-indicator {
  display: flex;
  justify-content: flex-start;
  margin-bottom: 0.75rem;
}

.typing-dots {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.5rem 0.75rem;
  background-color: var(--bg-tertiary);
  border-radius: 3px;
}

.typing-dots span {
  width: 6px;
  height: 6px;
  background-color: var(--text-secondary);
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
    opacity: 0.7;
  }
  30% {
    transform: translateY(-6px);
    opacity: 1;
  }
}

/* Scrollbar styling */
.chat-messages::-webkit-scrollbar {
  width: 8px;
}

.chat-messages::-webkit-scrollbar-track {
  background: var(--bg-primary);
}

.chat-messages::-webkit-scrollbar-thumb {
  background: #888;
  border-radius: 4px;
}

.chat-messages::-webkit-scrollbar-thumb:hover {
  background: #666;
}
</style>
