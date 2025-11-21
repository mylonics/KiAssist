# NewKiAssist

KiCAD AI Assistance - A cross-platform desktop application for AI-powered KiCAD design assistance.

## Features

- **Cross-Platform UI**: Built with Tauri + Vue for Windows, macOS, and Linux
- **Chat Interface**: Interactive chat box with echo functionality (LLM integration coming soon)
- **Modern Design**: Professional UI with light/dark mode support
- **Python Integration**: Includes Python utilities package for KiCAD integration
- **Continuous Integration**: Automated builds for all major platforms

## Development

### Prerequisites

- Node.js 20+
- Rust (latest stable)
- Python 3.8+
- Platform-specific dependencies:
  - **Linux**: `libwebkit2gtk-4.1-dev`, `build-essential`, `curl`, `wget`, `file`, `libxdo-dev`, `libssl-dev`, `libayatana-appindicator3-dev`, `librsvg2-dev`
  - **macOS**: Xcode Command Line Tools
  - **Windows**: Microsoft Visual Studio C++ Build Tools

### Setup

```bash
# Install Node dependencies
npm install

# Install Python package
cd python-lib
pip install -e .
cd ..
```

### Running in Development

```bash
npm run tauri dev
```

### Building for Production

```bash
npm run tauri build
```

Build artifacts will be available in `src-tauri/target/release/bundle/`.

## Project Structure

```
NewKiAssist/
├── src/                    # Vue frontend source
│   ├── components/        # Vue components
│   │   └── ChatBox.vue   # Chat interface component
│   ├── App.vue           # Main application component
│   └── main.ts           # Application entry point
├── src-tauri/             # Tauri backend
│   └── src/
│       └── lib.rs        # Rust backend with commands
├── python-lib/            # Python utilities package
│   └── newkiassist_utils/
└── .github/workflows/     # CI/CD pipelines
```

## Python Package

The `newkiassist-utils` package provides utility functions for message processing and KiCAD project validation. It will be extended to integrate with KiCAD IPC APIs.

### Usage

```python
from newkiassist_utils import process_message, validate_kicad_project

# Process messages
result = process_message("Hello, KiCAD!")

# Validate KiCAD projects
is_valid = validate_kicad_project("/path/to/project.kicad_pro")
```

## CI/CD

GitHub Actions workflows automatically build the application for:
- Ubuntu Linux (AppImage, DEB, RPM)
- macOS (DMG, App Bundle)
- Windows (MSI, EXE)

## Recommended IDE Setup

- [VS Code](https://code.visualstudio.com/) + [Vue - Official](https://marketplace.visualstudio.com/items?itemName=Vue.volar) + [Tauri](https://marketplace.visualstudio.com/items?itemName=tauri-apps.tauri-vscode) + [rust-analyzer](https://marketplace.visualstudio.com/items?itemName=rust-lang.rust-analyzer)

## License

TBD
