"""Конфігурація: config/default.yaml + перевизначення з *config/local.yaml"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _merge(default: dict, custom: dict) -> dict:
    """замінює пункти default на custom"""
    out = dict(default)
    for key, value in custom.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path | None = None) -> dict:
    """config/default.yaml, а якщо є config/local.yaml, то з його значеннями поверх"""
    cfg = yaml.safe_load(open(path or ROOT / "config" / "default.yaml"))
    local = ROOT / "config" / "local.yaml"
    if path is None and local.exists():
        cfg = _merge(cfg, yaml.safe_load(open(local)) or {})
    return cfg
