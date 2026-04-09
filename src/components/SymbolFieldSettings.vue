<script setup lang="ts">
import { ref, onMounted } from 'vue';

// -----------------------------------------------------------------------
// Types
// -----------------------------------------------------------------------

interface FieldDefault {
  key: string;
  enabled: boolean;
  editing: boolean;
}

// -----------------------------------------------------------------------
// State
// -----------------------------------------------------------------------

const collapsed = ref(true);
const fields = ref<FieldDefault[]>([]);
const loading = ref(false);
const saving = ref(false);
const error = ref('');
const dirty = ref(false);
const newFieldKey = ref('');

// -----------------------------------------------------------------------
// API
// -----------------------------------------------------------------------

function getApi() {
  return (window as any).pywebview?.api ?? null;
}

async function loadDefaults() {
  const api = getApi();
  if (!api) return;
  loading.value = true;
  error.value = '';
  try {
    const r = await api.get_symbol_field_defaults();
    if (r?.success && r.fields) {
      fields.value = r.fields.map((f: any) => ({
        key: f.key || '',
        enabled: f.enabled === 'true' || f.enabled === true,
        editing: false,
      }));
    }
  } catch (e: any) {
    error.value = e.message || 'Failed to load field defaults';
  } finally {
    loading.value = false;
  }
}

async function saveDefaults() {
  const api = getApi();
  if (!api) return;
  saving.value = true;
  error.value = '';
  try {
    const payload = fields.value.map(f => ({
      key: f.key,
      enabled: f.enabled ? 'true' : 'false',
    }));
    const r = await api.set_symbol_field_defaults(payload);
    if (r?.success) {
      dirty.value = false;
    } else {
      error.value = r?.error || 'Save failed';
    }
  } catch (e: any) {
    error.value = e.message || 'Save failed';
  } finally {
    saving.value = false;
  }
}

// -----------------------------------------------------------------------
// Field manipulation
// -----------------------------------------------------------------------

function toggleField(idx: number) {
  fields.value[idx].enabled = !fields.value[idx].enabled;
  dirty.value = true;
}

function removeField(idx: number) {
  fields.value.splice(idx, 1);
  dirty.value = true;
}

function moveField(idx: number, direction: -1 | 1) {
  const target = idx + direction;
  if (target < 0 || target >= fields.value.length) return;
  const temp = fields.value[idx];
  fields.value[idx] = fields.value[target];
  fields.value[target] = temp;
  dirty.value = true;
}

function startEditKey(idx: number) {
  fields.value[idx].editing = true;
}

function finishEditKey(idx: number) {
  fields.value[idx].key = fields.value[idx].key.trim();
  fields.value[idx].editing = false;
  dirty.value = true;
}

function addField() {
  const key = newFieldKey.value.trim();
  if (!key) return;
  // Don't add duplicates
  if (fields.value.some(f => f.key.toLowerCase() === key.toLowerCase())) {
    error.value = `Field "${key}" already exists`;
    return;
  }
  fields.value.push({ key, enabled: true, editing: false });
  newFieldKey.value = '';
  dirty.value = true;
}

// -----------------------------------------------------------------------
// Lifecycle
// -----------------------------------------------------------------------

onMounted(() => {
  loadDefaults();
});
</script>

<template>
  <div class="field-settings">
    <div class="settings-header" @click="collapsed = !collapsed">
      <span class="material-icons settings-icon">settings</span>
      <span class="settings-title">Symbol Field Defaults</span>
      <span v-if="dirty" class="unsaved-dot" title="Unsaved changes"></span>
      <span class="material-icons collapse-chevron">{{ collapsed ? 'expand_more' : 'expand_less' }}</span>
    </div>

    <div v-if="!collapsed" class="settings-body">
      <p class="settings-hint">
        Configure which fields appear by default on imported symbols and their display names.
      </p>

      <div v-if="loading" class="empty-hint">
        <span class="material-icons spinning" style="font-size: 0.9rem">autorenew</span>
        Loading…
      </div>

      <template v-else>
        <!-- Field rows -->
        <div class="field-list">
          <div
            v-for="(f, idx) in fields"
            :key="idx"
            :class="['field-row', { disabled: !f.enabled }]"
          >
            <button
              class="field-toggle"
              @click="toggleField(idx)"
              :title="f.enabled ? 'Disable field' : 'Enable field'"
            >
              <span class="material-icons">{{ f.enabled ? 'check_box' : 'check_box_outline_blank' }}</span>
            </button>

            <!-- Editable key name -->
            <input
              v-if="f.editing"
              v-model="f.key"
              class="field-key-input"
              @blur="finishEditKey(idx)"
              @keydown.enter="finishEditKey(idx)"
              @keydown.escape="finishEditKey(idx)"
              autofocus
            />
            <span
              v-else
              class="field-key"
              @dblclick="startEditKey(idx)"
              title="Double-click to rename"
            >{{ f.key }}</span>

            <!-- Reorder buttons -->
            <button
              class="field-action-btn"
              @click="moveField(idx, -1)"
              :disabled="idx === 0"
              title="Move up"
            >
              <span class="material-icons">arrow_drop_up</span>
            </button>
            <button
              class="field-action-btn"
              @click="moveField(idx, 1)"
              :disabled="idx === fields.length - 1"
              title="Move down"
            >
              <span class="material-icons">arrow_drop_down</span>
            </button>

            <!-- Remove -->
            <button
              class="field-action-btn remove-btn"
              @click="removeField(idx)"
              title="Remove field"
            >
              <span class="material-icons">close</span>
            </button>
          </div>
        </div>

        <!-- Add new field -->
        <div class="add-field-row">
          <input
            v-model="newFieldKey"
            class="text-input"
            placeholder="New field name…"
            @keydown.enter="addField"
          />
          <button
            class="action-btn secondary"
            @click="addField"
            :disabled="!newFieldKey.trim()"
            title="Add field"
          >
            <span class="material-icons">add</span>
          </button>
        </div>

        <!-- Save -->
        <button
          class="action-btn primary full-width"
          @click="saveDefaults"
          :disabled="saving || !dirty"
        >
          <span class="material-icons">save</span>
          {{ saving ? 'Saving…' : (dirty ? 'Save Defaults' : 'Saved') }}
        </button>

        <!-- Error -->
        <div v-if="error" class="notice error" style="margin-top: 0.25rem">
          <span class="material-icons">error_outline</span>
          {{ error }}
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.field-settings {
  font-size: 0.8rem;
}

.settings-header {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.45rem 0.6rem;
  cursor: pointer;
  user-select: none;
  font-size: 0.72rem;
  font-weight: 700;
  color: var(--text-primary);
  background-color: var(--bg-secondary);
  border-top: 1px solid var(--border-color);
}

.settings-header:hover {
  background-color: var(--bg-tertiary);
}

.settings-icon {
  font-size: 0.9rem;
  color: var(--accent-color);
}

.settings-title {
  flex: 1;
}

.unsaved-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: #f39c12;
  flex-shrink: 0;
}

.collapse-chevron {
  font-size: 0.9rem;
  color: var(--text-secondary);
}

.settings-body {
  padding: 0.5rem 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.settings-hint {
  font-size: 0.68rem;
  color: var(--text-secondary);
  line-height: 1.4;
  margin: 0;
}

.field-list {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.field-row {
  display: flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.2rem 0.35rem;
  background: var(--bg-input);
  border-bottom: 1px solid var(--border-color);
  transition: opacity 0.15s;
}

.field-row:last-child {
  border-bottom: none;
}

.field-row.disabled {
  opacity: 0.5;
}

.field-toggle {
  display: flex;
  align-items: center;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--accent-color);
  padding: 0;
  flex-shrink: 0;
}

.field-toggle .material-icons {
  font-size: 1rem;
}

.field-key {
  flex: 1;
  font-weight: 600;
  font-size: 0.72rem;
  color: var(--text-primary);
  cursor: default;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 0.1rem 0.2rem;
  border-radius: var(--radius-sm);
}

.field-key:hover {
  background-color: var(--bg-tertiary);
}

.field-key-input {
  flex: 1;
  font-weight: 600;
  font-size: 0.72rem;
  color: var(--text-primary);
  border: 1px solid var(--accent-color);
  border-radius: var(--radius-sm);
  background: var(--bg-input);
  padding: 0.1rem 0.2rem;
  outline: none;
  min-width: 0;
}

.field-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--text-secondary);
  padding: 0;
  width: 1.1rem;
  height: 1.1rem;
  flex-shrink: 0;
  border-radius: 2px;
  transition: all 0.12s;
}

.field-action-btn:hover:not(:disabled) {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}

.field-action-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.field-action-btn .material-icons {
  font-size: 0.9rem;
}

.field-action-btn.remove-btn:hover:not(:disabled) {
  background-color: color-mix(in srgb, #e74c3c 15%, transparent);
  color: #e74c3c;
}

.add-field-row {
  display: flex;
  gap: 0.3rem;
  align-items: center;
}

/* Shared utility classes from SymbolImporter */
.text-input {
  flex: 1;
  min-width: 0;
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background-color: var(--bg-input);
  color: var(--text-primary);
  font-size: 0.75rem;
  outline: none;
  transition: border-color 0.15s;
}

.text-input:focus {
  border-color: var(--accent-color);
}

.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.3rem 0.6rem;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  font-size: 0.72rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
  flex-shrink: 0;
}

.action-btn .material-icons {
  font-size: 0.9rem;
}

.action-btn.primary {
  background-color: var(--accent-color);
  color: #fff;
}

.action-btn.primary:hover:not(:disabled) {
  background-color: var(--accent-hover);
}

.action-btn.secondary {
  background-color: var(--bg-tertiary);
  color: var(--text-secondary);
  border-color: var(--border-color);
  padding: 0.3rem 0.4rem;
}

.action-btn.secondary:hover:not(:disabled) {
  background-color: var(--bg-secondary);
  color: var(--text-primary);
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.action-btn.full-width {
  width: 100%;
  justify-content: center;
}

.empty-hint {
  color: var(--text-secondary);
  font-size: 0.72rem;
  text-align: center;
  padding: 0.5rem;
}

.notice {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  padding: 0.4rem 0.5rem;
  border-radius: var(--radius-sm);
  font-size: 0.72rem;
  line-height: 1.4;
}

.notice .material-icons {
  font-size: 1rem;
  flex-shrink: 0;
}

.notice.error {
  background-color: color-mix(in srgb, #e74c3c 15%, transparent);
  color: #e74c3c;
  border: 1px solid color-mix(in srgb, #e74c3c 30%, transparent);
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.spinning {
  animation: spin 1s linear infinite;
}
</style>
