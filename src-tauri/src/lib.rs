// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/

mod kicad_ipc;
mod gemini;
mod api_key;

use kicad_ipc::KiCadInstance;
use api_key::{ApiKeyState, ApiKeyStore};
use tauri::{Manager, State};

#[tauri::command]
fn echo_message(message: &str) -> String {
    format!("Echo: {}", message)
}

#[tauri::command]
fn detect_kicad_instances() -> Vec<KiCadInstance> {
    kicad_ipc::detect_kicad_instances()
}

#[tauri::command]
fn check_api_key(state: State<ApiKeyState>) -> bool {
    let store = state.0.lock().unwrap();
    store.has_api_key()
}

#[tauri::command]
fn get_api_key(state: State<ApiKeyState>) -> Option<String> {
    let store = state.0.lock().unwrap();
    store.get_api_key()
}

#[tauri::command]
fn set_api_key(state: State<ApiKeyState>, api_key: String) -> Result<(), String> {
    let mut store = state.0.lock().unwrap();
    store.set_api_key(api_key)
}

#[tauri::command]
async fn send_message(
    state: State<'_, ApiKeyState>,
    message: String,
    model: String,
) -> Result<String, String> {
    let api_key = {
        let store = state.0.lock().unwrap();
        store.get_api_key()
    };
    
    match api_key {
        Some(key) => {
            gemini::send_message_to_gemini(&key, &model, &message)
                .await
                .map_err(|e| format!("Gemini API error: {}", e))
        }
        None => Err("API key not configured".to_string()),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_store::Builder::new().build())
        .manage(ApiKeyState(std::sync::Mutex::new(ApiKeyStore::new())))
        .setup(|app| {
            let handle = app.handle().clone();
            let state = app.state::<ApiKeyState>();
            let mut store = state.0.lock().unwrap();
            store.set_app_handle(handle);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            echo_message,
            detect_kicad_instances,
            check_api_key,
            get_api_key,
            set_api_key,
            send_message
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
