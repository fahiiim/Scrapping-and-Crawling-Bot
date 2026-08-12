import os

# Define the structure as a list of (path, content) or just path for directories
STRUCTURE = {
    # Directories (end with '/')
    "app/": None,
    "app/data/": None,
    "tests/": None,

    # Files (content can be a string or None for empty)
    "app/main.py": '# app/main.py\n"""Main entry point."""\n',
    "app/crawler.py": '# app/crawler.py\n"""Web crawler module."""\n',
    "app/scraper.py": '# app/scraper.py\n"""Data scraper module."""\n',
    "app/parser.py": '# app/parser.py\n"""HTML/JSON parser module."""\n',
    "app/exporter.py": '# app/exporter.py\n"""Data export module."""\n',
    "app/config.py": '# app/config.py\n"""Configuration settings."""\n',
    "app/data/vevor_products.csv": "",  # empty CSV file
    "tests/test_parser.py": '# tests/test_parser.py\n"""Unit tests for parser."""\n',
    "requirements.txt": "# Project dependencies\n",
    "README.md": "# Project Title\n\nDescription of your project.\n",
}

# Content for .gitignore
GITIGNORE_CONTENT = """# Byte-compiled / optimized / DLL files
__pycache__/
*.py[cod]
*$py.class

# Virtual environments
.env
venv/
env/
ENV/
env.bak/
venv.bak/

# IDE / editor files
.vscode/
.idea/
*.swp
*.swo
*.swn

# OS metadata
.DS_Store
Thumbs.db

# Logs and databases
*.log
*.sqlite
*.sqlite3

# Project specific
*.csv
!app/data/vevor_products.csv   # keep the sample CSV if needed
"""


def create_structure():
    """Create all directories and files defined in STRUCTURE."""
    for path, content in STRUCTURE.items():
        # If path ends with '/', it's a directory
        if path.endswith('/'):
            os.makedirs(path, exist_ok=True)
            print(f"Created directory: {path}")
        else:
            # Ensure parent directory exists
            parent = os.path.dirname(path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            # Write file (only if it doesn't exist to avoid overwriting)
            if not os.path.exists(path):
                with open(path, 'w', encoding='utf-8') as f:
                    if content:
                        f.write(content)
                print(f"Created file: {path}")
            else:
                print(f"Skipped existing file: {path}")

    # Create .gitignore separately
    gitignore_path = ".gitignore"
    if not os.path.exists(gitignore_path):
        with open(gitignore_path, 'w', encoding='utf-8') as f:
            f.write(GITIGNORE_CONTENT)
        print(f"Created file: {gitignore_path}")
    else:
        print(f"Skipped existing file: {gitignore_path}")


if __name__ == "__main__":
    create_structure()
    print("\nProject scaffold created successfully.")