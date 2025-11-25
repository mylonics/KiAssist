// Global type definitions for pywebview API

export interface KiCadInstance {
  socket_path: string;
  project_name: string;
  display_name: string;
  version: string;
  project_path: string;
  pcb_path: string;
  schematic_path: string;
}

export interface RecentProject {
  path: string;
  name: string;
  last_opened: string;
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

export interface PyWebViewAPI {
  echo_message: (message: string) => Promise<string>;
  detect_kicad_instances: () => Promise<KiCadInstance[]>;
  check_api_key: () => Promise<boolean>;
  get_api_key: () => Promise<string | null>;
  set_api_key: (apiKey: string) => Promise<ApiResult>;
  send_message: (message: string, model: string) => Promise<SendMessageResult>;
  get_recent_projects: () => Promise<RecentProject[]>;
  add_recent_project: (projectPath: string) => Promise<ApiResult>;
  remove_recent_project: (projectPath: string) => Promise<ApiResult>;
  validate_project_path: (path: string) => Promise<ProjectValidationResult>;
  browse_for_project: () => Promise<BrowseProjectResult>;
  get_open_project_paths: () => Promise<string[]>;
  get_projects_list: () => Promise<ProjectsListResult>;
  // Requirements Wizard API
  get_wizard_questions: () => Promise<WizardQuestionsResult>;
  check_requirements_file: (projectDir: string) => Promise<RequirementsFileResult>;
  get_requirements_content: (projectDir: string) => Promise<RequirementsContentResult>;
  save_requirements: (projectDir: string, requirementsContent: string, todoContent?: string) => Promise<SaveRequirementsResult>;
  refine_wizard_questions: (initialAnswers: Record<string, string>, model?: string) => Promise<WizardQuestionsResult>;
  synthesize_requirements: (questions: WizardQuestion[], answers: Record<string, string>, projectName?: string, model?: string) => Promise<SynthesizeResult>;
}

declare global {
  interface Window {
    pywebview?: {
      api: PyWebViewAPI;
    };
  }
}

export {};
