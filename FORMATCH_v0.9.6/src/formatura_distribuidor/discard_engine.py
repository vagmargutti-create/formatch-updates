from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageOps
from scipy.fft import dctn
from scipy.ndimage import laplace

from .models import IMAGE_EXTENSIONS
from .project_store import natural_key


@dataclass(frozen=True)
class PhotoMetric:
    path: Path
    quality: float
    sharpness: float
    phash: int
    histogram: tuple[float, ...]
    sha256: str
    faces: int
    face_phashes: tuple[int, ...]


@dataclass(frozen=True)
class DuplicateGroup:
    album_id: str
    keep: Path
    discard: tuple[Path, ...]
    scores: tuple[tuple[str, float], ...]
    notes: tuple[tuple[str, str], ...]
    category: str = "discard"


DiscardProgress = Callable[[int, int, str], None]


def _read(path: Path) -> Image.Image:
    try:
        with Image.open(path) as source:
            return ImageOps.exif_transpose(source).convert("RGB")
    except Exception as exc:
        raise ValueError(f"Não foi possível abrir {path.name}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _phash(gray: np.ndarray) -> int:
    small = np.asarray(
        Image.fromarray(gray.astype(np.uint8)).resize((32, 32), Image.Resampling.LANCZOS),
        dtype=np.float32,
    )
    coefficients = dctn(small, type=2, norm="ortho")[:8, :8]
    values = coefficients.flatten()[1:]
    median = float(np.median(values))
    result = 0
    for index, value in enumerate(values):
        if value > median:
            result |= 1 << index
    return result


def _histogram(image: Image.Image) -> tuple[float, ...]:
    hsv = np.asarray(image.resize((256, 256)).convert("HSV"), dtype=np.uint8)
    hist, _, _ = np.histogram2d(
        hsv[:, :, 0].flatten(),
        hsv[:, :, 1].flatten(),
        bins=(16, 8),
        range=((0, 256), (0, 256)),
    )
    hist = hist.astype(np.float32)
    hist /= max(float(np.linalg.norm(hist)), 1e-12)
    return tuple(float(value) for value in hist.flatten())


def _face_pose_hashes(image: Image.Image) -> tuple[int, tuple[int, ...]]:
    """Assinaturas faciais usadas somente para separar poses diferentes."""
    try:
        import cv2  # type: ignore

        gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
        face_detector = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        min_face = max(36, min(gray.shape[:2]) // 16)
        faces = face_detector.detectMultiScale(
            gray, scaleFactor=1.08, minNeighbors=5, minSize=(min_face, min_face)
        )
        largest_area = max((width * height for _, _, width, height in faces), default=0)
        primary_faces = [
            face for face in faces if face[2] * face[3] >= largest_area * 0.45
        ]
        face_phashes: list[int] = []
        for x, y, width, height in sorted(primary_faces, key=lambda item: item[0]):
            face = gray[y : y + height, x : x + width]
            face_phashes.append(_phash(face))
        return len(primary_faces), tuple(face_phashes)
    except Exception:
        return 0, ()


def measure(path: Path) -> PhotoMetric:
    image = _read(path)
    width, height = image.size
    scale = min(1.0, 1400 / max(height, width))
    working = image.resize(
        (max(1, int(width * scale)), max(1, int(height * scale))),
        Image.Resampling.LANCZOS,
    )
    gray = np.asarray(working.convert("L"), dtype=np.float32)
    sharpness = float(laplace(gray).var())
    mean = float(gray.mean())
    contrast = float(gray.std())
    clipped = float(((gray < 8) | (gray > 247)).mean())
    sharp_score = min(65.0, max(0.0, math.log1p(sharpness) * 10.5))
    exposure_score = max(0.0, 15.0 - abs(mean - 127.5) / 12.0 - clipped * 20.0)
    contrast_score = min(12.0, contrast / 5.0)
    resolution_score = min(8.0, math.log1p(width * height) / 2.2)
    faces, face_phashes = _face_pose_hashes(working)
    quality = sharp_score + exposure_score + contrast_score + resolution_score
    return PhotoMetric(
        path=path,
        quality=round(quality, 3),
        sharpness=round(sharpness, 3),
        phash=_phash(gray),
        histogram=_histogram(working),
        sha256=_sha256(path),
        faces=faces,
        face_phashes=face_phashes,
    )


def _scene_related(left: PhotoMetric, right: PhotoMetric) -> bool:
    """Detecta a mesma cena mesmo com corte ou orientação diferentes.

    É usada apenas para SUGESTÃO: correspondências do cenário nunca provocam
    descarte automático.
    """
    try:
        import cv2  # type: ignore

        first = cv2.imread(str(left.path), cv2.IMREAD_GRAYSCALE)
        second = cv2.imread(str(right.path), cv2.IMREAD_GRAYSCALE)
        if first is None or second is None:
            return False
        limit = 1200
        for image_name, image in (("first", first), ("second", second)):
            scale = min(1.0, limit / max(image.shape[:2]))
            if scale < 1.0:
                resized = cv2.resize(image, None, fx=scale, fy=scale)
                if image_name == "first":
                    first = resized
                else:
                    second = resized
        detector = cv2.ORB_create(nfeatures=1600, fastThreshold=12)
        key_a, desc_a = detector.detectAndCompute(first, None)
        key_b, desc_b = detector.detectAndCompute(second, None)
        if desc_a is None or desc_b is None or len(key_a) < 30 or len(key_b) < 30:
            return False
        pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc_a, desc_b, k=2)
        good = [a for a, b in pairs if a.distance < 0.72 * b.distance]
        if len(good) < 18:
            return False
        source = np.float32([key_a[item.queryIdx].pt for item in good]).reshape(-1, 1, 2)
        target = np.float32([key_b[item.trainIdx].pt for item in good]).reshape(-1, 1, 2)
        _, mask = cv2.findHomography(source, target, cv2.RANSAC, 5.0)
        return mask is not None and int(mask.sum()) >= 14 and float(mask.mean()) >= 0.30
    except Exception:
        return False


def _similarity(left: PhotoMetric, right: PhotoMetric) -> str | None:
    if left.sha256 == right.sha256:
        return "discard"
    hamming = (left.phash ^ right.phash).bit_count()
    left_hist = np.asarray(left.histogram, dtype=np.float32)
    right_hist = np.asarray(right.histogram, dtype=np.float32)
    if float(left_hist.std()) == 0 or float(right_hist.std()) == 0:
        correlation = float(np.allclose(left_hist, right_hist))
    else:
        correlation = float(np.corrcoef(left_hist, right_hist)[0, 1])
    pose_compatible = False
    if left.face_phashes and len(left.face_phashes) == len(right.face_phashes):
        face_distance = sum(
            (a ^ b).bit_count() for a, b in zip(left.face_phashes, right.face_phashes)
        ) / len(left.face_phashes)
        pose_compatible = face_distance <= 14
    if hamming <= 10 and correlation >= 0.95:
        return "discard"
    if pose_compatible and hamming <= 18 and correlation >= 0.95:
        return "discard"
    left_numbers = re.findall(r"\d+", left.path.stem)
    right_numbers = re.findall(r"\d+", right.path.stem)
    close_sequence = bool(
        left_numbers
        and right_numbers
        and abs(int(left_numbers[-1]) - int(right_numbers[-1])) <= 3
    )
    if close_sequence and pose_compatible and hamming <= 36 and correlation >= 0.84:
        return "discard"
    if close_sequence and _scene_related(left, right):
        return "suggestion"
    return None


def find_groups(
    distributed_dir: Path, progress: DiscardProgress | None = None
) -> list[DuplicateGroup]:
    direct_photos = [
        path
        for path in distributed_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    # Aceita tanto a pasta geral de álbuns quanto uma pasta individual (ex.: 001).
    albums = (
        [distributed_dir]
        if direct_photos
        else sorted(
            (path for path in distributed_dir.iterdir() if path.is_dir()),
            key=lambda path: natural_key(path.name),
        )
    )
    all_files = [
        (album, photo)
        for album in albums
        for photo in sorted(album.iterdir(), key=lambda path: natural_key(path.name))
        if photo.is_file() and photo.suffix.lower() in IMAGE_EXTENSIONS
    ]
    metrics_by_album: dict[Path, list[PhotoMetric]] = {album: [] for album in albums}
    for index, (album, photo) in enumerate(all_files, start=1):
        metrics_by_album[album].append(measure(photo))
        if progress:
            progress(index, len(all_files), f"{album.name}/{photo.name}")

    groups: list[DuplicateGroup] = []
    for album, metrics in metrics_by_album.items():
        current: list[PhotoMetric] = []
        current_category = "discard"

        def finish() -> None:
            if len(current) < 2:
                return
            best = max(current, key=lambda item: (item.quality, item.sharpness))
            discard = tuple(item.path for item in current if item.path != best.path)
            groups.append(
                DuplicateGroup(
                    album.name,
                    best.path,
                    discard,
                    tuple((item.path.name, item.quality) for item in current),
                    tuple(
                        (
                            item.path.name,
                            "melhor qualidade técnica"
                            if item.path == best.path
                            else (
                                "mesma cena; confira o enquadramento"
                                if current_category == "suggestion"
                                else "repetida; qualidade técnica inferior"
                            ),
                        )
                        for item in current
                    ),
                    current_category,
                )
            )

        for metric in metrics:
            similarity = _similarity(current[-1], metric) if current else "discard"
            if similarity:
                current.append(metric)
                if similarity == "suggestion":
                    current_category = "suggestion"
            else:
                finish()
                current = [metric]
                current_category = "discard"
        finish()
    return groups


def apply_discard(
    groups: list[DuplicateGroup], contract_root: Path
) -> tuple[int, Path]:
    discard_root = contract_root / "OUTROS" / "DESCARTE POR ÁLBUM"
    discard_root.mkdir(parents=True, exist_ok=True)
    operations: list[dict[str, str]] = []
    for group in groups:
        if not group.keep.exists():
            raise FileNotFoundError(f"A foto escolhida não existe: {group.keep}")
        destination_dir = discard_root / f"DESC {group.album_id}"
        destination_dir.mkdir(parents=True, exist_ok=True)
        for source in group.discard:
            if not source.exists():
                continue
            destination = destination_dir / source.name
            counter = 2
            while destination.exists():
                destination = destination_dir / f"{source.stem}__{counter}{source.suffix}"
                counter += 1
            shutil.move(str(source), str(destination))
            operations.append({"source": str(source), "destination": str(destination)})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest = discard_root / f"historico_{stamp}.json"
    manifest.write_text(
        json.dumps({"created_at": stamp, "operations": operations}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(operations), manifest


def undo_discard(manifest: Path) -> int:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    restored = 0
    for operation in reversed(data.get("operations", [])):
        source = Path(operation["source"])
        destination = Path(operation["destination"])
        if not destination.exists():
            continue
        if source.exists():
            raise FileExistsError(f"Não foi possível restaurar; já existe: {source}")
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(destination), str(source))
        restored += 1
    return restored
