"""Configuration. Every path here points OUTSIDE the repo and is set via .env."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent          # src/microct_lab -> src -> <repo>
DEFAULT_SCRIPTS_DIR = REPO_ROOT / "scripts"
WEB_DIR = PACKAGE_DIR / "web"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MICROCT_", env_file=".env", extra="ignore",
        protected_namespaces=(),
    )

    # External, machine-specific locations (kept out of version control):
    data_root: Path = Path.home() / "microct_lab_data" / "datasets"
    results_root: Path = Path.home() / "microct_lab_data" / "results"
    models_root: Path = Path.home() / "microct_lab_data" / "models"
    state_dir: Path = Path.home() / "microct_lab_data" / "state"

    db_url: Optional[str] = None
    segment_script: Path = DEFAULT_SCRIPTS_DIR / "segment_microct.py"
    python_exe: Optional[str] = None

    host: str = "127.0.0.1"
    port: int = 8000
    default_device: str = "auto"
    poll_seconds: float = 2.0

    @property
    def thumbs_dir(self) -> Path:
        return self.state_dir / "thumbnails"

    @property
    def database_url(self) -> str:
        if self.db_url:
            return self.db_url
        return f"sqlite:///{(self.state_dir / 'registry.db').as_posix()}"

    @property
    def python(self) -> str:
        return self.python_exe or sys.executable

    def ensure_dirs(self) -> None:
        for d in (self.data_root, self.results_root, self.models_root,
                  self.state_dir, self.thumbs_dir):
            Path(d).mkdir(parents=True, exist_ok=True)


settings = Settings()
