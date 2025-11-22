// This program source code file is part of KiAssist.
//
// Module for detecting and managing KiCad IPC instances

use kicad::{DocumentType, KiCad, KiCadConnectionConfig, KiCadError};
use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KiCadInstance {
    pub socket_path: String,
    pub project_name: String,
    pub display_name: String,
    pub version: String,
}

/// Get the base directory where KiCad IPC sockets are stored
fn get_ipc_socket_dir() -> PathBuf {
    match env::consts::OS {
        "windows" => {
            let temp = env::var("TEMP").unwrap_or_else(|_| String::from("C:\\Temp"));
            PathBuf::from(temp).join("kicad")
        }
        _ => PathBuf::from("/tmp/kicad"),
    }
}

/// Convert a socket file path to the IPC URI format
fn socket_path_to_uri(socket_file: &Path) -> String {
    // NNG handles the platform-specific pipe conversion internally
    // We just need to provide the path with the ipc:// scheme
    format!("ipc://{}", socket_file.display())
}

/// Discover all KiCad IPC socket files
fn discover_socket_files() -> Vec<PathBuf> {
    let socket_dir = get_ipc_socket_dir();
    let mut sockets = Vec::new();

    if !socket_dir.exists() {
        return sockets;
    }

    // Look for files matching api*.sock pattern
    if let Ok(entries) = fs::read_dir(&socket_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if let Some(filename) = path.file_name() {
                let filename_str = filename.to_string_lossy();
                if filename_str.starts_with("api") && filename_str.ends_with(".sock") {
                    sockets.push(path);
                }
            }
        }
    }

    // Sort sockets by name to ensure consistent ordering
    sockets.sort();
    sockets
}

/// Try to connect to a KiCad instance and retrieve its information
fn probe_kicad_instance(socket_path: &str) -> Result<KiCadInstance, KiCadError> {
    let config = KiCadConnectionConfig {
        socket_path: socket_path.to_string(),
        client_name: String::from("kiassist-probe"),
        ..Default::default()
    };

    let kicad = KiCad::new(config)?;
    
    // Get version
    let version = kicad.get_version()?;
    let version_str = version.to_string();

    // Try to get open documents to determine project name
    let project_name = match kicad.get_open_documents(DocumentType::DOCTYPE_PCB) {
        Ok(docs) if !docs.is_empty() => {
            // Extract project name from the first document
            if let Some(doc) = docs.first() {
                // The project path is typically in the format /path/to/project.kicad_pro
                // Extract just the project name
                let project_path = &doc.project.path;
                Path::new(project_path)
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or("Unknown Project")
                    .to_string()
            } else {
                String::from("No Project Open")
            }
        }
        _ => String::from("No Project Open"),
    };

    let display_name = if project_name == "No Project Open" {
        format!("KiCad {}", version_str)
    } else {
        format!("{} (KiCad {})", project_name, version_str)
    };

    Ok(KiCadInstance {
        socket_path: socket_path.to_string(),
        project_name,
        display_name,
        version: version_str,
    })
}

/// Detect all available KiCad instances
pub fn detect_kicad_instances() -> Vec<KiCadInstance> {
    let socket_files = discover_socket_files();
    let mut instances = Vec::new();

    for socket_file in socket_files {
        let socket_uri = socket_path_to_uri(&socket_file);
        
        // Try to connect to this instance
        if let Ok(instance) = probe_kicad_instance(&socket_uri) {
            instances.push(instance);
        }
        // Silently ignore instances that don't respond
    }

    instances
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_get_ipc_socket_dir() {
        let dir = get_ipc_socket_dir();
        assert!(dir.ends_with("kicad"));
    }

    #[test]
    fn test_socket_path_to_uri() {
        #[cfg(windows)]
        {
            let path = PathBuf::from("C:\\Temp\\kicad\\api.sock");
            let uri = socket_path_to_uri(&path);
            assert!(uri.starts_with("ipc://"));
            assert!(uri.contains("api.sock"));
        }

        #[cfg(not(windows))]
        {
            let path = PathBuf::from("/tmp/kicad/api.sock");
            let uri = socket_path_to_uri(&path);
            assert_eq!(uri, "ipc:///tmp/kicad/api.sock");
        }
    }
}
