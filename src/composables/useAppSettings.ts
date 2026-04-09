/**
 * App-wide settings composable.
 * Persists settings to localStorage and syncs field defaults with the Python backend.
 */
import { ref, watch } from 'vue';

// -----------------------------------------------------------------------
// Types
// -----------------------------------------------------------------------

export interface FieldDefault {
  key: string;
  enabled: boolean;
}

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
}

// -----------------------------------------------------------------------
// Singleton reactive state
// -----------------------------------------------------------------------

const STORAGE_KEY = 'kiassist_settings';

const defaults: AppSettings = {
  fieldDefaults: [
    { key: 'Reference', enabled: true },
    { key: 'Value', enabled: true },
    { key: 'Footprint', enabled: true },
    { key: 'Datasheet', enabled: true },
    { key: 'Description', enabled: true },
    { key: 'MF', enabled: true },
    { key: 'MPN', enabled: true },
    { key: 'DKPN', enabled: true },
    { key: 'LCSC', enabled: true },
  ],
  defaultSymLib: '',
  defaultFpLib: '',
  defaultModelsDir: '',
  modelsDirHistory: [],
  lastSymLib: '',
  lastFpLib: '',
  lastModelsDir: '',
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

// Persist on every change
watch(settings, (v) => saveToStorage(v), { deep: true });

// -----------------------------------------------------------------------
// Public API
// -----------------------------------------------------------------------

export function useAppSettings() {
  /** Load field defaults from the Python backend (one-time on app start) */
  async function loadFieldDefaultsFromBackend() {
    const api = (window as any).pywebview?.api;
    if (!api?.get_symbol_field_defaults) return;
    try {
      const r = await api.get_symbol_field_defaults();
      if (r?.success && r.fields?.length) {
        settings.value.fieldDefaults = r.fields.map((f: any) => ({
          key: f.key || '',
          enabled: f.enabled === 'true' || f.enabled === true,
        }));
      }
    } catch { /* ignore */ }
  }

  /** Save field defaults to the Python backend */
  async function saveFieldDefaultsToBackend() {
    const api = (window as any).pywebview?.api;
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
    const api = (window as any).pywebview?.api;
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
