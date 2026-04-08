// Global type definitions for pywebview API

export interface KiCadInstance {
  socket_path: string;
  project_name: string;
  display_name: string;
  version: string;
  project_path: string;
  pcb_path: string;
  schematic_path: string;
  pcb_open: boolean;
  schematic_open: boolean;
}

export interface RecentProject {
  path: string;
  name: string;
  last_opened: string;
  pcb_path?: string;
  schematic_path?: string;
}

export interface ApiResult {
  success: boolean;
  error?: string;
  warning?: string;
  cancelled?: boolean;
}

export interface SendMessageResult extends ApiResult {
  response?: string;
}

export interface StreamPollResult extends ApiResult {
  text?: string;
  thinking?: string;
  done?: boolean;
}

export interface LLMLogEntry {
  id: string;
  timestamp: number;
  provider: string;
  model: string;
  system_prompt: string;
  messages: Array<{
    role: string;
    content: string;
    tool_calls?: Array<{ id: string; name: string; arguments: Record<string, unknown> }>;
    tool_results?: Array<{ tool_call_id: string; content: string; is_error: boolean }>;
  }>;
  tool_count: number;
  tool_names: string[];
  response_text: string;
  response_tool_calls: Array<{ id: string; name: string; arguments: Record<string, unknown> }>;
  usage: Record<string, number>;
  duration_ms: number;
  is_stream: boolean;
  error: string;
  done: boolean;
}

export interface LLMLogResult extends ApiResult {
  entries: LLMLogEntry[];
}

export interface ProjectValidationResult extends ApiResult {
  valid?: boolean;
  project_path?: string;
  project_dir?: string;
  project_name?: string;
  pcb_path?: string;
  schematic_path?: string;
}

export interface BrowseProjectResult extends ProjectValidationResult {
  path?: string;
}

export interface ProjectsListResult extends ApiResult {
  open_projects: KiCadInstance[];
  recent_projects: RecentProject[];
}

// Requirements Wizard types
export interface WizardQuestion {
  id: string;
  category: string;
  question: string;
  placeholder?: string;
  multiline?: boolean;
}

export interface WizardQuestionsResult extends ApiResult {
  questions?: WizardQuestion[];
}

export interface RequirementsFileResult extends ApiResult {
  requirements_exists?: boolean;
  requirements_path?: string;
  todo_exists?: boolean;
  todo_path?: string;
}

export interface RequirementsContentResult extends ApiResult {
  content?: string;
}

export interface SaveRequirementsResult extends ApiResult {
  saved_files?: string[];
}

export interface SynthesizeResult extends ApiResult {
  requirements?: string;
  todo?: string;
}

// Context Lifecycle types
export interface ContextLifecycleQA {
  question: string;
  answer: string;
}

export interface ContextLifecycleState extends ApiResult {
  state?: string;
  raw_context?: string;
  questions?: string[];
  current_question_index?: number;
  answers?: ContextLifecycleQA[];
  requirements?: string;
  synthesized_context?: string;
  error?: string;
}

export interface InjectTestNoteResult extends ApiResult {
  message?: string;
  schematic_path?: string;
  created_new?: boolean;
}

// Multi-provider AI types
export interface ProviderModel {
  id: string;
  name: string;
}

export interface ProviderInfo {
  id: string;
  name: string;
  models: ProviderModel[];
  default_model: string;
  key_url: string;
  key_prefix: string;
  key_min_length: number;
  has_key: boolean;
  /** Base URL for local model providers (id === 'local') */
  base_url?: string;
  /** Server status for Gemma 4 provider (id === 'gemma4') */
  server_status?: GemmaServerStatus;
}

export interface ModelSelection {
  provider: string;
  model: string;
}

export interface ModelConfig {
  primary: ModelSelection;
  secondary: ModelSelection;
}

export interface ModelConfigResult extends ApiResult, ModelConfig {}

export interface ProvidersResult extends ApiResult {
  providers?: ProviderInfo[];
  current_provider?: string;
  current_model?: string;
  secondary_provider?: string;
  secondary_model?: string;
}

export interface LocalModelsResult extends ApiResult {
  models: ProviderModel[];
  base_url?: string;
}

// Gemma 4 local model types
export interface GemmaModelInfo {
  id: string;
  name: string;
  filename: string;
  size_label: string;
  size_bytes: number;
  description: string;
  context_window: number;
  release_tag: string;
  downloaded: boolean;
  path: string;
}

export interface GemmaServerStatus {
  running: boolean;
  url: string | null;
  model_id: string | null;
  port: number;
}

export interface GemmaModelsResult extends ApiResult {
  models: GemmaModelInfo[];
  server_status: GemmaServerStatus;
}

export interface GemmaDownloadProgress {
  model_id: string;
  filename: string;
  total_bytes: number;
  downloaded_bytes: number;
  speed_bytes_per_sec: number;
  eta_seconds: number;
  status: 'idle' | 'downloading' | 'completed' | 'error' | 'cancelled';
  error: string;
  percent: number;
}

export interface GemmaServerResult extends ApiResult {
  url?: string;
  model_id?: string;
}

// Session management types
export interface SessionInfo {
  session_id: string;
  started_at: string;
  last_at: string;
  message_count: number;
}

export interface SessionsResult extends ApiResult {
  sessions: SessionInfo[];
}

export interface ResumeSessionResult extends ApiResult {
  session_id?: string;
  messages?: Array<{ role: string; content: string }>;
}

export interface ExportSessionResult extends ApiResult {
  content?: string;
}

// Web component search types
export interface WebSearchResult {
  title: string;
  url: string;
  snippet?: string;
}

export interface ComponentSearchResult extends ApiResult {
  response?: string;
  search_results?: WebSearchResult[];
  query?: string;
  /** Which search backend was used: 'google' (Gemini grounding) or 'duckduckgo' */
  grounding?: 'google' | 'duckduckgo';
}

export interface PyWebViewAPI {
  echo_message: (message: string) => Promise<string>;
  detect_kicad_instances: () => Promise<KiCadInstance[]>;
  // API key management (provider-aware)
  check_api_key: (provider?: string) => Promise<boolean>;
  get_api_key: (provider?: string) => Promise<string | null>;
  set_api_key: (apiKey: string, provider?: string) => Promise<ApiResult>;
  // Provider management
  get_providers: () => Promise<ProvidersResult>;
  set_provider: (provider: string, model: string) => Promise<ApiResult>;
  // Dual-model (primary / secondary) configuration
  get_model_config: () => Promise<ModelConfigResult>;
  set_secondary_model: (provider: string, model: string) => Promise<ApiResult>;
  // Local model management
  get_local_models: () => Promise<LocalModelsResult>;
  set_local_base_url: (baseUrl: string) => Promise<ApiResult>;
  // Gemma 4 local model management
  get_gemma_models: () => Promise<GemmaModelsResult>;
  download_gemma_model: (modelId: string) => Promise<ApiResult>;
  cancel_gemma_download: () => Promise<ApiResult>;
  get_gemma_download_progress: () => Promise<GemmaDownloadProgress>;
  delete_gemma_model: (modelId: string) => Promise<ApiResult>;
  start_gemma_server: (modelId: string, nCtx?: number, nGpuLayers?: number) => Promise<GemmaServerResult>;
  stop_gemma_server: () => Promise<ApiResult>;
  get_gemma_server_status: () => Promise<GemmaServerStatus>;
  // Chat API
  send_message: (message: string, model?: string) => Promise<SendMessageResult>;
  // Streaming API
  start_stream_message: (message: string, model?: string, raw_mode?: boolean) => Promise<ApiResult>;
  poll_stream: () => Promise<StreamPollResult>;
  steer_stream: (message: string, model?: string) => Promise<ApiResult>;
  // LLM interaction log
  get_llm_log: (sinceId?: string | null) => Promise<LLMLogResult>;
  clear_llm_log: () => Promise<ApiResult>;
  // Session reset
  new_chat_session: () => Promise<ApiResult>;
  // Project API
  get_recent_projects: () => Promise<RecentProject[]>;
  add_recent_project: (projectPath: string) => Promise<ApiResult>;
  remove_recent_project: (projectPath: string) => Promise<ApiResult>;
  validate_project_path: (path: string) => Promise<ProjectValidationResult>;
  browse_for_project: () => Promise<BrowseProjectResult>;
  get_open_project_paths: () => Promise<string[]>;
  get_projects_list: () => Promise<ProjectsListResult>;
  set_project_path: (path: string) => Promise<ApiResult>;
  // Session management
  get_sessions: (projectPath?: string) => Promise<SessionsResult>;
  resume_session: (sessionId: string, projectPath?: string) => Promise<ResumeSessionResult>;
  export_session: (sessionId: string, projectPath?: string) => Promise<ExportSessionResult>;
  // Requirements Wizard API
  get_wizard_questions: () => Promise<WizardQuestionsResult>;
  check_requirements_file: (projectDir: string) => Promise<RequirementsFileResult>;
  get_requirements_content: (projectDir: string) => Promise<RequirementsContentResult>;
  save_requirements: (projectDir: string, requirementsContent: string, todoContent?: string) => Promise<SaveRequirementsResult>;
  refine_wizard_questions: (initialAnswers: Record<string, string>, model?: string) => Promise<WizardQuestionsResult>;
  synthesize_requirements: (questions: WizardQuestion[], answers: Record<string, string>, projectName?: string, model?: string) => Promise<SynthesizeResult>;
  // Context Lifecycle API
  start_context_lifecycle: () => Promise<ContextLifecycleState>;
  get_context_lifecycle_state: () => Promise<ContextLifecycleState>;
  submit_context_answer: (answer: string) => Promise<ContextLifecycleState>;
  // Schematic API
  inject_schematic_test_note: (projectPath: string) => Promise<InjectTestNoteResult>;
  is_schematic_api_available: () => Promise<boolean>;
  // Web component search
  web_search_components: (query: string, model?: string) => Promise<ComponentSearchResult>;
}

declare global {
  interface Window {
    pywebview?: {
      api: PyWebViewAPI;
    };
  }
}

export {};
