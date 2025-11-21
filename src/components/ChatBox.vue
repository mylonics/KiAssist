<script setup lang="ts">
import { ref } from 'vue';
import { invoke } from '@tauri-apps/api/core';

interface Message {
  id: number;
  text: string;
  sender: 'user' | 'assistant';
  timestamp: Date;
}

const messages = ref<Message[]>([]);
const inputMessage = ref('');
let messageIdCounter = 0;

async function sendMessage() {
  if (!inputMessage.value.trim()) return;

  // Add user message
  const userMessage: Message = {
    id: messageIdCounter++,
    text: inputMessage.value,
    sender: 'user',
    timestamp: new Date(),
  };
  messages.value.push(userMessage);

  const messageText = inputMessage.value;
  inputMessage.value = '';

  // Call backend to echo the message
  try {
    const echoResponse = await invoke<string>('echo_message', { message: messageText });
    
    // Add assistant response
    const assistantMessage: Message = {
      id: messageIdCounter++,
      text: echoResponse,
      sender: 'assistant',
      timestamp: new Date(),
    };
    messages.value.push(assistantMessage);
  } catch (error) {
    console.error('Error echoing message:', error);
  }
}

function handleKeyPress(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
}
</script>

<template>
  <div class="chat-container">
    <div class="chat-header">
      <h2>NewKiAssist Chat</h2>
      <p class="subtitle">KiCAD AI Assistant</p>
    </div>
    
    <div class="chat-messages">
      <div v-if="messages.length === 0" class="welcome-message">
        <p>👋 Welcome to NewKiAssist!</p>
        <p class="hint">Type a message below to get started. I'll echo your messages back to you.</p>
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
    </div>
    
    <div class="chat-input">
      <textarea
        v-model="inputMessage"
        @keypress="handleKeyPress"
        placeholder="Type your message here... (Press Enter to send)"
        rows="2"
      />
      <button @click="sendMessage" :disabled="!inputMessage.trim()">
        Send
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

/* Light mode */
@media (prefers-color-scheme: light) {
  :root {
    --bg-primary: #ffffff;
    --bg-secondary: #f5f5f5;
    --bg-tertiary: #e8e8e8;
    --bg-input: #ffffff;
    --text-primary: #2d2d2d;
    --text-secondary: #666666;
    --border-color: #d0d0d0;
  }
}
</style>
