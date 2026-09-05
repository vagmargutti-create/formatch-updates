from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from .distributor import DistributionCancelled
from .models import DistributionConfig, image_files
from .project_store import ProjectStore


AnalysisProgress = Callable[[int, int, int, Path, set[str]], None]


def analyze_project(
    config: DistributionConfig,
    engine,
    store: ProjectStore,
    progress: AnalysisProgress | None = None,
    stop_event: threading.Event | None = None,
) -> list[str]:
    warnings = engine.build_index(config.recognition_dir)
    references = image_files(config.recognition_dir)
    photos = image_files(config.events_dir)
    store.set_setting("recognition_dir", str(config.recognition_dir))
    store.set_setting("events_dir", str(config.events_dir))
    for reference in references:
        store.add_student(reference.stem, reference)
    for photo in photos:
        store.add_photo(photo)
    store.connection.commit()

    done = 0
    total = len(photos)
    for photo in photos:
        if stop_event and stop_event.is_set():
            raise DistributionCancelled("Análise interrompida com segurança.")
        photo_id = store.add_photo(photo)
        matches: set[str] = set()
        if store.photo_processed(photo):
            done += 1
            if progress:
                progress(done, total, store.counts()[2], photo, matches)
            continue
        try:
            observations = engine.analyze(photo, candidate_count=5)
            store.save_analysis(photo_id, observations, config.confidence_threshold)
            matches = {
                observation.candidates[0][0]
                for observation in observations
                if observation.candidates
                and observation.candidates[0][1] >= config.confidence_threshold
            }
        except Exception as exc:
            store.save_error(photo_id, str(exc))
        done += 1
        if progress:
            progress(done, total, store.counts()[2], photo, matches)
    return warnings
