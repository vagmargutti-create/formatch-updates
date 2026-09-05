from __future__ import annotations

import filecmp
import shutil
from pathlib import Path
from typing import Callable

from .project_store import ProjectStore


ExportProgress = Callable[[int, int, str], None]


def _copy_unique(source: Path, folder: Path) -> bool:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / source.name
    counter = 2
    while target.exists():
        if target.stat().st_size == source.stat().st_size and filecmp.cmp(
            source, target, shallow=False
        ):
            return False
        target = folder / f"{source.stem}__{counter}{source.suffix}"
        counter += 1
    shutil.copy2(source, target)
    return True


def export_project(
    store: ProjectStore,
    destination: Path,
    progress: ExportProgress | None = None,
    create_backup: bool = False,
) -> tuple[int, dict[str, int]]:
    rows = store.export_rows()
    total = len(rows)
    copied = 0
    album_counts: dict[str, int] = {}
    for index, (source, assignments) in enumerate(rows, start=1):
        if assignments:
            for identifier in assignments:
                copied += int(_copy_unique(source, destination / "DISTRIBUÍDOS" / identifier))
                if create_backup:
                    copied += int(
                        _copy_unique(
                            source,
                            destination / "OUTROS" / "CÓPIA DE SEGURANÇA" / identifier,
                        )
                    )
                album_counts[identifier] = album_counts.get(identifier, 0) + 1
        else:
            copied += int(_copy_unique(source, destination / "SEM IDENTIFICAÇÃO"))
        if progress:
            progress(index, total, source.name)
    return copied, album_counts
