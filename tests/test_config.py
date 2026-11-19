"""config: local.yaml поверх default.yaml."""

from rootbot.config import _merge


def test_local_overrides_only_its_keys():
    default = {"robot": {"ip": "x", "mock": False}, "camera": {"fps": 30}}
    merged = _merge(default, {"robot": {"ip": "192.168.1.203"}})
    assert merged == {"robot": {"ip": "192.168.1.203", "mock": False}, "camera": {"fps": 30}}


def test_empty_section_keeps_default():
    """Секція, у якій усе закоментовано, читається як None і не має стирати default."""
    merged = _merge({"camera": {"fps": 30}}, {"camera": None})
    assert merged == {"camera": {"fps": 30}}
