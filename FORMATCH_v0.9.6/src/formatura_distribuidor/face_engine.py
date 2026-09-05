from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass

import cv2
import numpy as np

from .models import image_files


class FaceEngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class FaceObservation:
    bbox: tuple[int, int, int, int]
    embedding: np.ndarray
    candidates: tuple[tuple[str, float], ...]


class InsightFaceEngine:
    """Local face matcher backed by InsightFace/ONNX Runtime."""

    def __init__(self, threshold: float = 0.45) -> None:
        self.threshold = threshold
        self.identifiers: list[str] = []
        self.embeddings = np.empty((0, 512), dtype=np.float32)
        cuda_preloaded = False
        try:
            import onnxruntime as ort
            # ONNX Runtime 1.21+ can load CUDA/cuDNN installed inside the
            # virtual environment. This avoids a separate CUDA Toolkit setup.
            if hasattr(ort, "preload_dlls"):
                try:
                    ort.preload_dlls(directory="")
                    cuda_preloaded = True
                except Exception:
                    cuda_preloaded = False
            from insightface.app import FaceAnalysis
        except ImportError as exc:
            raise FaceEngineError(
                "Dependências do reconhecimento facial não estão instaladas."
            ) from exc

        available = set(ort.get_available_providers())
        providers = []
        if "CUDAExecutionProvider" in available and cuda_preloaded:
            providers.append("CUDAExecutionProvider")
        providers.append("CPUExecutionProvider")
        self.using_gpu = "CUDAExecutionProvider" in providers
        self.app = FaceAnalysis(name="buffalo_l", providers=providers)
        ctx_id = 0 if self.using_gpu else -1
        self.app.prepare(ctx_id=ctx_id, det_size=(640, 640))

    @staticmethod
    def _read(path: Path) -> np.ndarray:
        data = np.fromfile(path, dtype=np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if image is None:
            raise FaceEngineError(f"Não foi possível abrir: {path.name}")
        return image

    @staticmethod
    def _normalized(embedding: np.ndarray) -> np.ndarray:
        vector = np.asarray(embedding, dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            raise FaceEngineError("Representação facial inválida.")
        return vector / norm

    def build_index(self, recognition_dir: Path) -> list[str]:
        identifiers: list[str] = []
        embeddings: list[np.ndarray] = []
        warnings: list[str] = []

        for path in image_files(recognition_dir):
            image = self._read(path)
            faces = self.app.get(image)
            if not faces:
                warnings.append(f"{path.name}: nenhum rosto encontrado")
                continue
            if len(faces) > 1:
                warnings.append(
                    f"{path.name}: vários rostos; usado o maior rosto da imagem"
                )
            face = max(
                faces,
                key=lambda item: float(
                    (item.bbox[2] - item.bbox[0]) * (item.bbox[3] - item.bbox[1])
                ),
            )
            identifiers.append(path.stem)
            embeddings.append(self._normalized(face.embedding))

        if not embeddings:
            raise FaceEngineError("Nenhuma referência facial válida foi encontrada.")
        if len(set(identifiers)) != len(identifiers):
            raise FaceEngineError(
                "Existem fotos de reconhecimento com o mesmo nome em subpastas diferentes."
            )

        self.identifiers = identifiers
        self.embeddings = np.vstack(embeddings)
        return warnings

    def recognize(self, photo: Path) -> set[str]:
        matches: set[str] = set()
        for observation in self.analyze(photo):
            if observation.candidates and observation.candidates[0][1] >= self.threshold:
                matches.add(observation.candidates[0][0])
        return matches

    def analyze(self, photo: Path, candidate_count: int = 5) -> list[FaceObservation]:
        if not self.identifiers:
            raise FaceEngineError("O cadastro facial ainda não foi carregado.")
        faces = self.app.get(self._read(photo))
        observations: list[FaceObservation] = []
        for face in faces:
            candidate = self._normalized(face.embedding)
            scores = self.embeddings @ candidate
            order = np.argsort(scores)[::-1][:candidate_count]
            candidates = tuple(
                (self.identifiers[int(index)], float(scores[int(index)]))
                for index in order
            )
            bbox = tuple(int(round(value)) for value in face.bbox)
            observations.append(FaceObservation(bbox, candidate, candidates))
        return observations

    def candidates_for_embedding(
        self, embedding: np.ndarray, count: int = 5
    ) -> tuple[tuple[str, float], ...]:
        candidate = self._normalized(embedding)
        scores = self.embeddings @ candidate
        order = np.argsort(scores)[::-1][:count]
        return tuple(
            (self.identifiers[int(index)], float(scores[int(index)])) for index in order
        )
