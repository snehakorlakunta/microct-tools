"""The server hosts the current interface (site/) at "/" by default.

The older built-in interface (web/) has no BV/TV or digit-length panel, so
serving it by default sent people to a page without the official
measurements. These pin which folder wins.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from microct_lab import config  # noqa: E402


def test_bundled_site_is_present():
    assert (config.SITE_DIR / "index.html").is_file()
    assert (config.SITE_DIR / "BUILD.txt").is_file()


def test_site_is_served_by_default(monkeypatch):
    monkeypatch.setattr(config.settings, "web_dir", None)
    assert config.settings.frontend_dir == config.SITE_DIR


def test_web_dir_override_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, "web_dir", tmp_path)
    assert config.settings.frontend_dir == tmp_path


def test_falls_back_to_legacy_when_site_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, "web_dir", None)
    monkeypatch.setattr(config, "SITE_DIR", tmp_path / "missing")
    assert config.settings.frontend_dir == config.WEB_DIR
