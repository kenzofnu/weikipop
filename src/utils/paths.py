# src/utils/paths.py
"""
Persistent per-user data directory.

User data (config.ini, imported dictionaries, mining log) must live OUTSIDE the
application/dist folder so that rebuilding, replacing, or moving the executable
never wipes the user's settings or dictionaries.

  Windows: %APPDATA%\\weikipop
  macOS:   ~/Library/Application Support/weikipop
  Linux:   $XDG_CONFIG_HOME/weikipop  (or ~/.config/weikipop)
"""
import os
import sys

APP_DIRNAME = "weikipop"


def user_data_dir() -> str:
    """Return (and create) the persistent per-user data directory."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform.startswith("darwin"):
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, APP_DIRNAME)
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        # Fall back to current directory if the data dir can't be created.
        return os.path.abspath(".")
    return path


def user_data_path(*parts: str) -> str:
    """Join one or more path components onto the user data directory."""
    return os.path.join(user_data_dir(), *parts)
