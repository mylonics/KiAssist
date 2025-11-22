use serde::{Deserialize, Serialize};
use std::env;
use std::sync::Mutex;
use tauri::{AppHandle, Wry};
use tauri_plugin_store::StoreExt;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApiKeyStore {
    api_key: Option<String>,
    #[serde(skip)]
    app_handle: Option<AppHandle<Wry>>,
}

const STORE_FILE: &str = "kiassist_config.json";
const API_KEY_FIELD: &str = "gemini_api_key";

impl ApiKeyStore {
    pub fn new() -> Self {
        // Try to load from environment variable first
        let api_key = env::var("GEMINI_API_KEY").ok();
        Self { 
            api_key,
            app_handle: None,
        }
    }
    
    pub fn set_app_handle(&mut self, handle: AppHandle<Wry>) {
        self.app_handle = Some(handle.clone());
        
        // Try to load from persistent store if environment variable not set
        if self.api_key.is_none() {
            if let Ok(store) = handle.store(STORE_FILE) {
                if let Some(key) = store.get(API_KEY_FIELD) {
                    if let Some(key_str) = key.as_str() {
                        self.api_key = Some(key_str.to_string());
                    }
                }
            }
        }
    }
    
    pub fn get_api_key(&self) -> Option<String> {
        self.api_key.clone()
    }
    
    pub fn set_api_key(&mut self, key: String) -> Result<(), String> {
        self.api_key = Some(key.clone());
        
        // Persist to store
        if let Some(handle) = &self.app_handle {
            if let Ok(store) = handle.store(STORE_FILE) {
                store.set(API_KEY_FIELD, serde_json::Value::String(key));
                store.save().map_err(|e| format!("Failed to save API key: {}", e))?;
            }
        }
        
        Ok(())
    }
    
    pub fn has_api_key(&self) -> bool {
        self.api_key.is_some()
    }
}

pub struct ApiKeyState(pub Mutex<ApiKeyStore>);
