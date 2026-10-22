"""JSON-file-backed artifact store.

Each program gets a directory under `data_dir/programs/<program_id>/`.
Artifacts are stored as JSON files inside that directory.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class _Encoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


class ArtifactStore:
    """Simple file-backed persistence for TPM artifacts."""

    def __init__(self, data_dir: Path) -> None:
        self._root = data_dir / "programs"
        self._root.mkdir(parents=True, exist_ok=True)

    def _program_dir(self, program_id: str) -> Path:
        d = self._root / program_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── generic read / write ───────────────────────────────────

    def save(self, program_id: str, artifact_name: str, model: BaseModel) -> Path:
        path = self._program_dir(program_id) / f"{artifact_name}.json"
        path.write_text(json.dumps(model.model_dump(), cls=_Encoder, indent=2))
        return path

    def load(self, program_id: str, artifact_name: str, model_cls: type[T]) -> T | None:
        path = self._program_dir(program_id) / f"{artifact_name}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return model_cls.model_validate(data)

    def exists(self, program_id: str, artifact_name: str) -> bool:
        return (self._program_dir(program_id) / f"{artifact_name}.json").exists()

    def list_programs(self) -> list[str]:
        return [d.name for d in self._root.iterdir() if d.is_dir()]

    def list_artifacts(self, program_id: str) -> list[str]:
        d = self._program_dir(program_id)
        return [p.stem for p in d.glob("*.json")]

    def save_raw(self, program_id: str, name: str, content: str) -> Path:
        path = self._program_dir(program_id) / f"{name}.md"
        path.write_text(content)
        return path

    def load_raw(self, program_id: str, name: str) -> str | None:
        path = self._program_dir(program_id) / f"{name}.md"
        if not path.exists():
            return None
        return path.read_text()
