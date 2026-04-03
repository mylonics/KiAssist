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
  done?: boolean;
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
}

export interface ProvidersResult extends ApiResult {
  providers?: ProviderInfo[];
  current_provider?: string;
  current_model?: string;
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
  // Chat API
  send_message: (message: string, model?: string) => Promise<SendMessageResult>;
  // Streaming API
  start_stream_message: (message: string, model?: string) => Promise<ApiResult>;
  poll_stream: () => Promise<StreamPollResult>;
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
  // Schematic API
  inject_schematic_test_note: (projectPath: string) => Promise<InjectTestNoteResult>;
  is_schematic_api_available: () => Promise<boolean>;
}

declare global {
  interface Window {
    pywebview?: {
      api: PyWebViewAPI;
    };
  }
}

export {};
