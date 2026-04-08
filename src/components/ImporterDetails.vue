<script setup lang="ts">
import { ref, watch, nextTick } from 'vue';
import type { ImportedComponent } from '../types/importer';
import KicadPreview from './KicadPreview.vue';
import ModelPreview from './ModelPreview.vue';

// -----------------------------------------------------------------------
// Props & Emits
// -----------------------------------------------------------------------

const props = defineProps<{
  component: ImportedComponent;
  warnings?: string[];
}>();

const emit = defineEmits<{
  (e: 'close'): void;
}>();

// -----------------------------------------------------------------------
// AI state
// -----------------------------------------------------------------------

interface AiSuggestion {
  library: string;
  name: string;
  reason: string;
  confidence: 'high' | 'medium' | 'low';
}

interface PinRow {
  type?: string;
  name: string;
  number: string;
}

interface PinMappingResult {
  mapping: Record<string, string | null>;
  notes: string;
  warnings: string[];
  imported_pins: PinRow[];
  base_pins: PinRow[];
  merged_symbol_sexpr: string;
}

const error = ref('');

// -----------------------------------------------------------------------
// Footprint search (autocomplete for the Footprint field)
// -----------------------------------------------------------------------

interface FpSearchResult {
  library: string;
  name: string;
  path?: string;
}

const fpSearchResults = ref<FpSearchResult[]>([]);
const fpSearchLoading = ref(false);
const fpSearchActive = ref(false);
const fpSearchQuery = ref('');
const fpSearchInputRef = ref<HTMLInputElement | null>(null);
const fpOriginalValue = ref('');
const libReloading = ref(false);
const selectedFpSexpr = ref('');
const selectedFpName = ref('');
const selectedFpStepData = ref('');
const selectedFpModelTransform = ref<{ offset: {x:number,y:number,z:number}, scale: {x:number,y:number,z:number}, rotate: {x:number,y:number,z:number} } | null>(null);
const selectedFpLoading = ref(false);
let fpSearchTimer: ReturnType<typeof setTimeout> | null = null;

/** Toggle the search panel open/closed */
function toggleFpSearch(row: FieldRow) {
  if (fpSearchActive.value) {
    closeFpSearch();
    return;
  }
  // Store original value on first activation
  if (!fpOriginalValue.value) {
    fpOriginalValue.value = row.value;
  }
  fpSearchActive.value = true;
  fpSearchQuery.value = '';
  fpSearchResults.value = [];
  nextTick(() => { fpSearchInputRef.value?.focus(); });
}

function closeFpSearch() {
  fpSearchActive.value = false;
  fpSearchResults.value = [];
  fpSearchQuery.value = '';
}

function onFpSearchInput() {
  if (fpSearchTimer) clearTimeout(fpSearchTimer);
  const query = fpSearchQuery.value.trim();
  if (!query) {
    fpSearchResults.value = [];
    return;
  }
  fpSearchLoading.value = true;
  fpSearchTimer = setTimeout(() => searchFootprints(query), 300);
}

async function searchFootprints(query: string) {
  const api = getApi();
  if (!api) {
    fpSearchLoading.value = false;
    return;
  }
  try {
    const r = await api.importer_search_footprints(query);
    if (r?.success) {
      fpSearchResults.value = r.results ?? [];
    } else {
      fpSearchResults.value = [];
    }
  } catch {
    fpSearchResults.value = [];
  } finally {
    fpSearchLoading.value = false;
  }
}

function selectFootprint(row: FieldRow, result: FpSearchResult) {
  row.value = `${result.library}:${result.name}`;
  closeFpSearch();
  // Fetch the selected footprint's sexpr for side-by-side preview
  fetchSelectedFpSexpr(result.library, result.name);
}

async function reloadLibraries() {
  const api = getApi();
  if (!api) return;
  libReloading.value = true;
  try {
    await api.importer_reload_libraries();
  } catch { /* ignore */ }
  finally {
    libReloading.value = false;
  }
}

async function fetchSelectedFpSexpr(library: string, name: string) {
  const api = getApi();
  if (!api) return;
  selectedFpLoading.value = true;
  selectedFpName.value = `${library}:${name}`;
  selectedFpSexpr.value = '';
  selectedFpStepData.value = '';
  selectedFpModelTransform.value = null;
  try {
    const r = await api.importer_get_footprint_sexpr(library, name);
    if (r?.success) {
      selectedFpSexpr.value = r.sexpr ?? '';
      selectedFpStepData.value = r.step_data ?? '';
      selectedFpModelTransform.value = r.model_transform ?? null;
    }
  } catch (err) {
    console.error('[FP-fetch] error:', err);
    selectedFpSexpr.value = '';
    selectedFpStepData.value = '';
    selectedFpModelTransform.value = null;
  } finally {
    selectedFpLoading.value = false;
  }
}

function resetFootprint(row: FieldRow) {
  row.value = fpOriginalValue.value;
  selectedFpSexpr.value = '';
  selectedFpName.value = '';
  selectedFpStepData.value = '';
  selectedFpModelTransform.value = null;
  closeFpSearch();
}

/** True when the footprint value differs from the original imported value */
function isFpModified(row: FieldRow): boolean {
  return fpOriginalValue.value !== '' && row.value !== fpOriginalValue.value;
}

// AI symbol suggestion
const aiSuggestions = ref<AiSuggestion[]>([]);
const aiSuggestLoading = ref(false);
const aiSuggestError = ref('');
const showAiSuggest = ref(false);

// AI pin mapping
const selectedBaseSuggestion = ref<AiSuggestion | null>(null);
const pinMappingResult = ref<PinMappingResult | null>(null);
const pinMapLoading = ref(false);
const pinMapError = ref('');
const pinMapApplied = ref(false);

// AI symbol generation
const aiGenerateLoading = ref(false);
const aiGenerateError = ref('');
const aiGeneratedSexpr = ref('');

// -----------------------------------------------------------------------
// Add Variant (clone a template symbol with updated fields)
// -----------------------------------------------------------------------

interface LibraryInfo {
  nickname: string;
  uri: string;
}

interface SymbolSearchResult {
  library: string;
  name: string;
  description: string;
  value: string;
  footprint: string;
}

const showAddVariant = ref(false);
const variantLibraries = ref<LibraryInfo[]>([]);
const variantSelectedLib = ref('');
const variantTemplateQuery = ref('');
const variantTemplateResults = ref<SymbolSearchResult[]>([]);
const variantTemplateLoading = ref(false);
const variantSelectedTemplate = ref<SymbolSearchResult | null>(null);
const variantNewName = ref('');
const variantLoading = ref(false);
const variantError = ref('');
const variantSuccess = ref('');
let variantTemplateTimer: ReturnType<typeof setTimeout> | null = null;

// -----------------------------------------------------------------------
// Editable fields system
// -----------------------------------------------------------------------

/** Mandatory fields — always visible and editable */
const MANDATORY_KEYS = [
  'Reference', 'Value', 'Footprint', 'Datasheet',
  'Description', 'MF', 'MPN', 'DKPN', 'LCSC',
] as const;

interface FieldRow {
  key: string;
  value: string;
  mandatory: boolean;
  enabled: boolean;    // optional fields start disabled
  editingKey: boolean; // is the user editing the key name
}

/** Build the initial fields list from the imported component */
function buildFieldRows(c: ImportedComponent): FieldRow[] {
  const f = c.fields;
  const rows: FieldRow[] = [];

  // Map of mandatory display-key → source value
  const mandatoryMap: Record<string, string> = {
    'Reference':   f.reference || 'U',
    'Value':       f.value || c.name || '',
    'Footprint':   f.footprint || '',
    'Datasheet':   f.datasheet || '',
    'Description': f.description || '',
    'MF':          f.manufacturer || '',
    'MPN':         f.mpn || '',
    'DKPN':        f.digikey_pn || '',
    'LCSC':        f.lcsc_pn || '',
  };

  // Add mandatory fields (always enabled)
  for (const key of MANDATORY_KEYS) {
    rows.push({
      key,
      value: mandatoryMap[key] ?? '',
      mandatory: true,
      enabled: true,
      editingKey: false,
    });
  }

  // Known optional fields
  const optionalKnown: Record<string, string> = {
    'Mouser': f.mouser_pn || '',
    'Package': f.package || '',
  };
  for (const [key, val] of Object.entries(optionalKnown)) {
    // Only include if there's actually a value
    if (val) {
      rows.push({ key, value: val, mandatory: false, enabled: false, editingKey: false });
    }
  }

  // Extra fields from the import
  if (f.extra) {
    for (const [k, v] of Object.entries(f.extra)) {
      if (v) rows.push({ key: k, value: v, mandatory: false, enabled: false, editingKey: false });
    }
  }

  return rows;
}

const fieldRows = ref<FieldRow[]>(buildFieldRows(props.component));

// Initialize the original footprint value for reset support
fpOriginalValue.value = props.component.fields.footprint || '';

// Rebuild if the component prop changes
watch(() => props.component, (c) => {
  fieldRows.value = buildFieldRows(c);
});

/** Toggle an optional field on/off */
function toggleField(row: FieldRow) {
  if (row.mandatory) return;
  row.enabled = !row.enabled;
}

/** Add a new custom field */
function addField() {
  fieldRows.value.push({
    key: '',
    value: '',
    mandatory: false,
    enabled: true,
    editingKey: true,
  });
}

/** Remove an optional field */
function removeField(index: number) {
  const row = fieldRows.value[index];
  if (row.mandatory) return;
  fieldRows.value.splice(index, 1);
}

// -----------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------

function getApi() {
  return (window as any).pywebview?.api ?? null;
}

// -----------------------------------------------------------------------
// Post-import actions
// -----------------------------------------------------------------------

async function openInKicad() {
  const api = getApi();
  if (!api || !props.component.footprint_path) return;
  try {
    const r = await api.importer_open_in_kicad(props.component.footprint_path);
    if (!r?.success) error.value = r?.error || 'Could not open KiCad';
  } catch (e: any) {
    error.value = e.message || 'Failed';
  }
}

// -----------------------------------------------------------------------
// AI: suggest a native KiCad symbol as base
// -----------------------------------------------------------------------

async function aiSuggestSymbol() {
  const api = getApi();
  if (!api) return;
  aiSuggestLoading.value = true;
  aiSuggestError.value = '';
  aiSuggestions.value = [];
  showAiSuggest.value = true;
  try {
    const c = props.component;
    const r = await api.importer_ai_suggest_symbol(
      c.fields.mpn,
      c.fields.manufacturer,
      c.fields.description,
      c.fields.package,
      c.symbol_sexpr || '',
    );
    if (r?.success) {
      aiSuggestions.value = r.suggestions ?? [];
      if (!aiSuggestions.value.length) {
        aiSuggestError.value = 'AI returned no suggestions. Try a different AI model or refine the component data.';
      }
    } else {
      aiSuggestError.value = r?.error || 'AI suggestion failed';
    }
  } catch (e: any) {
    aiSuggestError.value = e.message || 'AI suggestion failed';
  } finally {
    aiSuggestLoading.value = false;
  }
}

// -----------------------------------------------------------------------
// AI: map imported pins onto selected base symbol
// -----------------------------------------------------------------------

async function aiMapPins(suggestion: AiSuggestion) {
  const api = getApi();
  if (!api) return;
  selectedBaseSuggestion.value = suggestion;
  pinMappingResult.value = null;
  pinMapError.value = '';
  pinMapApplied.value = false;
  pinMapLoading.value = true;
  try {
    const r = await api.importer_ai_map_pins(
      props.component.fields.mpn,
      props.component.symbol_sexpr || '',
      suggestion.library,
      suggestion.name,
    );
    if (r?.success) {
      pinMappingResult.value = r as PinMappingResult;
    } else {
      pinMapError.value = r?.error || 'Pin mapping failed';
    }
  } catch (e: any) {
    pinMapError.value = e.message || 'Pin mapping failed';
  } finally {
    pinMapLoading.value = false;
  }
}

async function applyPinMapping() {
  if (!pinMappingResult.value?.merged_symbol_sexpr) return;
  const api = getApi();
  if (!api) return;
  try {
    const r = await api.importer_write_symbol_sexpr(
      pinMappingResult.value.merged_symbol_sexpr,
      props.component.name,
      '',
      false,
    );
    if (r?.success) {
      pinMapApplied.value = true;
    } else {
      pinMapError.value = r?.error || 'Failed to apply mapping';
    }
  } catch (e: any) {
    pinMapError.value = e.message || 'Failed to apply mapping';
  }
}

// -----------------------------------------------------------------------
// AI: generate symbol from scratch
// -----------------------------------------------------------------------

async function aiGenerateSymbol() {
  const api = getApi();
  if (!api) return;
  aiGenerateLoading.value = true;
  aiGenerateError.value = '';
  aiGeneratedSexpr.value = '';
  try {
    const c = props.component;
    const r = await api.importer_ai_generate_symbol(
      c.fields.mpn,
      c.fields.manufacturer,
      c.fields.description,
      c.fields.package,
      c.fields.reference || 'U',
      c.fields.datasheet || '',
      [],
    );
    if (r?.success) {
      aiGeneratedSexpr.value = r.symbol_sexpr;
    } else {
      aiGenerateError.value = r?.error || 'AI generation failed';
    }
  } catch (e: any) {
    aiGenerateError.value = e.message || 'AI generation failed';
  } finally {
    aiGenerateLoading.value = false;
  }
}

async function saveGeneratedSymbol() {
  const api = getApi();
  if (!api || !aiGeneratedSexpr.value) return;
  try {
    const r = await api.importer_save_generated_symbol(
      aiGeneratedSexpr.value,
      props.component.name,
      '',
      false,
    );
    if (r?.success) {
      aiGeneratedSexpr.value = '';
    } else {
      aiGenerateError.value = r?.error || 'Save failed';
    }
  } catch (e: any) {
    aiGenerateError.value = e.message || 'Save failed';
  }
}

// -----------------------------------------------------------------------
// Add Variant actions
// -----------------------------------------------------------------------

async function toggleAddVariant() {
  showAddVariant.value = !showAddVariant.value;
  if (showAddVariant.value && !variantLibraries.value.length) {
    await loadVariantLibraries();
  }
}

async function loadVariantLibraries() {
  const api = getApi();
  if (!api) return;
  try {
    const r = await api.importer_get_sym_libraries();
    if (r?.success) {
      variantLibraries.value = r.libraries ?? [];
    }
  } catch { /* ignore */ }
}

function onVariantTemplateInput() {
  if (variantTemplateTimer) clearTimeout(variantTemplateTimer);
  const query = variantTemplateQuery.value.trim();
  if (!query || !variantSelectedLib.value) {
    variantTemplateResults.value = [];
    return;
  }
  variantTemplateLoading.value = true;
  variantTemplateTimer = setTimeout(() => searchVariantTemplates(query), 300);
}

async function searchVariantTemplates(query: string) {
  const api = getApi();
  if (!api) {
    variantTemplateLoading.value = false;
    return;
  }
  try {
    const r = await api.importer_search_symbols(query, variantSelectedLib.value);
    if (r?.success) {
      variantTemplateResults.value = r.results ?? [];
    } else {
      variantTemplateResults.value = [];
    }
  } catch {
    variantTemplateResults.value = [];
  } finally {
    variantTemplateLoading.value = false;
  }
}

function selectVariantTemplate(result: SymbolSearchResult) {
  variantSelectedTemplate.value = result;
  variantTemplateResults.value = [];
  variantTemplateQuery.value = result.name;
  // Auto-generate new name suggestion from current fields
  autoGenerateVariantName();
}

function autoGenerateVariantName() {
  // Build a name from fieldRows following the pcb-club pattern
  const ref = getFieldValue('Reference');
  const value = getFieldValue('Value');
  const fp = getFieldValue('Footprint');

  if (!ref || !value) {
    variantNewName.value = '';
    return;
  }

  // Extract package size from footprint (e.g. "Resistor_SMD:R_0603_..." → "0603")
  let pkg = '';
  const pkgMatch = fp.match(/_(\d{4})_/);
  if (pkgMatch) pkg = pkgMatch[1];

  // Try to build a descriptive name
  let name = ref + '_' + value;
  if (pkg) name += '_' + pkg;
  variantNewName.value = name;
}

function getFieldValue(key: string): string {
  const row = fieldRows.value.find(r => r.key === key);
  return row?.value || '';
}

/** Collect current field values into a dict for the backend */
function collectFieldsDict(): Record<string, string> {
  const result: Record<string, string> = {};
  for (const row of fieldRows.value) {
    if (row.enabled && row.key && row.value) {
      result[row.key] = row.value;
    }
  }
  return result;
}

async function addVariant() {
  const api = getApi();
  if (!api) return;
  if (!variantSelectedLib.value || !variantSelectedTemplate.value || !variantNewName.value.trim()) {
    variantError.value = 'Please select a library, template symbol, and enter a name.';
    return;
  }
  variantLoading.value = true;
  variantError.value = '';
  variantSuccess.value = '';
  try {
    const fields = collectFieldsDict();
    const r = await api.importer_add_variant(
      variantSelectedLib.value,
      variantSelectedTemplate.value.name,
      variantNewName.value.trim(),
      fields,
      '',  // target_library (same as template)
    );
    if (r?.success) {
      variantSuccess.value = `Added "${r.name}" to ${r.library}`;
      variantNewName.value = '';
    } else {
      variantError.value = r?.error || 'Add variant failed';
    }
  } catch (e: any) {
    variantError.value = e.message || 'Add variant failed';
  } finally {
    variantLoading.value = false;
  }
}
</script>

<template>
  <div class="details-panel">
    <!-- Header -->
    <div class="details-header">
      <button class="back-btn" @click="emit('close')" title="Back to chat">
        <span class="material-icons">close</span>
      </button>
      <span class="material-icons header-icon">check_circle</span>
      <span class="header-title">{{ component.name }}</span>
      <span class="import-badge">{{ component.import_method }}</span>
    </div>

    <div class="details-body">
      <!-- ===== Preview Section ===== -->
      <div v-if="component.symbol_sexpr || component.footprint_sexpr || component.step_data" class="preview-section">
        <div v-if="component.symbol_sexpr" class="preview-card">
          <div class="preview-label">
            <span class="material-icons preview-label-icon">memory</span>
            Symbol Preview
          </div>
          <KicadPreview :sexpr="component.symbol_sexpr" type="symbol" />
        </div>

        <!-- Footprint: single viewer with comparison when a different footprint is selected -->
        <div v-if="component.footprint_sexpr" class="preview-card">
          <div class="preview-label">
            <span class="material-icons preview-label-icon">crop_square</span>
            <template v-if="selectedFpSexpr">Footprint Comparison</template>
            <template v-else>Footprint Preview</template>
          </div>
          <KicadPreview
            :sexpr="component.footprint_sexpr"
            type="footprint"
            :compareSexpr="selectedFpSexpr || undefined"
            primaryLabel="Imported"
            :compareLabel="selectedFpName || 'Selected'"
          />
        </div>
        <div v-if="selectedFpLoading" class="preview-card">
          <div class="preview-label">
            <span class="material-icons preview-label-icon">crop_square</span>
            Loading footprint…
          </div>
          <div class="fp-loading-placeholder">
            <span class="material-icons fp-spin-icon">sync</span>
          </div>
        </div>

        <!-- 3D Model: pass all 4 data sources; toggles inside ModelPreview -->
        <div v-if="component.step_data || selectedFpStepData" class="preview-card preview-card-3d">
          <div class="preview-label">
            <span class="material-icons preview-label-icon">view_in_ar</span>
            <template v-if="selectedFpStepData || selectedFpSexpr">3D Model Comparison</template>
            <template v-else>3D Model</template>
          </div>
          <ModelPreview
            :stepData="component.step_data || undefined"
            :footprintSexpr="component.footprint_sexpr || undefined"
            :compareStepData="selectedFpStepData || undefined"
            :compareFootprintSexpr="selectedFpSexpr || undefined"
            :compareModelTransform="selectedFpModelTransform || undefined"
            primaryLabel="Imported"
            :compareLabel="selectedFpName || 'Selected'"
          />
        </div>
      </div>

      <div class="details-columns">
        <!-- ===== LEFT COLUMN: Symbol Fields ===== -->
        <div class="details-col">
          <div class="detail-section">
            <div class="section-label">
              <span class="material-icons section-icon">memory</span>
              Symbol Fields
            </div>
            <table class="fields-table">
              <thead>
                <tr>
                  <th class="field-th-toggle"></th>
                  <th class="field-th-key">Field</th>
                  <th class="field-th-val">Value</th>
                  <th class="field-th-action"></th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(row, idx) in fieldRows"
                  :key="idx"
                  :class="['field-row', { 'field-disabled': !row.enabled }]"
                >
                  <!-- Toggle checkbox (mandatory fields can't be disabled) -->
                  <td class="field-toggle">
                    <input
                      v-if="!row.mandatory"
                      type="checkbox"
                      :checked="row.enabled"
                      @change="toggleField(row)"
                      class="field-checkbox"
                      title="Enable/disable this field"
                    />
                    <span v-else class="material-icons field-lock" title="Mandatory field">lock</span>
                  </td>
                  <!-- Key -->
                  <td class="field-key">
                    <input
                      v-if="!row.mandatory && row.editingKey"
                      v-model="row.key"
                      class="field-input field-key-input"
                      placeholder="Field name"
                      @blur="row.editingKey = false"
                      @keyup.enter="($event.target as HTMLInputElement).blur()"
                    />
                    <span
                      v-else
                      :class="['field-key-text', { 'editable-key': !row.mandatory && row.enabled }]"
                      @dblclick="!row.mandatory && row.enabled ? row.editingKey = true : null"
                    >{{ row.key }}</span>
                  </td>
                  <!-- Value -->
                  <td class="field-val">
                    <template v-if="row.enabled">
                      <a
                        v-if="row.key === 'Datasheet' && row.value.startsWith('http')"
                        :href="row.value"
                        target="_blank"
                        class="ds-link"
                      >{{ row.value }}</a>
                      <!-- Footprint: value + separate search -->
                      <div v-else-if="row.key === 'Footprint'" class="fp-wrapper">
                        <div class="fp-value-row">
                          <input
                            v-model="row.value"
                            class="field-input fp-input"
                            placeholder="Footprint name"
                            readonly
                          />
                          <button
                            v-if="isFpModified(row)"
                            class="fp-btn fp-reset-btn"
                            title="Reset to imported footprint"
                            @click="resetFootprint(row)"
                          >
                            <span class="material-icons">undo</span>
                          </button>
                          <button
                            class="fp-btn fp-search-btn"
                            :class="{ active: fpSearchActive }"
                            title="Search footprint libraries"
                            @click="toggleFpSearch(row)"
                          >
                            <span class="material-icons">{{ fpSearchActive ? 'close' : 'search' }}</span>
                          </button>
                        </div>
                        <!-- Separate search box -->
                        <div v-if="fpSearchActive" class="fp-search-panel">
                          <div class="fp-search-input-row">
                            <span class="material-icons fp-search-icon">search</span>
                            <input
                              ref="fpSearchInputRef"
                              v-model="fpSearchQuery"
                              class="field-input fp-search-input"
                              placeholder="Type to search footprints…"
                              @input="onFpSearchInput()"
                            />
                            <span v-if="fpSearchLoading" class="material-icons fp-spin-icon">sync</span>
                            <button
                              class="fp-btn"
                              title="Reload libraries"
                              :disabled="libReloading"
                              @click="reloadLibraries()"
                            >
                              <span class="material-icons" :class="{ 'fp-spin-icon': libReloading }">
                                refresh
                              </span>
                            </button>
                          </div>
                          <div v-if="fpSearchResults.length" class="fp-results">
                            <div
                              v-for="sr in fpSearchResults"
                              :key="sr.library + ':' + sr.name"
                              class="fp-drop-item"
                              @click="selectFootprint(row, sr)"
                            >
                              <span class="fp-drop-lib">{{ sr.library }}</span>
                              <span class="fp-drop-name">{{ sr.name }}</span>
                            </div>
                          </div>
                          <div v-else-if="fpSearchQuery.trim() && !fpSearchLoading" class="fp-drop-hint">
                            No matches
                          </div>
                          <div v-else-if="!fpSearchQuery.trim() && !fpSearchLoading" class="fp-drop-hint">
                            Start typing to search
                          </div>
                        </div>
                      </div>
                      <input
                        v-else
                        v-model="row.value"
                        class="field-input"
                        :placeholder="row.key"
                      />
                    </template>
                    <span v-else class="field-val-disabled">{{ row.value || '—' }}</span>
                  </td>
                  <!-- Remove button (optional only) -->
                  <td class="field-action">
                    <button
                      v-if="!row.mandatory"
                      class="field-remove-btn"
                      @click="removeField(idx)"
                      title="Remove field"
                    >
                      <span class="material-icons">close</span>
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <button class="add-field-btn" @click="addField">
              <span class="material-icons">add</span>
              Add Field
            </button>
            <div v-if="!fieldRows.length" class="empty-hint">No field data available.</div>
          </div>

          <!-- ===== Add Variant Section ===== -->
          <div class="detail-section">
            <button class="add-variant-toggle" @click="toggleAddVariant">
              <span class="material-icons section-icon">library_add</span>
              <span class="section-label-text">Add Variant to Library</span>
              <span class="material-icons toggle-chevron">{{ showAddVariant ? 'expand_less' : 'expand_more' }}</span>
            </button>

            <div v-if="showAddVariant" class="variant-panel">
              <div class="variant-hint">
                Clone a template passive symbol and apply the fields above.
              </div>

              <!-- Library selector -->
              <label class="variant-label">Target Library</label>
              <select v-model="variantSelectedLib" class="variant-select" @change="variantSelectedTemplate = null; variantTemplateQuery = ''; variantTemplateResults = []">
                <option value="">— Select library —</option>
                <option v-for="lib in variantLibraries" :key="lib.nickname" :value="lib.nickname">
                  {{ lib.nickname }}
                </option>
              </select>

              <!-- Template symbol search -->
              <label class="variant-label">Template Symbol</label>
              <div class="variant-search-row">
                <input
                  v-model="variantTemplateQuery"
                  class="field-input variant-search-input"
                  :placeholder="variantSelectedLib ? 'Search symbols in ' + variantSelectedLib + '…' : 'Select a library first'"
                  :disabled="!variantSelectedLib"
                  @input="onVariantTemplateInput()"
                />
                <span v-if="variantTemplateLoading" class="material-icons fp-spin-icon">sync</span>
              </div>
              <div v-if="variantSelectedTemplate" class="variant-template-badge">
                <span class="material-icons" style="font-size: 14px">check_circle</span>
                {{ variantSelectedTemplate.name }}
                <span v-if="variantSelectedTemplate.value" class="variant-template-val">({{ variantSelectedTemplate.value }})</span>
              </div>
              <div v-if="variantTemplateResults.length" class="variant-results">
                <div
                  v-for="sr in variantTemplateResults"
                  :key="sr.library + ':' + sr.name"
                  class="fp-drop-item"
                  @click="selectVariantTemplate(sr)"
                >
                  <span class="fp-drop-name">{{ sr.name }}</span>
                  <span v-if="sr.value" class="fp-drop-lib">{{ sr.value }}</span>
                </div>
              </div>

              <!-- New symbol name -->
              <label class="variant-label">New Symbol Name</label>
              <input
                v-model="variantNewName"
                class="field-input variant-name-input"
                placeholder="e.g. R_4.7k_0603_0.1W_1%"
              />

              <!-- Action -->
              <div class="variant-actions">
                <button
                  class="action-btn primary"
                  @click="addVariant"
                  :disabled="variantLoading || !variantSelectedLib || !variantSelectedTemplate || !variantNewName.trim()"
                >
                  <span class="material-icons">{{ variantLoading ? 'sync' : 'library_add' }}</span>
                  {{ variantLoading ? 'Adding…' : 'Add Variant' }}
                </button>
              </div>

              <div v-if="variantSuccess" class="notice success" style="margin-top: 0.5rem">
                <span class="material-icons">check_circle</span>
                {{ variantSuccess }}
              </div>
              <div v-if="variantError" class="notice error" style="margin-top: 0.5rem">
                <span class="material-icons">error_outline</span>
                {{ variantError }}
              </div>
            </div>
          </div>

          <!-- Files -->
          <div v-if="component.symbol_path || component.footprint_path || component.model_paths?.length" class="detail-section">
            <div class="section-label">
              <span class="material-icons section-icon">folder</span>
              Files
            </div>
            <div class="paths-list">
              <div v-if="component.symbol_path" class="path-row">
                <span class="material-icons path-icon">memory</span>
                <span class="path-text" :title="component.symbol_path">{{ component.symbol_path }}</span>
              </div>
              <div v-if="component.footprint_path" class="path-row">
                <span class="material-icons path-icon">crop_square</span>
                <span class="path-text" :title="component.footprint_path">{{ component.footprint_path }}</span>
              </div>
              <div v-for="mp in component.model_paths" :key="mp" class="path-row">
                <span class="material-icons path-icon">view_in_ar</span>
                <span class="path-text" :title="mp">{{ mp }}</span>
              </div>
            </div>
          </div>

          <!-- Actions -->
          <div class="detail-actions">
            <button
              v-if="component.footprint_path"
              class="action-btn primary"
              @click="openInKicad"
            >
              <span class="material-icons">open_in_new</span>
              Open in KiCad
            </button>
          </div>

          <!-- Warnings -->
          <div v-if="warnings?.length" class="notice warn">
            <span class="material-icons">warning</span>
            <ul><li v-for="w in warnings" :key="w">{{ w }}</li></ul>
          </div>
          <div v-if="error" class="notice error">
            <span class="material-icons">error_outline</span>
            {{ error }}
          </div>
        </div>

        <!-- ===== RIGHT COLUMN: AI Tools ===== -->
        <div class="details-col">
          <div class="ai-section">
            <div class="ai-section-header">
              <span class="material-icons ai-icon">auto_awesome</span>
              <span class="ai-section-title">AI Symbol Tools</span>
            </div>

            <div class="ai-action-row">
              <button
                class="action-btn primary ai-btn"
                @click="aiSuggestSymbol"
                :disabled="aiSuggestLoading || aiGenerateLoading"
                title="Let AI suggest a native KiCad symbol to use as a visual base"
              >
                <span class="material-icons">search</span>
                {{ aiSuggestLoading ? 'Searching…' : 'AI Suggest Base Symbol' }}
              </button>
              <button
                class="action-btn secondary ai-btn"
                @click="aiGenerateSymbol"
                :disabled="aiGenerateLoading || aiSuggestLoading"
                title="Ask AI to generate a new symbol from scratch"
              >
                <span class="material-icons">draw</span>
                {{ aiGenerateLoading ? 'Generating…' : 'AI Generate Symbol' }}
              </button>
            </div>

            <!-- AI suggest error -->
            <div v-if="aiSuggestError" class="notice error" style="margin: 0 0.5rem">
              <span class="material-icons">error_outline</span>
              {{ aiSuggestError }}
            </div>

            <!-- Suggestions list -->
            <div v-if="showAiSuggest && aiSuggestions.length" class="ai-suggestions">
              <div class="ai-sub-label">Select a base symbol, then click "Map Pins":</div>
              <div
                v-for="sug in aiSuggestions"
                :key="sug.library + ':' + sug.name"
                :class="['ai-sug-row', { selected: selectedBaseSuggestion === sug }]"
                @click="selectedBaseSuggestion = sug; pinMappingResult = null; pinMapApplied = false"
              >
                <div class="ai-sug-top">
                  <span
                    :class="['confidence-badge', sug.confidence]"
                    :title="'Confidence: ' + sug.confidence"
                  >{{ sug.confidence[0].toUpperCase() }}</span>
                  <span class="sug-lib">{{ sug.library }}</span>
                  <span class="sug-name">{{ sug.name }}</span>
                  <button
                    class="action-btn primary map-btn"
                    @click.stop="aiMapPins(sug)"
                    :disabled="pinMapLoading"
                  >
                    <span class="material-icons">device_hub</span>
                    Map Pins
                  </button>
                </div>
                <div class="sug-reason">{{ sug.reason }}</div>
              </div>
            </div>

            <!-- Pin mapping error -->
            <div v-if="pinMapError" class="notice error" style="margin: 0 0.5rem">
              <span class="material-icons">error_outline</span>
              {{ pinMapError }}
            </div>

            <!-- Pin mapping result -->
            <div v-if="pinMappingResult" class="pin-map-card">
              <div class="pin-map-header">
                <span class="material-icons" style="color: var(--accent-color)">device_hub</span>
                Pin Mapping — {{ selectedBaseSuggestion?.library }}:{{ selectedBaseSuggestion?.name }}
              </div>
              <div v-if="pinMappingResult.notes" class="pin-map-notes">{{ pinMappingResult.notes }}</div>
              <div v-if="pinMappingResult.warnings?.length" class="notice warn" style="margin: 0.3rem 0.5rem">
                <span class="material-icons">warning</span>
                <ul><li v-for="w in pinMappingResult.warnings" :key="w">{{ w }}</li></ul>
              </div>
              <table class="fields-table">
                <thead>
                  <tr>
                    <th class="field-key">Base Pin</th>
                    <th class="field-key">Base Name</th>
                    <th class="field-key">→ Import Pin</th>
                    <th class="field-key">Import Name</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="bp in pinMappingResult.base_pins"
                    :key="bp.number"
                    :class="{ unmapped: !pinMappingResult.mapping[bp.number] }"
                  >
                    <td class="field-key">{{ bp.number }}</td>
                    <td class="field-val">{{ bp.name }}</td>
                    <td class="field-key" :style="{ color: pinMappingResult.mapping[bp.number] ? 'var(--accent-color)' : '#e74c3c' }">
                      {{ pinMappingResult.mapping[bp.number] ?? '—' }}
                    </td>
                    <td class="field-val">
                      {{ pinMappingResult.imported_pins.find(p => p.number === pinMappingResult!.mapping[bp.number])?.name ?? '' }}
                    </td>
                  </tr>
                </tbody>
              </table>
              <div class="card-actions">
                <button
                  class="action-btn primary"
                  @click="applyPinMapping"
                  :disabled="pinMapApplied"
                >
                  <span class="material-icons">check_circle</span>
                  {{ pinMapApplied ? 'Applied ✓' : 'Apply Mapping to Library' }}
                </button>
              </div>
            </div>

            <!-- AI generate error -->
            <div v-if="aiGenerateError" class="notice error" style="margin: 0 0.5rem">
              <span class="material-icons">error_outline</span>
              {{ aiGenerateError }}
            </div>

            <!-- AI generated symbol preview -->
            <div v-if="aiGeneratedSexpr" class="ai-gen-card">
              <div class="pin-map-header">
                <span class="material-icons" style="color: var(--accent-color)">draw</span>
                AI-Generated Symbol S-expression
              </div>
              <pre class="sexpr-preview">{{ aiGeneratedSexpr.substring(0, 800) }}{{ aiGeneratedSexpr.length > 800 ? '\n…' : '' }}</pre>
              <div class="card-actions">
                <button class="action-btn primary" @click="saveGeneratedSymbol">
                  <span class="material-icons">save</span>
                  Save Generated Symbol
                </button>
              </div>
            </div>

          </div><!-- /ai-section -->
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.details-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background-color: var(--bg-primary);
}

/* Header */
.details-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
  background-color: var(--bg-secondary);
}

.back-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.8rem;
  height: 1.8rem;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.12s;
}

.back-btn:hover {
  background-color: var(--bg-tertiary);
  color: var(--text-primary);
}

.back-btn .material-icons {
  font-size: 1.2rem;
}

.header-icon {
  font-size: 1.2rem;
  color: var(--accent-color);
}

.header-title {
  font-weight: 700;
  font-size: 0.95rem;
  color: var(--text-primary);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.import-badge {
  font-size: 0.65rem;
  font-weight: 700;
  text-transform: uppercase;
  padding: 0.15rem 0.45rem;
  border-radius: 4px;
  background-color: var(--bg-tertiary);
  color: var(--text-secondary);
  border: 1px solid var(--border-color);
  flex-shrink: 0;
}

/* Body */
.details-body {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  min-height: 0;
}

/* Preview section */
.preview-section {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 1rem;
  margin-bottom: 1rem;
  max-width: 1200px;
}

.preview-card {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background-color: var(--bg-secondary);
  overflow: hidden;
}

.preview-card-3d {
  min-height: 300px;
}

.fp-loading-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 150px;
  color: var(--text-secondary);
}

.preview-label {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.4rem 0.75rem;
  border-bottom: 1px solid var(--border-color);
  background-color: var(--bg-tertiary);
  font-weight: 700;
  font-size: 0.78rem;
  color: var(--text-primary);
}

.preview-label-icon {
  font-size: 1rem;
  color: var(--accent-color);
}

.details-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
  max-width: 1200px;
}

.details-col {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  min-width: 0;
}

/* Sections */
.detail-section {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background-color: var(--bg-secondary);
  overflow: hidden;
}

.section-label {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border-color);
  background-color: var(--bg-tertiary);
  font-weight: 700;
  font-size: 0.8rem;
  color: var(--text-primary);
}

.section-icon {
  font-size: 1rem;
  color: var(--accent-color);
}

/* Fields table */
.fields-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.78rem;
}

.fields-table thead th {
  padding: 0.3rem 0.5rem;
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  color: var(--text-secondary);
  text-align: left;
  border-bottom: 1px solid var(--border-color);
}

.field-th-toggle { width: 28px; }
.field-th-key { width: 100px; }
.field-th-val { }
.field-th-action { width: 28px; }

.fields-table tr:not(:last-child) td {
  border-bottom: 1px solid var(--border-color);
}

/* Row states */
.field-row {
  transition: opacity 0.15s;
}
.field-row.field-disabled {
  opacity: 0.45;
}

/* Toggle column */
.field-toggle {
  padding: 0.3rem 0.4rem;
  text-align: center;
  vertical-align: middle;
}
.field-checkbox {
  width: 14px;
  height: 14px;
  cursor: pointer;
  accent-color: var(--accent-color);
}
.field-lock {
  font-size: 0.85rem;
  color: var(--text-secondary);
  opacity: 0.5;
}

/* Key column */
.field-key {
  padding: 0.3rem 0.5rem;
  color: var(--text-secondary);
  font-weight: 600;
  white-space: nowrap;
  width: 100px;
}
.field-key-text {
  cursor: default;
}
.field-key-text.editable-key {
  cursor: pointer;
  border-bottom: 1px dashed var(--border-color);
}
.field-key-input {
  width: 90px;
  font-weight: 600;
}

/* Value column */
.field-val {
  padding: 0.3rem 0.5rem;
  color: var(--text-primary);
  word-break: break-all;
}
.field-val-disabled {
  opacity: 0.6;
  font-style: italic;
}

/* Shared input style */
.field-input {
  width: 100%;
  padding: 0.2rem 0.4rem;
  font-size: 0.78rem;
  font-family: inherit;
  color: var(--text-primary);
  background-color: var(--bg-input, var(--bg-secondary));
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm, 3px);
  outline: none;
  transition: border-color 0.15s;
}
.field-input:focus {
  border-color: var(--accent-color);
}

/* Action column */
.field-action {
  padding: 0.2rem;
  text-align: center;
}
.field-remove-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-secondary);
  padding: 2px;
  border-radius: 3px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.field-remove-btn:hover {
  color: #e55;
  background-color: rgba(255,50,50,0.1);
}
.field-remove-btn .material-icons {
  font-size: 0.9rem;
}

/* Add field button */
.add-field-btn {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  margin: 0.4rem 0.5rem;
  padding: 0.25rem 0.6rem;
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--accent-color);
  background: none;
  border: 1px dashed var(--border-color);
  border-radius: var(--radius-sm, 3px);
  cursor: pointer;
  transition: background-color 0.15s, border-color 0.15s;
}
.add-field-btn:hover {
  background-color: var(--bg-tertiary);
  border-color: var(--accent-color);
}
.add-field-btn .material-icons {
  font-size: 0.9rem;
}

.ds-link {
  color: var(--accent-color);
  text-decoration: none;
  word-break: break-all;
}

.ds-link:hover {
  text-decoration: underline;
}

/* Footprint field */
.fp-wrapper {
  width: 100%;
}

.fp-value-row {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 0;
}

.fp-input {
  flex: 1;
  border-top-right-radius: 0 !important;
  border-bottom-right-radius: 0 !important;
  min-width: 0;
}

.fp-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 0.2rem 0.3rem;
  border: 1px solid var(--border-color);
  border-left: none;
  background-color: var(--bg-tertiary);
  color: var(--text-secondary);
  cursor: pointer;
  transition: background-color 0.12s, color 0.12s;
  flex-shrink: 0;
}

.fp-btn:last-child {
  border-top-right-radius: var(--radius-sm, 3px);
  border-bottom-right-radius: var(--radius-sm, 3px);
}

.fp-btn .material-icons {
  font-size: 0.9rem;
}

.fp-btn:hover:not(:disabled) {
  background-color: var(--bg-secondary);
  color: var(--text-primary);
}

.fp-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.fp-search-btn:hover:not(:disabled) {
  color: var(--accent-color);
}

.fp-search-btn.active {
  background-color: var(--accent-color);
  color: #fff;
  border-color: var(--accent-color);
}

.fp-reset-btn:hover {
  color: #e5a33a;
}

/* Search panel (separate from the value field) */
.fp-search-panel {
  margin-top: 0.3rem;
  border: 1px solid var(--accent-color);
  border-radius: var(--radius-sm, 3px);
  background-color: var(--bg-secondary);
  overflow: hidden;
}

.fp-search-input-row {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.3rem 0.4rem;
  border-bottom: 1px solid var(--border-color);
  background-color: var(--bg-tertiary);
}

.fp-search-icon {
  font-size: 0.9rem;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.fp-search-input {
  flex: 1;
  border: none !important;
  background: transparent !important;
  padding: 0.15rem 0 !important;
  min-width: 0;
}

.fp-search-input:focus {
  outline: none;
  border: none !important;
}

.fp-spin-icon {
  font-size: 0.85rem;
  animation: fp-spin 0.8s linear infinite;
}

@keyframes fp-spin { to { transform: rotate(360deg); } }

.fp-results {
  max-height: 50vh;
  overflow-y: auto;
}

.fp-drop-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.3rem 0.5rem;
  cursor: pointer;
  font-size: 0.75rem;
  transition: background-color 0.1s;
}

.fp-drop-item:hover {
  background-color: var(--bg-tertiary);
}

.fp-drop-lib {
  color: var(--text-secondary);
  font-size: 0.68rem;
  flex-shrink: 0;
}

.fp-drop-name {
  color: var(--text-primary);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fp-drop-hint {
  padding: 0.4rem 0.5rem;
  font-size: 0.72rem;
  color: var(--text-secondary);
  font-style: italic;
  text-align: center;
}

.empty-hint {
  color: var(--text-secondary);
  font-size: 0.8rem;
  text-align: center;
  padding: 1rem;
}

/* Paths */
.paths-list {
  padding: 0.5rem 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.path-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.75rem;
}

.path-icon {
  font-size: 0.9rem;
  color: var(--accent-color);
  flex-shrink: 0;
}

.path-text {
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: monospace;
}

/* Actions */
.detail-actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

/* Buttons */
.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.4rem 0.75rem;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  font-size: 0.8rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
  flex-shrink: 0;
}

.action-btn .material-icons {
  font-size: 1rem;
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
}

.action-btn.secondary:hover:not(:disabled) {
  background-color: var(--bg-secondary);
  color: var(--text-primary);
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Notices */
.notice {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  padding: 0.5rem 0.6rem;
  border-radius: var(--radius-sm);
  font-size: 0.8rem;
  line-height: 1.4;
}

.notice .material-icons {
  font-size: 1.1rem;
  flex-shrink: 0;
  margin-top: 0.05rem;
}

.notice.error {
  background-color: color-mix(in srgb, #e74c3c 15%, transparent);
  color: #e74c3c;
  border: 1px solid color-mix(in srgb, #e74c3c 30%, transparent);
}

.notice.warn {
  background-color: color-mix(in srgb, #f39c12 15%, transparent);
  color: #c17d10;
  border: 1px solid color-mix(in srgb, #f39c12 30%, transparent);
}

.notice ul {
  margin: 0;
  padding-left: 1rem;
}

/* ===== AI Section ===== */
.ai-section {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
  background-color: var(--bg-secondary);
}

.ai-section-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border-color);
  background: color-mix(in srgb, var(--accent-color) 8%, var(--bg-tertiary));
}

.ai-icon {
  font-size: 1.1rem;
  color: var(--accent-color);
}

.ai-section-title {
  font-weight: 700;
  font-size: 0.85rem;
  color: var(--text-primary);
}

.ai-action-row {
  display: flex;
  gap: 0.5rem;
  padding: 0.6rem;
  flex-wrap: wrap;
}

.ai-btn {
  flex: 1;
  justify-content: center;
}

.ai-sub-label {
  font-size: 0.75rem;
  color: var(--text-secondary);
  padding: 0 0.6rem 0.35rem;
  font-style: italic;
}

/* Suggestions list */
.ai-suggestions {
  padding: 0 0.6rem 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.ai-sug-row {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  padding: 0.4rem 0.6rem;
  cursor: pointer;
  transition: background-color 0.12s;
}

.ai-sug-row:hover {
  background-color: var(--bg-tertiary);
}

.ai-sug-row.selected {
  border-color: var(--accent-color);
  background-color: color-mix(in srgb, var(--accent-color) 10%, transparent);
}

.ai-sug-top {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.confidence-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.2rem;
  height: 1.2rem;
  border-radius: 50%;
  font-size: 0.65rem;
  font-weight: 700;
  flex-shrink: 0;
}

.confidence-badge.high { background-color: #27ae60; color: white; }
.confidence-badge.medium { background-color: #f39c12; color: white; }
.confidence-badge.low { background-color: #bdc3c7; color: white; }

.sug-lib {
  font-size: 0.72rem;
  color: var(--text-secondary);
}

.sug-name {
  font-weight: 600;
  font-size: 0.8rem;
  color: var(--text-primary);
  flex: 1;
}

.sug-reason {
  font-size: 0.72rem;
  color: var(--text-secondary);
  margin-top: 0.2rem;
  line-height: 1.3;
}

.map-btn {
  padding: 0.25rem 0.6rem !important;
  font-size: 0.72rem !important;
}

.map-btn .material-icons {
  font-size: 0.85rem !important;
}

/* Pin mapping card */
.pin-map-card {
  margin: 0.5rem 0.6rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.pin-map-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.4rem 0.6rem;
  background-color: var(--bg-tertiary);
  border-bottom: 1px solid var(--border-color);
  font-weight: 600;
  font-size: 0.8rem;
  color: var(--text-primary);
}

.pin-map-notes {
  padding: 0.35rem 0.6rem;
  font-size: 0.75rem;
  color: var(--text-secondary);
  font-style: italic;
  border-bottom: 1px solid var(--border-color);
}

.fields-table th {
  padding: 0.3rem 0.75rem;
  text-align: left;
  background-color: var(--bg-tertiary);
  font-size: 0.72rem;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-color);
}

tr.unmapped {
  opacity: 0.6;
}

.card-actions {
  padding: 0.5rem 0.6rem;
  border-top: 1px solid var(--border-color);
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

/* AI generate card */
.ai-gen-card {
  margin: 0.5rem 0.6rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.sexpr-preview {
  padding: 0.5rem 0.6rem;
  font-size: 0.72rem;
  font-family: monospace;
  color: var(--text-secondary);
  background-color: var(--bg-input);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
  margin: 0;
  border-bottom: 1px solid var(--border-color);
}

/* ===== Add Variant Section ===== */
.add-variant-toggle {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  width: 100%;
  padding: 0.5rem 0;
  border: none;
  background: none;
  cursor: pointer;
  color: var(--text-primary);
  font-weight: 700;
  font-size: 0.82rem;
}

.add-variant-toggle:hover {
  color: var(--accent-color);
}

.section-label-text {
  flex: 1;
  text-align: left;
}

.toggle-chevron {
  font-size: 1.1rem;
  color: var(--text-secondary);
}

.variant-panel {
  padding: 0.5rem 0;
}

.variant-hint {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-bottom: 0.5rem;
  font-style: italic;
}

.variant-label {
  display: block;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--text-secondary);
  margin: 0.5rem 0 0.25rem;
}

.variant-select {
  width: 100%;
  padding: 0.35rem 0.5rem;
  font-size: 0.8rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background-color: var(--bg-input);
  color: var(--text-primary);
}

.variant-search-row {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}

.variant-search-input {
  flex: 1;
}

.variant-template-badge {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  margin-top: 0.3rem;
  padding: 0.25rem 0.5rem;
  background: color-mix(in srgb, var(--accent-color) 12%, var(--bg-tertiary));
  border-radius: var(--radius-sm);
  font-size: 0.78rem;
  color: var(--accent-color);
}

.variant-template-val {
  color: var(--text-secondary);
  font-style: italic;
}

.variant-results {
  max-height: 150px;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  margin-top: 0.25rem;
  background-color: var(--bg-input);
}

.variant-name-input {
  width: 100%;
}

.variant-actions {
  margin-top: 0.6rem;
  display: flex;
  gap: 0.5rem;
}

.notice.success {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  font-size: 0.78rem;
  padding: 0.4rem 0.6rem;
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, #27ae60 10%, var(--bg-tertiary));
  color: #27ae60;
}
</style>
