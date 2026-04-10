/**
 * App-wide settings composable.
 * Persists settings to localStorage and syncs field defaults with the Python backend.
 */
import { ref, watch } from 'vue';
import { getApi } from './useApi';

// -----------------------------------------------------------------------
// Types
// -----------------------------------------------------------------------

export interface FieldDefault {
  key: string;
  enabled: boolean;
  /** Non-editable description identifying the purpose of this field */
  description?: string;
}

/** Descriptions for the built-in configurable fields */
export const FIELD_DESCRIPTIONS: Record<string, string> = {
  MF: 'Manufacturer',
  MPN: 'Manufacturer Part Number',
  DKPN: 'Digi-Key Part Number',
  LCSC: 'LCSC Part Number',
};

export interface AppSettings {
  /** Symbol field defaults (ordered list of field key + enabled) */
  fieldDefaults: FieldDefault[];
  /** Default symbol library nickname (or absolute path) */
  defaultSymLib: string;
  /** Default footprint library nickname (or absolute path) */
  defaultFpLib: string;
  /** Default 3D model directory */
  defaultModelsDir: string;
  /** Recently-used 3D model directories */
  modelsDirHistory: string[];
  /** Last-used symbol library (overrides default after first save) */
  lastSymLib: string;
  /** Last-used footprint library */
  lastFpLib: string;
  /** Last-used 3D models directory */
  lastModelsDir: string;
  /** Default symbols for variant import per component type */
  variantDefaultSymbols: Record<string, { library: string; symbol: string }>;
}

// -----------------------------------------------------------------------
// Singleton reactive state
// -----------------------------------------------------------------------

const STORAGE_KEY = 'kiassist_settings';

const defaults: AppSettings = {
  fieldDefaults: [
    { key: 'MF', enabled: true, description: 'Manufacturer' },
    { key: 'MPN', enabled: true, description: 'Manufacturer Part Number' },
    { key: 'DKPN', enabled: true, description: 'Digi-Key Part Number' },
    { key: 'LCSC', enabled: true, description: 'LCSC Part Number' },
  ],
  defaultSymLib: '',
  defaultFpLib: '',
  defaultModelsDir: '',
  modelsDirHistory: [],
  lastSymLib: '',
  lastFpLib: '',
  lastModelsDir: '',
  variantDefaultSymbols: {
    resistor:  { library: 'Device', symbol: 'R_Small_US' },
    capacitor: { library: 'Device', symbol: 'C_Small' },
    inductor:  { library: 'Device', symbol: 'L_Small' },
    diode:     { library: 'Device', symbol: 'D_Small' },
  },
};

function loadFromStorage(): AppSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return { ...defaults, ...parsed };
    }
  } catch { /* ignore */ }
  return { ...defaults };
}

function saveToStorage(s: AppSettings) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch { /* ignore */ }
}

const settings = ref<AppSettings>(loadFromStorage());

// Persist on every change (debounced to avoid excessive writes)
let _saveTimer: ReturnType<typeof setTimeout> | null = null;
watch(settings, (v) => {
  if (_saveTimer) clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => saveToStorage(v), 500);
}, { deep: true });

// -----------------------------------------------------------------------
// Public API
// -----------------------------------------------------------------------

export function useAppSettings() {
  /** Load field defaults from the Python backend (one-time on app start) */
  async function loadFieldDefaultsFromBackend() {
    const api = getApi();
    if (!api?.get_symbol_field_defaults) return;
    try {
      const r = await api.get_symbol_field_defaults();
      if (r?.success && r.fields?.length) {
        settings.value.fieldDefaults = r.fields.map((f: any) => ({
          key: f.key || '',
          enabled: f.enabled === 'true' || f.enabled === true,
          description: f.description || FIELD_DESCRIPTIONS[f.key] || undefined,
        }));
      }
    } catch { /* ignore */ }
  }

  /** Save field defaults to the Python backend */
  async function saveFieldDefaultsToBackend() {
    const api = getApi();
    if (!api?.set_symbol_field_defaults) return { success: false, error: 'No API' };
    try {
      const payload = settings.value.fieldDefaults.map(f => ({
        key: f.key,
        enabled: f.enabled ? 'true' : 'false',
      }));
      const r = await api.set_symbol_field_defaults(payload);
      return r;
    } catch (e: any) {
      return { success: false, error: e.message || 'Save failed' };
    }
  }

  /** Load library defaults from the Python backend */
  async function loadLibraryDefaultsFromBackend() {
    const api = getApi();
    if (!api?.importer_get_library_defaults) return;
    try {
      const r = await api.importer_get_library_defaults();
      if (r?.success) {
        if (r.sym_lib && !settings.value.defaultSymLib) {
          settings.value.defaultSymLib = r.sym_lib;
        }
        if (r.fp_lib && !settings.value.defaultFpLib) {
          settings.value.defaultFpLib = r.fp_lib;
        }
        if (r.models_dir && !settings.value.defaultModelsDir) {
          settings.value.defaultModelsDir = r.models_dir;
        }
      }
    } catch { /* ignore */ }
  }

  /** Get the effective symbol library to use (last-used > default) */
  function getEffectiveSymLib(): string {
    return settings.value.lastSymLib || settings.value.defaultSymLib;
  }

  /** Get the effective footprint library */
  function getEffectiveFpLib(): string {
    return settings.value.lastFpLib || settings.value.defaultFpLib;
  }

  /** Get the effective 3D models directory */
  function getEffectiveModelsDir(): string {
    return settings.value.lastModelsDir || settings.value.defaultModelsDir;
  }

  /** Record a models directory in history */
  function addModelsDirToHistory(dir: string) {
    if (!dir) return;
    const hist = settings.value.modelsDirHistory.filter(d => d !== dir);
    hist.unshift(dir);
    // Keep at most 10 entries
    settings.value.modelsDirHistory = hist.slice(0, 10);
  }

  /** Record last-used libraries after a successful save */
  function recordLastUsedLibraries(symLib: string, fpLib: string, modelsDir: string) {
    if (symLib) settings.value.lastSymLib = symLib;
    if (fpLib) settings.value.lastFpLib = fpLib;
    if (modelsDir) {
      settings.value.lastModelsDir = modelsDir;
      addModelsDirToHistory(modelsDir);
    }
  }

  return {
    settings,
    loadFieldDefaultsFromBackend,
    saveFieldDefaultsToBackend,
    loadLibraryDefaultsFromBackend,
    getEffectiveSymLib,
    getEffectiveFpLib,
    getEffectiveModelsDir,
    addModelsDirToHistory,
    recordLastUsedLibraries,
  };
}
