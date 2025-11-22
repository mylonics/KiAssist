<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { invoke } from '@tauri-apps/api/core';

interface Message {
  id: string;
  text: string;
  sender: 'user' | 'assistant';
  timestamp: Date;
}

const messages = ref<Message[]>([]);
const inputMessage = ref('');
const selectedModel = ref('2.5-flash');
const hasApiKey = ref(false);
const showApiKeyPrompt = ref(false);
const apiKeyInput = ref('');
const isLoading = ref(false);

const availableModels = [
  { value: '2.5-flash', label: 'Gemini 2.5 Flash' },
  { value: '2.5-pro', label: 'Gemini 2.5 Pro' },
  { value: '3-flash', label: 'Gemini 3 Flash' },
  { value: '3-pro', label: 'Gemini 3 Pro' },
];

function generateMessageId(): string {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

async function checkApiKey() {
  try {
    hasApiKey.value = await invoke<boolean>('check_api_key');
    if (!hasApiKey.value) {
      showApiKeyPrompt.value = true;
    }
  } catch (error) {
    console.error('Error checking API key:', error);
  }
}

async function saveApiKey() {
  if (!apiKeyInput.value.trim()) return;
  
  try {
    await invoke('set_api_key', { apiKey: apiKeyInput.value.trim() });
    hasApiKey.value = true;
    showApiKeyPrompt.value = false;
    apiKeyInput.value = '';
  } catch (error) {
    console.error('Error saving API key:', error);
    alert('Failed to save API key. Please try again.');
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
    const response = await invoke<string>('send_message', { 
      message: messageText,
      model: selectedModel.value
    });
    
    // Add assistant response
    const assistantMessage: Message = {
      id: generateMessageId(),
      text: response,
      sender: 'assistant',
      timestamp: new Date(),
    };
    messages.value.push(assistantMessage);
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

onMounted(() => {
  checkApiKey();
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
      <div class="header-content">
        <div>
          <h2>KiAssist Chat</h2>
          <p class="subtitle">KiCAD AI Assistant powered by Gemini</p>
        </div>
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
  height: 100vh;
  max-width: 900px;
  margin: 0 auto;
  background-color: var(--bg-primary);
}

.chat-header {
  padding: 1.5rem;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
}

.header-controls {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.model-label {
  font-size: 0.9rem;
  font-weight: 500;
}

.model-select {
  padding: 0.5rem 0.75rem;
  background-color: rgba(255, 255, 255, 0.9);
  color: #2d2d2d;
  border: none;
  border-radius: 6px;
  font-size: 0.9rem;
  cursor: pointer;
  transition: background-color 0.2s;
}

.model-select:hover {
  background-color: white;
}

.settings-btn {
  padding: 0.5rem 0.75rem;
  background-color: rgba(255, 255, 255, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.2s;
  font-size: 1.2rem;
}

.settings-btn:hover {
  background-color: rgba(255, 255, 255, 0.3);
}

.chat-header h2 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
}

.subtitle {
  margin: 0.25rem 0 0 0;
  font-size: 0.875rem;
  opacity: 0.9;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 1.5rem;
  background-color: var(--bg-secondary);
}

.welcome-message {
  text-align: center;
  padding: 3rem 1rem;
  color: var(--text-secondary);
}

.welcome-message p:first-child {
  font-size: 2rem;
  margin-bottom: 1rem;
}

.hint {
  font-size: 0.95rem;
  opacity: 0.8;
}

.message {
  display: flex;
  margin-bottom: 1rem;
  animation: fadeIn 0.3s ease-in;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
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
  max-width: 70%;
  padding: 0.75rem 1rem;
  border-radius: 12px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
}

.message.user .message-content {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-bottom-right-radius: 4px;
}

.message.assistant .message-content {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
  border-bottom-left-radius: 4px;
}

.message-text {
  word-wrap: break-word;
  line-height: 1.5;
}

.message-time {
  font-size: 0.7rem;
  margin-top: 0.25rem;
  opacity: 0.7;
  text-align: right;
}

.chat-input {
  display: flex;
  gap: 0.75rem;
  padding: 1.25rem;
  background-color: var(--bg-primary);
  border-top: 1px solid var(--border-color);
}

textarea {
  flex: 1;
  padding: 0.75rem;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  font-family: inherit;
  font-size: 0.95rem;
  resize: none;
  background-color: var(--bg-input);
  color: var(--text-primary);
  transition: border-color 0.2s;
}

textarea:focus {
  outline: none;
  border-color: #667eea;
}

button {
  padding: 0.75rem 1.5rem;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 0.95rem;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.2s, transform 0.1s;
}

button:hover:not(:disabled) {
  opacity: 0.9;
  transform: translateY(-1px);
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
  background-color: rgba(0, 0, 0, 0.85);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  animation: fadeIn 0.3s ease-in;
}

.modal-content {
  background-color: var(--bg-primary);
  padding: 2rem;
  border-radius: 12px;
  max-width: 500px;
  width: 90%;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
  border: 1px solid var(--border-color);
}

.modal-content h3 {
  margin: 0 0 1rem 0;
  color: var(--text-primary);
}

.modal-description {
  color: var(--text-secondary);
  margin-bottom: 1.5rem;
  line-height: 1.5;
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
  padding: 0.75rem;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  font-family: inherit;
  font-size: 1rem;
  background-color: var(--bg-input);
  color: var(--text-primary);
  margin-bottom: 1.5rem;
}

.api-key-input:focus {
  outline: none;
  border-color: #667eea;
}

.modal-actions {
  display: flex;
  gap: 0.75rem;
  justify-content: flex-end;
}

.btn-primary {
  padding: 0.75rem 1.5rem;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 0.95rem;
  font-weight: 500;
  cursor: pointer;
}

.btn-secondary {
  padding: 0.75rem 1.5rem;
  background: transparent;
  color: var(--text-primary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  font-size: 0.95rem;
  font-weight: 500;
  cursor: pointer;
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
  gap: 0.4rem;
  padding: 0.75rem 1rem;
  background-color: var(--bg-tertiary);
  border-radius: 12px;
  border-bottom-left-radius: 4px;
}

.typing-dots span {
  width: 8px;
  height: 8px;
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
    transform: translateY(-10px);
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
  background: #555;
}

/* Default color variables (light mode) */
:root {
  --bg-primary: #ffffff;
  --bg-secondary: #f5f5f5;
  --bg-tertiary: #e8e8e8;
  --bg-input: #ffffff;
  --text-primary: #2d2d2d;
  --text-secondary: #666666;
  --border-color: #d0d0d0;
}

/* Dark mode support */
@media (prefers-color-scheme: dark) {
  :root {
    --bg-primary: #1a1a1a;
    --bg-secondary: #2d2d2d;
    --bg-tertiary: #3a3a3a;
    --bg-input: #2d2d2d;
    --text-primary: #e4e4e4;
    --text-secondary: #a0a0a0;
    --border-color: #404040;
  }
}
</style>
