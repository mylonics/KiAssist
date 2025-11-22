# KiAssist Python Utilities

A Python utility library for the KiAssist application. This package provides helper functions for processing messages and working with KiCAD projects.

## Installation

```bash
pip install -e .
```

## Usage

```python
from kiassist_utils import process_message, validate_kicad_project

# Process a message
result = process_message("Hello, KiCAD!")
print(result)  # Output: Processed: Hello, KiCAD!

# Validate a KiCAD project
is_valid = validate_kicad_project("/path/to/project.kicad_pro")
print(is_valid)  # Output: True
```

## Future Features

- Integration with KiCAD IPC API
- Schematic and PCB analysis utilities
- AI-assisted design suggestions
