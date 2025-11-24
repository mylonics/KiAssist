# KiAssist

KiCAD AI Assistance - A cross-platform desktop application for AI-powered KiCAD design assistance.

## Features

- **Cross-Platform UI**: Built with Python + pywebview + Vue for Windows, macOS, and Linux
- **AI-Powered Chat**: Interactive chat interface powered by Google Gemini LLM
  - Support for multiple Gemini models (2.5 Flash, 2.5 Pro, 3 Flash, 3 Pro)
  - Automatic API key detection from environment variables
  - Secure API key storage in OS credential store
  - Real-time responses with loading indicators
- **KiCAD Integration**: Direct connection to running KiCAD instances
- **Modern Design**: Professional UI with light/dark mode support
- **Python Backend**: Pure Python backend with pywebview for native desktop experience
- **Continuous Integration**: Automated builds for all major platforms

## Getting Started

### Installation

**Windows:**
1. Download `KiAssist-Setup-0.1.0.exe` from the [latest release](https://github.com/mylonics/NewKiAssist/releases)
2. Run the installer (no admin rights required)
3. Launch KiAssist from the Start Menu

**macOS:**
1. Download `KiAssist-0.1.0.dmg` from the [latest release](https://github.com/mylonics/NewKiAssist/releases)
2. Open the DMG and drag KiAssist.app to Applications
3. Launch KiAssist from Applications

**Linux:**
1. Download `KiAssist-0.1.0-x86_64.AppImage` from the [latest release](https://github.com/mylonics/NewKiAssist/releases)
2. Make it executable: `chmod +x KiAssist-0.1.0-x86_64.AppImage`
3. Run: `./KiAssist-0.1.0-x86_64.AppImage`

### Prerequisites for Development

- Node.js 20+
- Python 3.8+
- Google Gemini API key (get one at [Google AI Studio](https://makersuite.google.com/app/apikey))
- Platform-specific dependencies:
  - **Linux**: `python3-dev`, `libgirepository1.0-dev`, `gir1.2-webkit2-4.1`, `libcairo2-dev`, `pkg-config`, `python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-3.0`
  - **macOS**: No additional dependencies (uses native WebKit)
  - **Windows**: No additional dependencies (uses WebView2, installed on Windows 10+)

### Configuring Gemini API

KiAssist can be configured with your Gemini API key in two ways:

**Option 1: Environment Variable (Recommended for Development)**
```bash
export GEMINI_API_KEY="your-api-key-here"
./start.sh
```

**Option 2: Interactive Prompt**
- Launch the application without setting the environment variable
- A modal will prompt you to enter your API key
- The key will be securely stored in your OS credential store for future sessions
- You can update the key anytime using the settings button (⚙️) in the chat header

## Development

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
# Build the frontend first
npm run build

# Run the application
./start.sh  # On Linux/macOS
start.bat   # On Windows
```

Or manually:
```bash
python -m kiassist_utils.main
```

### Building for Production

The build process creates installers for all platforms:

```bash
# Use the build script
./build.sh  # On Linux/macOS
build.bat   # On Windows
```

This will:
1. Install all dependencies
2. Build the Vue.js frontend
3. Package the application with PyInstaller into a directory bundle

**Output:**
- **Windows**: `dist/KiAssist/` directory (can be packaged with Inno Setup)
- **macOS**: `dist/KiAssist.app` bundle (can be packaged into DMG)
- **Linux**: `dist/KiAssist/` directory (can be packaged as AppImage)

**Creating Installers:**

The CI/CD pipeline automatically builds installers for all platforms. To build installers locally:

**Windows (requires Inno Setup):**
```bash
# After building with PyInstaller
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
# Output: installer-output/KiAssist-Setup-0.1.0.exe
```

**macOS (requires create-dmg):**
```bash
# After building with PyInstaller
brew install create-dmg
create-dmg \
  --volname "KiAssist" \
  --window-pos 200 120 \
  --window-size 600 400 \
  --icon-size 100 \
  --icon "KiAssist.app" 175 190 \
  --hide-extension "KiAssist.app" \
  --app-drop-link 425 190 \
  "KiAssist-0.1.0.dmg" \
  "dist/KiAssist.app"
# Output: KiAssist-0.1.0.dmg
```

**Linux (requires appimagetool):**
```bash
# After building with PyInstaller
wget https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x appimagetool-x86_64.AppImage

mkdir -p KiAssist.AppDir/usr/bin
cp -r dist/KiAssist KiAssist.AppDir/usr/bin/kiassist
cp kiassist.desktop KiAssist.AppDir/
cp kiassist.png KiAssist.AppDir/
ln -s usr/bin/kiassist/KiAssist KiAssist.AppDir/AppRun

ARCH=x86_64 ./appimagetool-x86_64.AppImage KiAssist.AppDir KiAssist-0.1.0-x86_64.AppImage
# Output: KiAssist-0.1.0-x86_64.AppImage
```

Build artifacts will be available in the `dist/` directory and installer output directories.

## Project Structure

```
KiAssist/
├── src/                    # Vue frontend source
│   ├── components/        # Vue components
│   │   ├── ChatBox.vue   # AI chat interface with Gemini integration
│   │   └── KiCadInstanceSelector.vue  # KiCAD instance selection
│   ├── App.vue           # Main application component
│   └── main.ts           # Application entry point
├── python-lib/            # Python backend package
│   └── kiassist_utils/
│       ├── main.py       # Main application with pywebview
│       ├── api_key.py    # API key management and persistence
│       ├── gemini.py     # Gemini API integration
│       └── kicad_ipc.py  # KiCAD IPC communication
├── dist/                  # Built frontend and PyInstaller output (generated)
├── .github/workflows/     # CI/CD pipelines
├── installer.iss          # Inno Setup script for Windows installer
├── kiassist.desktop       # Linux desktop entry file
├── kiassist.svg           # Application icon (SVG)
├── kiassist.png           # Application icon (PNG, 256x256)
├── kiassist.spec          # PyInstaller specification file
├── start.sh / start.bat   # Development startup scripts
└── build.sh / build.bat   # Build scripts
```

## Using Gemini AI Chat

1. **Start the Application**: Launch KiAssist with `./start.sh` or run the built application
2. **Configure API Key**: If not set via environment variable, enter your Gemini API key when prompted
3. **Select Model**: Choose your preferred Gemini model from the dropdown in the chat header:
   - **Gemini 2.5 Flash**: Fast and efficient for quick responses
   - **Gemini 2.5 Pro**: Advanced capabilities for complex queries
   - **Gemini 3 Flash**: Future-ready fast model
   - **Gemini 3 Pro**: Future-ready advanced model
4. **Start Chatting**: Type your questions about KiCAD or PCB design and get AI-powered responses
5. **Manage Settings**: Click the settings button (⚙️) to update your API key at any time

## Python Package

The `kiassist-utils` package provides:
- Gemini API integration
- Secure API key storage using OS credential store (keyring)
- KiCAD IPC instance detection
- Desktop UI wrapper using pywebview

### Usage

```python
from kiassist_utils import KiAssistAPI, main

# Run the application
main()

# Or use the API programmatically
api = KiAssistAPI()
api.set_api_key("your-api-key")
response = api.send_message("Hello, KiCAD!", "2.5-flash")
```

## CI/CD

GitHub Actions workflows automatically build installers for all platforms:

**Windows:**
- Directory bundle: `dist/KiAssist/`
- Installer: `KiAssist-Setup-0.1.0.exe` (Inno Setup)
- Installation path: `%LOCALAPPDATA%\Programs\KiAssist\`

**macOS:**
- App bundle: `dist/KiAssist.app`
- DMG installer: `KiAssist-0.1.0.dmg` (create-dmg)
- Installation path: `/Applications/KiAssist.app`

**Linux:**
- Directory bundle: `dist/KiAssist/`
- AppImage: `KiAssist-0.1.0-x86_64.AppImage`
- Portable - runs from any location

All build artifacts are available in GitHub Actions artifacts after each workflow run.

### Installer Benefits

- **No Decompression Overhead**: One-folder mode eliminates 2-5 second startup delay
- **Standard Installation**: Native installer experience on each platform
- **User Data Persistence**: API keys stored in OS credential stores persist across updates automatically
  - Windows: Windows Credential Manager
  - macOS: Keychain
  - Linux: Secret Service
- **No Admin Required**: All installers support per-user installation

## Technology Stack

- **Frontend**: Vue.js 3 + TypeScript + Vite
- **Backend**: Python 3.8+
- **Desktop Framework**: pywebview (native webview wrapper)
- **API Integration**: Google Gemini API via requests
- **Secure Storage**: OS keyring (Windows Credential Manager, macOS Keychain, Linux Secret Service)
- **Packaging**: PyInstaller for cross-platform executables

## Recommended IDE Setup

- [VS Code](https://code.visualstudio.com/) + [Vue - Official](https://marketplace.visualstudio.com/items?itemName=Vue.volar) + [Python](https://marketplace.visualstudio.com/items?itemName=ms-python.python)

## License

TBD
