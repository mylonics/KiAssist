<script setup lang="ts">
import { ref, watch, onMounted, computed } from 'vue';
import type { ImportedComponent } from '../types/importer';
import KicadPreview from './KicadPreview.vue';
import ModelPreview from './ModelPreview.vue';
import { useAppSettings, FIELD_DESCRIPTIONS } from '../composables/useAppSettings';

const {
  settings: appSettings,
  getEffectiveSymLib,
  getEffectiveFpLib,
  getEffectiveModelsDir,
  recordLastUsedLibraries,
  addModelsDirToHistory,
} = useAppSettings();

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
const fpSearchInputRef = ref<HTMLInputElement | null>(null); // used as template ref
void fpSearchInputRef; // suppress TS6133
const fpOriginalValue = ref('');
const libReloading = ref(false);
const selectedFpSexpr = ref('');
const selectedFpName = ref('');
const selectedFpStepData = ref('');
const selectedFpModelTransform = ref<{ offset: {x:number,y:number,z:number}, scale: {x:number,y:number,z:number}, rotate: {x:number,y:number,z:number} } | null>(null);
const selectedFpLoading = ref(false);
let fpSearchTimer: ReturnType<typeof setTimeout> | null = null;

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

function selectFootprint(result: FpSearchResult) {
  const ref = `${result.library}:${result.name}`;
  // Update the Footprint field row to reflect the new selection
  const fpRow = fieldRows.value.find(r => r.key === 'Footprint');
  if (fpRow) fpRow.value = ref;
  selectedFpName.value = ref;
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

function resetFootprint() {
  // Restore the Footprint field row to the original value
  const fpRow = fieldRows.value.find(r => r.key === 'Footprint');
  if (fpRow) fpRow.value = fpOriginalValue.value;
  selectedFpSexpr.value = '';
  selectedFpName.value = '';
  selectedFpStepData.value = '';
  selectedFpModelTransform.value = null;
  closeFpSearch();
}

// -----------------------------------------------------------------------
// Symbol search (replace graphical symbol from existing library)
// -----------------------------------------------------------------------

interface SymSearchResult {
  library: string;
  name: string;
  description?: string;
}

const symSearchResults = ref<SymSearchResult[]>([]);
const symSearchLoading = ref(false);
const symSearchActive = ref(false);
const symSearchQuery = ref('');
const symSearchInputRef = ref<HTMLInputElement | null>(null); // used as template ref
void symSearchInputRef; // suppress TS6133
const selectedSymSexpr = ref('');       // base symbol sexpr for preview
const selectedSymMergedSexpr = ref(''); // merged result (imported fields + base graphics)
const selectedSymName = ref('');
const selectedSymLoading = ref(false);
const symReplaceError = ref('');
let symSearchTimer: ReturnType<typeof setTimeout> | null = null;

// -----------------------------------------------------------------------
// -----------------------------------------------------------------------
// -----------------------------------------------------------------------
// Add Variant (moved to VariantImporter.vue side panel)
// -----------------------------------------------------------------------

interface LibraryInfo {
  nickname: string;
  uri: string;
}

// -----------------------------------------------------------------------
// Save to Library
// -----------------------------------------------------------------------

const saveSymLibraries = ref<LibraryInfo[]>([]);
const saveFpLibraries = ref<LibraryInfo[]>([]);
const saveSymLib = ref('');          // nickname or 'custom'
const saveSymLibCustom = ref('');    // absolute path when custom
const saveFpLib = ref('');           // nickname or 'custom'
const saveFpLibCustom = ref('');     // absolute path when custom
const saveModelsDir = ref('');       // optional custom 3D models dir
const saveModelsDirMode = ref<'auto' | 'history' | 'custom'>('auto'); // dropdown mode
const saveLoading = ref(false);
const saveError = ref('');
const saveSuccess = ref('');
const saveLibsLoaded = ref(false);

// Overwrite confirmation dialog state
type SaveAction = 'overwrite' | 'append' | 'ignore';
const showOverwriteDialog = ref(false);
const overwriteChecks = ref({
  symbolExists: false, footprintExists: false, modelExists: false,
  symbolName: '', footprintName: '', modelName: '',
});
const symbolAction = ref<SaveAction>('overwrite');
const footprintAction = ref<SaveAction>('overwrite');
const modelAction = ref<SaveAction>('overwrite');

/** True when at least one saveable asset is loaded */
const hasAnythingToSave = computed(() => {
  return !!(props.component.symbol_sexpr || props.component.footprint_sexpr || props.component.step_data);
});

const hasSym = computed(() => !!props.component.symbol_sexpr);
const hasFp = computed(() => !!props.component.footprint_sexpr);
const hasModel = computed(() => !!props.component.step_data);

/** True when this component came from the variant importer. */
const isVariant = computed(() => (props.component.source_info || '').startsWith('Variant:'));

/**
 * For variants, footprint / 3D-model saving is opt-in.
 * This flag becomes true only after the user explicitly confirms they want
 * to save a copy of the footprint (and associated 3D model).
 */
const variantSaveFp = ref(false);

/** Show the "Save footprint copy?" confirmation banner for variants. */
const showVariantFpConfirm = ref(false);

/** Whether the fp/3D selectors should be enabled (non-variant OR user opted-in). */
const fpSaveEnabled = computed(() => hasFp.value && (!isVariant.value || variantSaveFp.value));
const modelSaveEnabled = computed(() => hasModel.value && (!isVariant.value || variantSaveFp.value));

/** Called when user clicks the greyed-out fp/3D area on a variant. */
function onVariantFpClick() {
  if (!isVariant.value || variantSaveFp.value) return;
  showVariantFpConfirm.value = true;
}

function confirmVariantSaveFp() {
  variantSaveFp.value = true;
  showVariantFpConfirm.value = false;
}

function cancelVariantSaveFp() {
  showVariantFpConfirm.value = false;
}

// Template ref for the 3D model preview (used to read current transform on save)
const modelPreviewRef = ref<InstanceType<typeof ModelPreview> | null>(null);

/** Extract model transform from the imported component's footprint_sexpr */
const importedModelTransform = computed(() => {
  const sexpr = props.component.footprint_sexpr;
  if (!sexpr) return undefined;
  return parseModelTransformFromSexpr(sexpr);
});

function parseModelTransformFromSexpr(sexpr: string): { offset: {x:number,y:number,z:number}, scale: {x:number,y:number,z:number}, rotate: {x:number,y:number,z:number} } | undefined {
  const xyzPat = (tag: string) => {
    const m = sexpr.match(new RegExp(`\\(${tag}\\s+\\(xyz\\s+([\\d.eE+\\-]+)\\s+([\\d.eE+\\-]+)\\s+([\\d.eE+\\-]+)\\s*\\)`));
    return m ? { x: parseFloat(m[1]), y: parseFloat(m[2]), z: parseFloat(m[3]) } : null;
  };
  const offset = xyzPat('offset');
  const scale = xyzPat('scale');
  const rotate = xyzPat('rotate');
  if (!offset && !scale && !rotate) return undefined;
  return {
    offset: offset ?? { x: 0, y: 0, z: 0 },
    scale: scale ?? { x: 1, y: 1, z: 1 },
    rotate: rotate ?? { x: 0, y: 0, z: 0 },
  };
}

/** Update (model …) transform values inside a footprint S-expression string. */
function updateFootprintTransform(
  sexpr: string,
  transform: { offset: {x:number,y:number,z:number}, scale: {x:number,y:number,z:number}, rotate: {x:number,y:number,z:number} },
): string {
  function replaceXyz(src: string, tag: string, vals: {x:number,y:number,z:number}): string {
    const pat = new RegExp(`\\(${tag}\\s+\\(xyz\\s+[\\d.eE+\\-]+\\s+[\\d.eE+\\-]+\\s+[\\d.eE+\\-]+\\s*\\)\\s*\\)`);
    return src.replace(pat, `(${tag} (xyz ${vals.x} ${vals.y} ${vals.z}))`);
  }
  let result = replaceXyz(sexpr, 'offset', transform.offset);
  result = replaceXyz(result, 'scale', transform.scale);
  result = replaceXyz(result, 'rotate', transform.rotate);
  return result;
}

// -----------------------------------------------------------------------
// Editable fields system
// -----------------------------------------------------------------------

/** Default field keys loaded from user config (populated on mount). */
const configuredFieldKeys = ref<{ key: string; enabled: boolean; description?: string }[]>([]);

/** KiCad mandatory fields – always included regardless of settings */
const MANDATORY_KEYS = ['Reference', 'Value', 'Footprint', 'Datasheet', 'Description'];

/** Fallback extra keys if config fails to load */
const FALLBACK_EXTRA_KEYS = [
  { key: 'MF', enabled: true, description: 'Manufacturer' },
  { key: 'MPN', enabled: true, description: 'Manufacturer Part Number' },
  { key: 'DKPN', enabled: true, description: 'Digi-Key Part Number' },
  { key: 'LCSC', enabled: true, description: 'LCSC Part Number' },
];

interface FieldRow {
  key: string;
  value: string;
  mandatory: boolean;
  enabled: boolean;    // optional fields start disabled
  editingKey: boolean; // is the user editing the key name
  description?: string; // non-editable description of the field's purpose
}

/** Map of known field keys to their source value from ImportedComponent */
function fieldSourceMap(c: ImportedComponent): Record<string, string> {
  const f = c.fields;
  return {
    'Reference':   f.reference || 'U',
    'Value':       f.value || c.name || '',
    'Footprint':   f.footprint || '',
    'Datasheet':   f.datasheet || '',
    'Description': f.description || '',
    'MF':          f.manufacturer || '',
    'MPN':         f.mpn || '',
    'DKPN':        f.digikey_pn || '',
    'LCSC':        f.lcsc_pn || '',
    'Mouser':      f.mouser_pn || '',
    'Package':     f.package || '',
  };
}

/** Build the initial fields list from the imported component */
function buildFieldRows(c: ImportedComponent): FieldRow[] {
  const rows: FieldRow[] = [];
  const sourceMap = fieldSourceMap(c);
  const addedKeys = new Set<string>();

  // 1. Always add KiCad mandatory fields first
  for (const key of MANDATORY_KEYS) {
    rows.push({
      key,
      value: sourceMap[key] ?? '',
      mandatory: true,
      enabled: true,
      editingKey: false,
    });
    addedKeys.add(key);
  }

  // 2. Add user-configured extra fields (MF, MPN, DKPN, LCSC, or custom)
  const extras = configuredFieldKeys.value.length
    ? configuredFieldKeys.value
    : FALLBACK_EXTRA_KEYS;

  for (const { key, enabled, description } of extras) {
    if (addedKeys.has(key)) continue;
    rows.push({
      key,
      value: sourceMap[key] ?? '',
      mandatory: false,
      enabled: enabled,
      editingKey: false,
      description: description || FIELD_DESCRIPTIONS[key] || undefined,
    });
    addedKeys.add(key);
  }

  // 3. Add any remaining known fields that have values but aren't in the defaults
  for (const [key, val] of Object.entries(sourceMap)) {
    if (!addedKeys.has(key) && val) {
      rows.push({ key, value: val, mandatory: false, enabled: false, editingKey: false });
      addedKeys.add(key);
    }
  }

  // 4. Extra fields from the import
  if (c.fields.extra) {
    for (const [k, v] of Object.entries(c.fields.extra)) {
      if (v && !addedKeys.has(k)) {
        rows.push({ key: k, value: v, mandatory: false, enabled: false, editingKey: false });
        addedKeys.add(k);
      }
    }
  }

  return rows;
}

const fieldRows = ref<FieldRow[]>(buildFieldRows(props.component));

// Initialize the original footprint value for reset support
fpOriginalValue.value = props.component.fields.footprint || '';

// Load field defaults from backend, then rebuild if available
onMounted(async () => {
  // Use settings composable for field defaults
  if (appSettings.value.fieldDefaults.length) {
    configuredFieldKeys.value = appSettings.value.fieldDefaults.map(f => ({
      key: f.key,
      enabled: f.enabled,
      description: f.description || FIELD_DESCRIPTIONS[f.key] || undefined,
    }));
    fieldRows.value = buildFieldRows(props.component);
  } else {
    // Fall back to backend API
    try {
      const api = (window as any).pywebview?.api;
      if (api?.get_symbol_field_defaults) {
        const r = await api.get_symbol_field_defaults();
        if (r?.success && r.fields?.length) {
          configuredFieldKeys.value = r.fields.map((f: any) => ({
            key: f.key || '',
            enabled: f.enabled === 'true' || f.enabled === true,
            description: f.description || FIELD_DESCRIPTIONS[f.key] || undefined,
          }));
          fieldRows.value = buildFieldRows(props.component);
        }
      }
    } catch {
      // Fall back to defaults silently
    }
  }

  // Auto-load save-to-library data
  await initSaveToLib();
});

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
// Symbol search: replace graphical symbol from existing library
// -----------------------------------------------------------------------

function closeSymSearch() {
  symSearchActive.value = false;
  symSearchResults.value = [];
  symSearchQuery.value = '';
}

function onSymSearchInput() {
  if (symSearchTimer) clearTimeout(symSearchTimer);
  const query = symSearchQuery.value.trim();
  if (!query) {
    symSearchResults.value = [];
    return;
  }
  symSearchLoading.value = true;
  symSearchTimer = setTimeout(() => searchSymbols(query), 300);
}

async function searchSymbols(query: string) {
  const api = getApi();
  if (!api) {
    symSearchLoading.value = false;
    return;
  }
  try {
    const r = await api.importer_search_symbols(query);
    if (r?.success) {
      symSearchResults.value = r.results ?? [];
    } else {
      symSearchResults.value = [];
    }
  } catch {
    symSearchResults.value = [];
  } finally {
    symSearchLoading.value = false;
  }
}

async function selectSymbol(result: SymSearchResult) {
  const api = getApi();
  if (!api) return;
  closeSymSearch();
  selectedSymLoading.value = true;
  selectedSymName.value = `${result.library}:${result.name}`;
  selectedSymSexpr.value = '';
  selectedSymMergedSexpr.value = '';
  symReplaceError.value = '';
  try {
    const r = await api.importer_replace_symbol_graphics(
      props.component.symbol_sexpr || '',
      result.library,
      result.name,
    );
    if (r?.success) {
      selectedSymSexpr.value = r.base_sexpr ?? '';
      selectedSymMergedSexpr.value = r.merged_sexpr ?? '';
    } else {
      symReplaceError.value = r?.error || 'Failed to load symbol';
      selectedSymName.value = '';
    }
  } catch (e: any) {
    symReplaceError.value = e.message || 'Failed to load symbol';
    selectedSymName.value = '';
  } finally {
    selectedSymLoading.value = false;
  }
}

function resetSymbol() {
  selectedSymSexpr.value = '';
  selectedSymMergedSexpr.value = '';
  selectedSymName.value = '';
  symReplaceError.value = '';
  closeSymSearch();
}

// -----------------------------------------------------------------------
// Add Variant actions (moved to VariantImporter.vue side panel)
// -----------------------------------------------------------------------

// -----------------------------------------------------------------------
// Save to Library
// -----------------------------------------------------------------------

async function initSaveToLib() {
  if (saveLibsLoaded.value) return;
  await loadSaveLibraries();
  await loadSaveDefaults();
  saveLibsLoaded.value = true;
}

async function loadSaveLibraries() {
  const api = getApi();
  if (!api) return;
  try {
    const [symRes, fpRes] = await Promise.all([
      api.importer_get_sym_libraries(),
      api.importer_get_fp_libraries(),
    ]);
    if (symRes?.success) {
      saveSymLibraries.value = (symRes.libraries || []).map((l: any) => ({
        nickname: l.nickname,
        uri: l.uri,
      }));
    }
    if (fpRes?.success) {
      saveFpLibraries.value = (fpRes.libraries || []).map((l: any) => ({
        nickname: l.nickname,
        uri: l.uri,
      }));
    }
  } catch { /* ignore */ }
}

async function loadSaveDefaults() {
  // Use settings composable for effective defaults
  const effectiveSym = getEffectiveSymLib();

  if (effectiveSym) {
    const isNickname = saveSymLibraries.value.some(l => l.nickname === effectiveSym);
    if (isNickname) {
      saveSymLib.value = effectiveSym;
      saveSymLibCustom.value = '';
    } else {
      saveSymLib.value = 'custom';
      saveSymLibCustom.value = effectiveSym;
    }
  }

  // For variants, skip auto-populating fp/model defaults (user must opt-in first)
  if (!isVariant.value) {
    const effectiveFp = getEffectiveFpLib();
    const effectiveModels = getEffectiveModelsDir();
    if (effectiveFp) {
      const isNickname = saveFpLibraries.value.some(l => l.nickname === effectiveFp);
      if (isNickname) {
        saveFpLib.value = effectiveFp;
        saveFpLibCustom.value = '';
      } else {
        saveFpLib.value = 'custom';
        saveFpLibCustom.value = effectiveFp;
      }
    }
    if (effectiveModels) {
      saveModelsDir.value = effectiveModels;
      saveModelsDirMode.value = 'custom';
    }
  }

  // Also try backend defaults as fallback
  const api = getApi();
  if (!api) return;
  try {
    const r = await api.importer_get_library_defaults();
    if (r?.success) {
      const savedSym = r.sym_lib || '';
      const savedFp = r.fp_lib || '';
      const savedModels = r.models_dir || '';

      if (savedSym && !saveSymLib.value) {
        const isNickname = saveSymLibraries.value.some(l => l.nickname === savedSym);
        if (isNickname) {
          saveSymLib.value = savedSym;
        } else {
          saveSymLib.value = 'custom';
          saveSymLibCustom.value = savedSym;
        }
      }
      // Skip fp/model backend defaults for variants (user must opt-in)
      if (!isVariant.value) {
        if (savedFp && !saveFpLib.value) {
          const isNickname = saveFpLibraries.value.some(l => l.nickname === savedFp);
          if (isNickname) {
            saveFpLib.value = savedFp;
          } else {
            saveFpLib.value = 'custom';
            saveFpLibCustom.value = savedFp;
          }
        }
        if (savedModels && !saveModelsDir.value) {
          saveModelsDir.value = savedModels;
          saveModelsDirMode.value = 'custom';
        }
      }
    }
  } catch { /* ignore */ }
}

function isKicadLib(uri: string): boolean {
  const kicadVars = ['KICAD_SYMBOL_DIR', 'KICAD_FOOTPRINT_DIR', 'KICAD_3DMODEL_DIR',
    'KICAD10_SYMBOL_DIR', 'KICAD10_FOOTPRINT_DIR', 'KICAD11_SYMBOL_DIR',
    'KICAD11_FOOTPRINT_DIR', 'KICAD12_SYMBOL_DIR', 'KICAD12_FOOTPRINT_DIR'];
  return kicadVars.some(v => uri.includes('${' + v + '}'));
}

async function browseSaveSymLib() {
  const api = getApi();
  if (!api) return;
  try {
    const r = await api.importer_browse_sym_library();
    if (r?.success && r.path) {
      saveSymLib.value = 'custom';
      saveSymLibCustom.value = r.path;
    }
  } catch { /* ignore */ }
}

async function browseSaveFpLib() {
  const api = getApi();
  if (!api) return;
  try {
    const r = await api.importer_browse_fp_library();
    if (r?.success && r.path) {
      saveFpLib.value = 'custom';
      saveFpLibCustom.value = r.path;
    }
  } catch { /* ignore */ }
}

async function browseSaveModelsDir() {
  const api = getApi();
  if (!api) return;
  try {
    const r = await api.importer_browse_models_dir();
    if (r?.success && r.path) {
      saveModelsDir.value = r.path;
      saveModelsDirMode.value = 'custom';
      addModelsDirToHistory(r.path);
    }
  } catch { /* ignore */ }
}

function onModelsDirModeChange(mode: string) {
  if (mode === 'auto') {
    saveModelsDirMode.value = 'auto';
    saveModelsDir.value = '';
  } else if (mode === 'custom') {
    saveModelsDirMode.value = 'custom';
    // Keep current value or clear for manual entry
  } else {
    // It's a history path
    saveModelsDirMode.value = 'history';
    saveModelsDir.value = mode;
  }
}

/** Build the component data dict from current state (with edited fields) */
function buildComponentData(): Record<string, any> {
  const c = props.component;
  const fields: Record<string, any> = {};

  // Map field rows back to the flat field structure
  const knownFieldMap: Record<string, string> = {
    'Reference': 'reference',
    'Value': 'value',
    'Footprint': 'footprint',
    'Datasheet': 'datasheet',
    'Description': 'description',
    'MF': 'manufacturer',
    'MPN': 'mpn',
    'DKPN': 'digikey_pn',
    'Mouser': 'mouser_pn',
    'MSPN': 'mouser_pn',
    'LCSC': 'lcsc_pn',
    'Package': 'package',
  };

  // Initialize all fields with empty defaults
  fields.reference = '';
  fields.value = '';
  fields.footprint = '';
  fields.datasheet = '';
  fields.description = '';
  fields.manufacturer = '';
  fields.mpn = '';
  fields.digikey_pn = '';
  fields.mouser_pn = '';
  fields.lcsc_pn = '';
  fields.package = '';
  fields.extra = {};

  // Apply enabled field rows
  for (const row of fieldRows.value) {
    if (!row.enabled || !row.key) continue;
    const attr = knownFieldMap[row.key];
    if (attr) {
      fields[attr] = row.value || '';
    } else {
      fields.extra[row.key] = row.value || '';
    }
  }

  // If user selected a pre-existing KiCad footprint, reference it directly
  // in the symbol without copying the footprint or 3D model files.
  let fpSexpr: string;
  let stepData: string;
  if (selectedFpName.value) {
    // Existing footprint selected — just use the library reference, don't copy
    fpSexpr = '';
    stepData = '';
  } else {
    fpSexpr = c.footprint_sexpr;
    stepData = c.step_data;
  }
  if (fpSexpr && modelPreviewRef.value) {
    const currentXform = modelPreviewRef.value.getTransform();
    if (currentXform) {
      fpSexpr = updateFootprintTransform(fpSexpr, currentXform);
    }
  }

  // If a replacement symbol was selected, use the merged sexpr
  const symSexpr = selectedSymMergedSexpr.value || c.symbol_sexpr;

  return {
    name: c.name,
    import_method: c.import_method,
    source_info: c.source_info,
    symbol_sexpr: symSexpr,
    footprint_sexpr: fpSexpr,
    step_data: stepData,
    fields,
  };
}

function _getSaveLibPaths() {
  const symNickname = saveSymLib.value !== 'custom' ? saveSymLib.value : '';
  const symPath = saveSymLib.value === 'custom' ? saveSymLibCustom.value : '';
  const fpNickname = saveFpLib.value !== 'custom' ? saveFpLib.value : '';
  const fpPath = saveFpLib.value === 'custom' ? saveFpLibCustom.value : '';
  return { symNickname, symPath, fpNickname, fpPath };
}

async function saveToLibrary() {
  const api = getApi();
  if (!api) return;

  const _hasSym = !!props.component.symbol_sexpr;
  // When user selected a pre-existing footprint, no need to write footprint/model
  const usingExistingFp = !!selectedFpName.value;
  const _hasFp = !usingExistingFp && !!props.component.footprint_sexpr;
  const _hasModel = !usingExistingFp && !!props.component.step_data;
  const { symNickname, symPath, fpNickname, fpPath } = _getSaveLibPaths();

  if (_hasSym && !symNickname && !symPath) {
    saveError.value = 'Please select a symbol library.';
    return;
  }
  if (_hasFp && !fpNickname && !fpPath && (!isVariant.value || variantSaveFp.value)) {
    saveError.value = 'Please select a footprint library.';
    return;
  }

  saveError.value = '';
  saveSuccess.value = '';

  // For variants where user didn't opt-in to saving fp, treat as no fp/model
  const skipVariantFpCheck = isVariant.value && !variantSaveFp.value;
  const checkHasFp = _hasFp && !skipVariantFpCheck;
  const checkHasModel = _hasModel && !skipVariantFpCheck;
  const checkFpNickname = skipVariantFpCheck ? '' : fpNickname;
  const checkFpPath = skipVariantFpCheck ? '' : fpPath;
  const checkModelsDir = skipVariantFpCheck ? '' : saveModelsDir.value;

  // Check for existing files first
  try {
    const checkResult = await api.importer_check_library_existing(
      props.component.name,
      symNickname, symPath,
      checkFpNickname, checkFpPath,
      checkModelsDir,
      _hasSym, checkHasFp, checkHasModel,
    );

    if (checkResult?.success) {
      const anyExists = checkResult.symbol_exists || checkResult.footprint_exists || checkResult.model_exists;
      if (anyExists) {
        overwriteChecks.value = {
          symbolExists: checkResult.symbol_exists,
          footprintExists: checkResult.footprint_exists,
          modelExists: checkResult.model_exists,
          symbolName: checkResult.symbol_name || props.component.name,
          footprintName: checkResult.footprint_name || props.component.name,
          modelName: checkResult.model_name || (props.component.name + '.step'),
        };
        // Default all to overwrite
        symbolAction.value = 'overwrite';
        footprintAction.value = 'overwrite';
        modelAction.value = 'overwrite';
        showOverwriteDialog.value = true;
        return;
      }
    }
  } catch {
    // If check fails, proceed with save anyway
  }

  // No conflicts — save directly
  await doSaveToLibrary(false, false, false, false, false, false);
}

function confirmOverwrite() {
  showOverwriteDialog.value = false;
  doSaveToLibrary(
    symbolAction.value === 'overwrite',
    footprintAction.value === 'overwrite',
    modelAction.value === 'overwrite',
    symbolAction.value === 'ignore',
    footprintAction.value === 'ignore',
    modelAction.value === 'ignore',
  );
}

function cancelOverwrite() {
  showOverwriteDialog.value = false;
}

async function doSaveToLibrary(
  owSymbol: boolean, owFootprint: boolean, owModel: boolean,
  skipSymbol: boolean, skipFootprint: boolean, skipModel: boolean,
) {
  const api = getApi();
  if (!api) return;

  // For variants where user didn't opt-in to saving footprint, clear fp/model paths
  const skipVariantFp = isVariant.value && !variantSaveFp.value;
  const { symNickname, symPath, fpNickname, fpPath } = _getSaveLibPaths();
  const effectiveFpNickname = skipVariantFp ? '' : fpNickname;
  const effectiveFpPath = skipVariantFp ? '' : fpPath;
  const effectiveModelsDir = skipVariantFp ? '' : saveModelsDir.value;

  saveLoading.value = true;
  saveError.value = '';
  saveSuccess.value = '';

  try {
    const componentData = buildComponentData();
    const r = await api.importer_commit_to_library(
      componentData,
      symNickname,
      symPath,
      effectiveFpNickname,
      effectiveFpPath,
      effectiveModelsDir,
      false,  // overwrite (legacy global flag)
      owSymbol,
      owFootprint,
      owModel,
      skipSymbol,
      skipFootprint,
      skipModel,
      true,  // save_as_default
    );

    if (r?.success) {
      const parts: string[] = [];
      if (r.symbol_name) parts.push(`Symbol "${r.symbol_name}" saved`);
      if (r.footprint_path) parts.push(`Footprint saved`);
      if (r.model_paths?.length) parts.push(`${r.model_paths.length} 3D model(s) saved`);
      if (r.footprint_ref) parts.push(`Linked as ${r.footprint_ref}`);
      saveSuccess.value = parts.join(' · ') || 'Saved successfully';

      if (r.warnings?.length) {
        saveSuccess.value += '\n⚠ ' + r.warnings.join('; ');
      }

      recordLastUsedLibraries(
        symNickname || symPath,
        effectiveFpNickname || effectiveFpPath,
        effectiveModelsDir,
      );
    } else {
      saveError.value = r?.error || 'Save failed';
    }
  } catch (e: any) {
    saveError.value = e.message || 'Save failed';
  } finally {
    saveLoading.value = false;
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
            <template v-if="selectedSymSexpr">Symbol Comparison</template>
            <template v-else>Symbol Preview</template>
          </div>
          <KicadPreview
            :sexpr="component.symbol_sexpr"
            type="symbol"
            :compareSexpr="selectedSymSexpr || undefined"
            primaryLabel="Imported"
            :compareLabel="selectedSymName || 'Selected'"
          />
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
            ref="modelPreviewRef"
            :stepData="component.step_data || undefined"
            :footprintSexpr="component.footprint_sexpr || undefined"
            :modelTransform="importedModelTransform || undefined"
            :compareStepData="selectedFpStepData || undefined"
            :compareFootprintSexpr="selectedFpSexpr || undefined"
            :compareModelTransform="selectedFpModelTransform || undefined"
            primaryLabel="Imported"
            :compareLabel="selectedFpName || 'Selected'"
          />
        </div>
      </div>

      <!-- ===== Select Symbol / Footprint Row ===== -->
      <div class="selector-row">
        <!-- Select Symbol Graphics -->
        <div class="selector-card">
          <div class="selector-header">
            <span class="material-icons selector-icon">memory</span>
            <span class="selector-title">Select Symbol</span>
          </div>
          <div class="selector-body">
            <div v-if="selectedSymName" class="selector-badge">
              <span class="material-icons" style="font-size: 13px; color: var(--accent-color)">check_circle</span>
              <span class="selector-badge-name">{{ selectedSymName }}</span>
              <button class="fp-btn" title="Clear" @click="resetSymbol"><span class="material-icons">close</span></button>
            </div>
            <div class="fp-search-input-row selector-search-row">
              <span class="material-icons fp-search-icon">search</span>
              <input
                ref="symSearchInputRef"
                v-model="symSearchQuery"
                class="field-input fp-search-input"
                placeholder="Search symbols…"
                @input="onSymSearchInput()"
                @focus="symSearchActive = true"
              />
              <span v-if="symSearchLoading" class="material-icons fp-spin-icon">sync</span>
              <button class="fp-btn" title="Reload libraries" :disabled="libReloading" @click="reloadLibraries()">
                <span class="material-icons" :class="{ 'fp-spin-icon': libReloading }">refresh</span>
              </button>
            </div>
            <div v-if="symSearchResults.length" class="fp-results selector-results">
              <div v-for="sr in symSearchResults" :key="sr.library + ':' + sr.name" class="fp-drop-item" @click="selectSymbol(sr)">
                <span class="fp-drop-lib">{{ sr.library }}</span>
                <span class="fp-drop-name">{{ sr.name }}</span>
              </div>
            </div>
            <div v-else-if="symSearchQuery.trim() && !symSearchLoading && symSearchActive" class="fp-drop-hint">No matches</div>
            <div v-if="selectedSymLoading" class="selector-loading"><span class="material-icons fp-spin-icon">sync</span> Loading…</div>
            <div v-if="symReplaceError" class="notice error selector-notice"><span class="material-icons">error_outline</span> {{ symReplaceError }}</div>
          </div>
        </div>

        <!-- Select Footprint -->
        <div class="selector-card">
          <div class="selector-header">
            <span class="material-icons selector-icon">crop_square</span>
            <span class="selector-title">Select Footprint</span>
          </div>
          <div class="selector-body">
            <div v-if="selectedFpName" class="selector-badge">
              <span class="material-icons" style="font-size: 13px; color: var(--accent-color)">check_circle</span>
              <span class="selector-badge-name">{{ selectedFpName }}</span>
              <button class="fp-btn" title="Clear" @click="resetFootprint"><span class="material-icons">close</span></button>
            </div>
            <div class="fp-search-input-row selector-search-row">
              <span class="material-icons fp-search-icon">search</span>
              <input
                ref="fpSearchInputRef"
                v-model="fpSearchQuery"
                class="field-input fp-search-input"
                placeholder="Search footprints…"
                @input="onFpSearchInput()"
                @focus="fpSearchActive = true"
              />
              <span v-if="fpSearchLoading" class="material-icons fp-spin-icon">sync</span>
              <button class="fp-btn" title="Reload libraries" :disabled="libReloading" @click="reloadLibraries()">
                <span class="material-icons" :class="{ 'fp-spin-icon': libReloading }">refresh</span>
              </button>
            </div>
            <div v-if="fpSearchResults.length" class="fp-results selector-results">
              <div v-for="sr in fpSearchResults" :key="sr.library + ':' + sr.name" class="fp-drop-item" @click="selectFootprint(sr)">
                <span class="fp-drop-lib">{{ sr.library }}</span>
                <span class="fp-drop-name">{{ sr.name }}</span>
              </div>
            </div>
            <div v-else-if="fpSearchQuery.trim() && !fpSearchLoading && fpSearchActive" class="fp-drop-hint">No matches</div>
            <div v-if="selectedFpLoading" class="selector-loading"><span class="material-icons fp-spin-icon">sync</span> Loading…</div>
          </div>
        </div>
      </div>

      <div class="details-columns details-columns-single">
        <!-- ===== Symbol Fields ===== -->
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
                    <div class="field-key-wrapper">
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
                      <span v-if="row.description" class="field-desc-badge" :title="row.description">{{ row.description }}</span>
                    </div>
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

          <!-- ===== Save to Library Section (always open) ===== -->
          <div class="detail-section">
            <div class="section-label">
              <span class="material-icons section-icon">save</span>
              Save to Library
            </div>

            <div class="save-panel">
              <div class="variant-hint">
                Save the imported symbol, footprint, and 3D model to your libraries with proper linking.
              </div>

              <!-- Symbol library selector (always visible, greyed out when no symbol) -->
              <label :class="['variant-label', { 'label-disabled': !hasSym }]">
                Symbol Library
                <span v-if="!hasSym" class="label-status">(no symbol loaded)</span>
              </label>
              <div :class="['save-lib-row', { 'row-disabled': !hasSym }]">
                <select v-model="saveSymLib" class="variant-select save-lib-select" :disabled="!hasSym">
                  <option value="">— Select symbol library —</option>
                  <optgroup label="Custom Libraries">
                    <template v-for="lib in saveSymLibraries" :key="lib.nickname">
                      <option v-if="!isKicadLib(lib.uri)" :value="lib.nickname">
                        {{ lib.nickname }}
                      </option>
                    </template>
                  </optgroup>
                  <optgroup label="KiCad Libraries">
                    <template v-for="lib in saveSymLibraries" :key="lib.nickname">
                      <option v-if="isKicadLib(lib.uri)" :value="lib.nickname">
                        {{ lib.nickname }}
                      </option>
                    </template>
                  </optgroup>
                  <option value="custom">Browse for file…</option>
                </select>
                <button class="fp-btn save-browse-btn" @click="browseSaveSymLib" :disabled="!hasSym" title="Browse for .kicad_sym file">
                  <span class="material-icons">folder_open</span>
                </button>
              </div>
              <div v-if="hasSym && saveSymLib === 'custom' && saveSymLibCustom" class="save-custom-path">
                <span class="material-icons" style="font-size: 14px">description</span>
                {{ saveSymLibCustom }}
              </div>

              <!-- Footprint library selector (greyed out for variants until user opts in) -->
              <label :class="['variant-label', { 'label-disabled': !fpSaveEnabled }]">
                Footprint Library
                <span v-if="!hasFp" class="label-status">(no footprint loaded)</span>
                <span v-else-if="isVariant && !variantSaveFp" class="label-status">(using KiCad library — click to save a copy)</span>
              </label>
              <div
                :class="['save-lib-row', { 'row-disabled': !fpSaveEnabled }]"
                @click="isVariant && !variantSaveFp && hasFp ? onVariantFpClick() : undefined"
                :style="isVariant && !variantSaveFp && hasFp ? 'cursor: pointer' : ''"
              >
                <select v-model="saveFpLib" class="variant-select save-lib-select" :disabled="!fpSaveEnabled">
                  <option value="">— Select footprint library —</option>
                  <optgroup label="Custom Libraries">
                    <template v-for="lib in saveFpLibraries" :key="lib.nickname">
                      <option v-if="!isKicadLib(lib.uri)" :value="lib.nickname">
                        {{ lib.nickname }}
                      </option>
                    </template>
                  </optgroup>
                  <optgroup label="KiCad Libraries">
                    <template v-for="lib in saveFpLibraries" :key="lib.nickname">
                      <option v-if="isKicadLib(lib.uri)" :value="lib.nickname">
                        {{ lib.nickname }}
                      </option>
                    </template>
                  </optgroup>
                  <option value="custom">Browse for directory…</option>
                </select>
                <button class="fp-btn save-browse-btn" @click="browseSaveFpLib" :disabled="!fpSaveEnabled" title="Browse for .pretty directory">
                  <span class="material-icons">folder_open</span>
                </button>
              </div>
              <!-- Variant footprint save confirmation dialog -->
              <div v-if="showVariantFpConfirm" class="variant-fp-confirm">
                <span class="material-icons" style="font-size: 16px; color: var(--accent)">info</span>
                <span>This variant uses a standard KiCad footprint. Would you like to save a copy to your own library?</span>
                <div class="variant-fp-confirm-actions">
                  <button class="action-btn primary small" @click="confirmVariantSaveFp">Yes, save a copy</button>
                  <button class="action-btn secondary small" @click="cancelVariantSaveFp">No</button>
                </div>
              </div>
              <div v-if="fpSaveEnabled && saveFpLib === 'custom' && saveFpLibCustom" class="save-custom-path">
                <span class="material-icons" style="font-size: 14px">folder</span>
                {{ saveFpLibCustom }}
              </div>

              <!-- 3D Models directory (greyed out for variants until user opts in) -->
              <label :class="['variant-label', { 'label-disabled': !modelSaveEnabled }]">
                3D Models Directory
                <span v-if="!hasModel" class="label-status">(no 3D model loaded)</span>
                <span v-else-if="isVariant && !variantSaveFp" class="label-status">(using KiCad 3D model)</span>
                <span v-else class="variant-hint-inline">(optional — defaults to footprint lib/3dmodels)</span>
              </label>
              <div :class="['save-lib-row', { 'row-disabled': !modelSaveEnabled }]">
                <select
                  class="variant-select save-lib-select models-dir-select"
                  :disabled="!modelSaveEnabled"
                  :value="saveModelsDirMode === 'auto' ? 'auto' : (saveModelsDirMode === 'custom' ? 'custom' : saveModelsDir)"
                  @change="(e: Event) => onModelsDirModeChange((e.target as HTMLSelectElement).value)"
                >
                  <option value="auto">Auto: &lt;footprint_lib&gt;/3dmodels</option>
                  <template v-for="dir in appSettings.modelsDirHistory" :key="dir">
                    <option :value="dir">{{ dir }}</option>
                  </template>
                  <option value="custom">Custom path…</option>
                </select>
                <button class="fp-btn save-browse-btn" @click="browseSaveModelsDir" :disabled="!modelSaveEnabled" title="Browse for 3D models directory">
                  <span class="material-icons">folder_open</span>
                </button>
              </div>
              <div v-if="modelSaveEnabled && saveModelsDirMode === 'custom'" class="save-models-custom-row">
                <input
                  v-model="saveModelsDir"
                  class="field-input save-models-input"
                  placeholder="Enter custom 3D models directory path…"
                  :disabled="!modelSaveEnabled"
                />
              </div>

              <!-- Action -->
              <div class="variant-actions">
                <button
                  class="action-btn primary"
                  @click="saveToLibrary"
                  :disabled="saveLoading || !hasAnythingToSave"
                >
                  <span class="material-icons">{{ saveLoading ? 'sync' : 'save' }}</span>
                  {{ saveLoading ? 'Saving…' : 'Save to Library' }}
                </button>
              </div>

              <div v-if="saveSuccess" class="notice success" style="margin-top: 0.5rem">
                <span class="material-icons">check_circle</span>
                {{ saveSuccess }}
                <button class="close-part-btn" @click="emit('close')" title="Close this part and reset the importer">
                  <span class="material-icons">close</span> Close Part
                </button>
              </div>
              <div v-if="saveError" class="notice error" style="margin-top: 0.5rem">
                <span class="material-icons">error_outline</span>
                {{ saveError }}
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


      </div>
    </div>
  </div>

  <!-- Overwrite confirmation modal overlay -->
  <Transition name="modal-fade">
    <div v-if="showOverwriteDialog" class="overwrite-overlay" @click.self="cancelOverwrite">
      <div class="overwrite-modal">
        <div class="overwrite-modal-header">
          <span class="material-icons overwrite-modal-warn">warning</span>
          <span>Files Already Exist</span>
        </div>
        <div class="overwrite-modal-body">
          <p class="overwrite-hint">Choose an action for each existing file:</p>

          <!-- Symbol row -->
          <div v-if="overwriteChecks.symbolExists" class="overwrite-item">
            <span class="material-icons overwrite-item-icon">memory</span>
            <span class="overwrite-item-name">Symbol: <strong>{{ overwriteChecks.symbolName }}</strong></span>
            <div class="overwrite-radios">
              <label class="overwrite-radio"><input type="radio" v-model="symbolAction" value="overwrite" /> Overwrite</label>
              <label class="overwrite-radio"><input type="radio" v-model="symbolAction" value="append" /> Append</label>
              <label class="overwrite-radio"><input type="radio" v-model="symbolAction" value="ignore" /> Ignore</label>
            </div>
          </div>

          <!-- Footprint row -->
          <div v-if="overwriteChecks.footprintExists" class="overwrite-item">
            <span class="material-icons overwrite-item-icon">crop_square</span>
            <span class="overwrite-item-name">Footprint: <strong>{{ overwriteChecks.footprintName }}</strong></span>
            <div class="overwrite-radios">
              <label class="overwrite-radio"><input type="radio" v-model="footprintAction" value="overwrite" /> Overwrite</label>
              <label class="overwrite-radio"><input type="radio" v-model="footprintAction" value="append" /> Append</label>
              <label class="overwrite-radio"><input type="radio" v-model="footprintAction" value="ignore" /> Ignore</label>
            </div>
          </div>

          <!-- 3D Model row -->
          <div v-if="overwriteChecks.modelExists" class="overwrite-item">
            <span class="material-icons overwrite-item-icon">view_in_ar</span>
            <span class="overwrite-item-name">3D Model: <strong>{{ overwriteChecks.modelName }}</strong></span>
            <div class="overwrite-radios">
              <label class="overwrite-radio"><input type="radio" v-model="modelAction" value="overwrite" /> Overwrite</label>
              <label class="overwrite-radio"><input type="radio" v-model="modelAction" value="append" /> Append</label>
              <label class="overwrite-radio"><input type="radio" v-model="modelAction" value="ignore" /> Ignore</label>
            </div>
          </div>
        </div>
        <div class="overwrite-modal-actions">
          <button class="action-btn secondary" @click="cancelOverwrite">Cancel</button>
          <button class="action-btn primary" @click="confirmOverwrite">
            <span class="material-icons">save</span> Confirm Save
          </button>
        </div>
      </div>
    </div>
  </Transition>
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

.details-columns-single {
  grid-template-columns: 1fr;
}

/* ===== Selector Row (Symbol / Footprint pickers) ===== */
.selector-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
  max-width: 1200px;
  margin-bottom: 0.75rem;
}

.selector-card {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
  background-color: var(--bg-secondary);
}

.selector-header {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.35rem 0.6rem;
  border-bottom: 1px solid var(--border-color);
  background: color-mix(in srgb, var(--accent-color) 8%, var(--bg-tertiary));
}

.selector-icon {
  font-size: 0.95rem;
  color: var(--accent-color);
}

.selector-title {
  font-weight: 700;
  font-size: 0.78rem;
  color: var(--text-primary);
}

.selector-body {
  padding: 0.4rem 0.5rem;
}

.selector-badge {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  margin-bottom: 0.35rem;
  padding: 0.2rem 0.4rem;
  background: color-mix(in srgb, var(--accent-color) 12%, var(--bg-tertiary));
  border-radius: var(--radius-sm);
  font-size: 0.73rem;
  color: var(--text-primary);
}

.selector-badge-name {
  flex: 1;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.selector-search-row {
  margin: 0;
}

.selector-results {
  max-height: 150px;
}

.selector-loading {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.73rem;
  color: var(--text-secondary);
  padding: 0.25rem 0;
}

.selector-notice {
  margin: 0.3rem 0 0 !important;
  padding: 0.25rem 0.4rem !important;
  font-size: 0.72rem !important;
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
.field-th-val { flex: 1; }
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
  width: 140px;
}
.field-key-wrapper {
  display: flex;
  flex-direction: column;
  gap: 0;
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
.field-desc-badge {
  font-size: 0.65rem;
  color: var(--text-secondary);
  font-weight: 400;
  font-style: italic;
  opacity: 0.7;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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

.card-actions {
  padding: 0.5rem 0.6rem;
  border-top: 1px solid var(--border-color);
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

/* ===== Symbol Search Section ===== */
.sym-search-body {
  padding: 0.6rem;
}

.sym-search-hint {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-bottom: 0.5rem;
  line-height: 1.4;
}

.sym-selected-badge {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  margin-bottom: 0.5rem;
  padding: 0.3rem 0.5rem;
  background: color-mix(in srgb, var(--accent-color) 12%, var(--bg-tertiary));
  border-radius: var(--radius-sm);
  font-size: 0.78rem;
  color: var(--text-primary);
}

.sym-selected-name {
  flex: 1;
  font-weight: 600;
}

.sym-results {
  max-height: 200px;
}

.sym-loading {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.4rem 0;
  font-size: 0.78rem;
  color: var(--text-secondary);
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
  flex-wrap: wrap;
  gap: 0.4rem;
  font-size: 0.78rem;
  padding: 0.4rem 0.6rem;
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, #27ae60 10%, var(--bg-tertiary));
  color: #27ae60;
}

.close-part-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  margin-left: auto;
  padding: 0.2rem 0.5rem;
  font-size: 0.72rem;
  font-weight: 500;
  color: var(--text-secondary);
  background: var(--bg-secondary, #f0f2f5);
  border: 1px solid var(--border-color, #e1e4e8);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
}

.close-part-btn:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary, #e4e6e9);
}

.close-part-btn .material-icons {
  font-size: 0.85rem;
}

/* ===== Save to Library Section ===== */
.save-lib-row {
  display: flex;
  align-items: center;
  gap: 0;
}

.save-lib-select {
  flex: 1;
  border-top-right-radius: 0 !important;
  border-bottom-right-radius: 0 !important;
}

.save-browse-btn {
  border-top-left-radius: 0 !important;
  border-bottom-left-radius: 0 !important;
  height: auto;
  padding: 0.35rem 0.5rem !important;
}

.save-browse-btn .material-icons {
  font-size: 0.95rem;
}

.save-custom-path {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  margin-top: 0.25rem;
  padding: 0.25rem 0.5rem;
  background: color-mix(in srgb, var(--accent-color) 8%, var(--bg-tertiary));
  border-radius: var(--radius-sm);
  font-size: 0.72rem;
  color: var(--text-secondary);
  font-family: monospace;
  word-break: break-all;
}

/* Variant footprint save confirmation banner */
.variant-fp-confirm {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
  margin-top: 0.35rem;
  padding: 0.5rem 0.65rem;
  background: color-mix(in srgb, var(--accent-color) 10%, var(--bg-tertiary));
  border: 1px solid color-mix(in srgb, var(--accent-color) 30%, var(--border-color));
  border-radius: var(--radius-sm);
  font-size: 0.78rem;
  color: var(--text-primary);
}

.variant-fp-confirm-actions {
  display: flex;
  gap: 0.4rem;
  margin-left: auto;
}

.variant-fp-confirm-actions .action-btn.small {
  padding: 0.2rem 0.5rem;
  font-size: 0.72rem;
  min-height: unset;
}

.save-models-input {
  flex: 1;
  border-top-right-radius: 0 !important;
  border-bottom-right-radius: 0 !important;
}

/* Overwrite confirmation modal */
.overwrite-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(3px);
}

.overwrite-modal {
  background: #ffffff;
  color: #1a1a1a;
  border: 1px solid #f39c12;
  border-radius: 10px;
  padding: 1.2rem 1.4rem;
  min-width: 380px;
  max-width: 520px;
  width: 90vw;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.25);
  animation: modal-enter 0.2s ease-out;
}

@keyframes modal-enter {
  from { opacity: 0; transform: translateY(-12px) scale(0.97); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}

.overwrite-modal-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 1rem;
  font-weight: 600;
  color: #1a1a1a;
  margin-bottom: 0.75rem;
}

.overwrite-modal-warn {
  font-size: 1.4rem;
  color: #f39c12;
}

.overwrite-modal-body {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.overwrite-hint {
  font-size: 0.78rem;
  color: #555;
  margin: 0 0 0.2rem 0;
}

.overwrite-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.82rem;
  color: #1a1a1a;
  padding: 0.45rem 0.55rem;
  border-radius: 6px;
  background: #f7f7f8;
  border: 1px solid #e0e0e0;
}

.overwrite-item-icon {
  font-size: 1rem;
  color: #666;
}

.overwrite-item-name {
  flex: 1;
  min-width: 120px;
}

.overwrite-radios {
  display: flex;
  gap: 0.75rem;
  margin-left: auto;
}

.overwrite-radio {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.76rem;
  color: #555;
  cursor: pointer;
  white-space: nowrap;
}

.overwrite-radio:hover {
  color: #1a1a1a;
}

.overwrite-radio input[type="radio"] {
  margin: 0;
  accent-color: #2196f3;
}

.overwrite-modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 0.75rem;
  padding-top: 0.6rem;
  border-top: 1px solid #e0e0e0;
}

/* Modal transition */
.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity 0.2s ease;
}
.modal-fade-enter-active .overwrite-modal,
.modal-fade-leave-active .overwrite-modal {
  transition: transform 0.2s ease, opacity 0.2s ease;
}
.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}
.modal-fade-enter-from .overwrite-modal {
  transform: translateY(-12px) scale(0.97);
}
.modal-fade-leave-to .overwrite-modal {
  transform: translateY(8px) scale(0.97);
}

.variant-hint-inline {
  font-weight: 400;
  font-style: italic;
  font-size: 0.68rem;
  color: var(--text-secondary);
}

/* Save to Library panel (always open) */
.save-panel {
  padding: 0.5rem 0.75rem;
}

.label-disabled {
  opacity: 0.45;
}

.label-status {
  font-weight: 400;
  font-style: italic;
  font-size: 0.68rem;
}

.row-disabled {
  opacity: 0.45;
  pointer-events: none;
}

.save-models-custom-row {
  margin-top: 0.25rem;
}

.models-dir-select {
  font-size: 0.75rem;
}
</style>
