// Global type definitions for pywebview API

export interface KiCadInstance {
  socket_path: string;
  project_name: string;
  display_name: string;
  version: string;
}

export interface ApiResult {
  success: boolean;
  error?: string;
}

export interface SendMessageResult extends ApiResult {
  response?: string;
}

export interface PyWebViewAPI {
  echo_message: (message: string) => Promise<string>;
  detect_kicad_instances: () => Promise<KiCadInstance[]>;
  check_api_key: () => Promise<boolean>;
  get_api_key: () => Promise<string | null>;
  set_api_key: (apiKey: string) => Promise<ApiResult>;
  send_message: (message: string, model: string) => Promise<SendMessageResult>;
}

declare global {
  interface Window {
    pywebview?: {
      api: PyWebViewAPI;
    };
  }
}

export {};
