from __future__ import annotations

import filecmp
import shutil
import threading
from pathlib import Path
from typing import Protocol

from .models import DistributionConfig, DistributionStats, ProgressCallback, image_files


class RecognitionEngine(Protocol):
    def build_index(self, recognition_dir: Path) -> list[str]: ...
    def recognize(self, photo: Path) -> set[str]: ...


class DistributionCancelled(RuntimeError):
    pass


def _available_name(folder: Path, source: Path) -> tuple[Path, bool]:
    target = folder / source.name
    counter = 2
    while target.exists():
        if target.stat().st_size == source.stat().st_size and filecmp.cmp(
            source, target, shallow=False
        ):
            return target, False
        target = folder / f"{source.stem}__{counter}{source.suffix}"
        counter += 1
    return target, True


def _copy(source: Path, folder: Path) -> tuple[Path, bool]:
    folder.mkdir(parents=True, exist_ok=True)
    target, needs_copy = _available_name(folder, source)
    if needs_copy:
        shutil.copy2(source, target)
    return target, needs_copy


def validate_config(config: DistributionConfig) -> None:
    for label, folder in (
        ("Reconhecimento", config.recognition_dir),
        ("Eventos", config.events_dir),
    ):
        if not folder.is_dir():
            raise ValueError(f"A pasta {label} não existe: {folder}")
    if config.recognition_dir.resolve() == config.events_dir.resolve():
        raise ValueError("Reconhecimento e Eventos precisam ser pastas diferentes.")
    output = config.output_dir.resolve()
    events = config.events_dir.resolve()
    if output == events or output in events.parents or events in output.parents:
        raise ValueError("A saída e Eventos não podem ficar uma dentro da outra.")


def distribute(
    config: DistributionConfig,
    engine: RecognitionEngine,
    progress: ProgressCallback | None = None,
    stop_event: threading.Event | None = None,
) -> tuple[DistributionStats, list[str]]:
    validate_config(config)
    warnings = engine.build_index(config.recognition_dir)
    reference_ids = [path.stem for path in image_files(config.recognition_dir)]
    if len(set(reference_ids)) != len(reference_ids):
        raise ValueError("Existem cadastros com o mesmo nome de arquivo.")
    photos = image_files(config.events_dir)
    stats = DistributionStats(total=len(photos))
    distributed_root = config.output_dir / "DISTRIBUÍDOS"
    unidentified_root = config.output_dir / "SEM IDENTIFICAÇÃO"
    for identifier in reference_ids:
        (distributed_root / identifier).mkdir(parents=True, exist_ok=True)
        stats.album_counts[identifier] = 0

    for photo in photos:
        if stop_event and stop_event.is_set():
            raise DistributionCancelled("Processamento interrompido pelo usuário.")
        matches: set[str] = set()
        try:
            matches = engine.recognize(photo)
            if matches:
                for identifier in sorted(matches):
                    _, copied = _copy(photo, distributed_root / identifier)
                    stats.album_counts[identifier] = stats.album_counts.get(identifier, 0) + 1
                    stats.copied_files += int(copied)
                stats.recognized_photos += 1
            else:
                _, copied = _copy(photo, unidentified_root)
                stats.unidentified_photos += 1
                stats.copied_files += int(copied)
        except Exception as exc:  # continue safely and report the file
            stats.errors.append(f"{photo.name}: {exc}")
        finally:
            stats.processed += 1
            if progress:
                progress(stats, photo, matches)

    return stats, warnings
