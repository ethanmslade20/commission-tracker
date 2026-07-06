import os
import yaml
from pathlib import Path

_ROOT = Path(__file__).parent.parent


def load_settings() -> dict:
    path = _ROOT / "config" / "settings.yaml"
    with open(path) as f:
        settings = yaml.safe_load(f)
    # Resolve relative paths from project root
    for key in ("snapshot_dir", "input_dir", "carrier_config_path"):
        if key in settings and settings[key]:
            settings[key] = str(_ROOT / settings[key])
    return settings


def load_carrier_configs(path: str) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f)
    # Support both old 'carriers' key and new 'sources' key
    return data.get("sources", data.get("carriers", {}))


def load_full_carrier_config(path: str) -> dict:
    """Return the full YAML (sources + top-level aliases/matrix)."""
    with open(path) as f:
        return yaml.safe_load(f)


def project_root() -> Path:
    return _ROOT


_AGENT_DEFAULTS = {"first_name": "Ethan", "last_name": "Slade",
                   "name": "Ethan Slade", "npn": "21457938"}


def get_agent() -> dict:
    """The agent identity this tracker is configured for (config/settings.yaml
    `agent:` block). Falls back to defaults so a missing block never crashes."""
    try:
        agent = load_settings().get("agent") or {}
    except Exception:
        agent = {}
    return {**_AGENT_DEFAULTS, **agent}
