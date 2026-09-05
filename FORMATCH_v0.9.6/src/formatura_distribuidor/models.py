from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass(frozen=True)
class DistributionConfig:
    recognition_dir: Path
    events_dir: Path
    output_dir: Path
    confidence_threshold: float = 0.45
    low_album_limit: int = 12


@dataclass
class DistributionStats:
    total: int = 0
    processed: int = 0
    recognized_photos: int = 0
    unidentified_photos: int = 0
    copied_files: int = 0
    errors: list[str] = field(default_factory=list)
    album_counts: dict[str, int] = field(default_factory=dict)


ProgressCallback = Callable[[DistributionStats, Path, set[str]], None]


def image_files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )

