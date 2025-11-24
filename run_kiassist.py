#!/usr/bin/env python3
"""Entry point script for KiAssist application.

This script serves as the main entry point when running KiAssist
as a standalone executable. It properly initializes the package
and starts the application.
"""

import sys
from pathlib import Path

# When running as a PyInstaller executable, ensure the package is importable
if getattr(sys, 'frozen', False):
    # Running as compiled executable
    # Add the path where PyInstaller extracts files
    bundle_dir = Path(sys._MEIPASS)
else:
    # Running in normal Python environment
    # Add python-lib to the path if needed
    bundle_dir = Path(__file__).parent
    python_lib = bundle_dir / "python-lib"
    if python_lib.exists() and str(python_lib) not in sys.path:
        sys.path.insert(0, str(python_lib))

# Now import and run the main function
from kiassist_utils.main import main

if __name__ == "__main__":
    main()
