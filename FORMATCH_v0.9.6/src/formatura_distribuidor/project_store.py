from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np


def natural_key(filename: str) -> str:
    parts = re.split(r"(\d+)", filename.casefold())
    return "".join(part.zfill(16) if part.isdigit() else part for part in parts)


@dataclass(frozen=True)
class ReviewPhoto:
    id: int
    path: Path
    filename: str
    assignments: tuple[str, ...]


class ProjectStore:
    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self.database = database
        self.connection = sqlite3.connect(database)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS students (
                identifier TEXT PRIMARY KEY,
                reference_path TEXT NOT NULL,
                embedding BLOB
            );
            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                sort_key TEXT NOT NULL,
                processed INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS faces (
                id INTEGER PRIMARY KEY,
                photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
                x1 INTEGER NOT NULL, y1 INTEGER NOT NULL,
                x2 INTEGER NOT NULL, y2 INTEGER NOT NULL,
                embedding BLOB NOT NULL,
                candidates_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assignments (
                photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
                student_id TEXT NOT NULL,
                confidence REAL NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY(photo_id, student_id)
            );
            CREATE INDEX IF NOT EXISTS photos_sort ON photos(sort_key);
            CREATE INDEX IF NOT EXISTS faces_photo ON faces(photo_id);
            CREATE INDEX IF NOT EXISTS assignments_student ON assignments(student_id);
            CREATE TABLE IF NOT EXISTS album_exclusions (
                photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
                student_id TEXT NOT NULL,
                PRIMARY KEY(photo_id, student_id)
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def set_setting(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.connection.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.connection.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return str(row[0]) if row else default

    def add_student(
        self, identifier: str, reference_path: Path, embedding: np.ndarray | None = None
    ) -> None:
        blob = (
            np.asarray(embedding, dtype=np.float32).tobytes()
            if embedding is not None
            else None
        )
        self.connection.execute(
            "INSERT INTO students(identifier,reference_path,embedding) VALUES(?,?,?) "
            "ON CONFLICT(identifier) DO UPDATE SET "
            "reference_path=excluded.reference_path,"
            "embedding=COALESCE(excluded.embedding,students.embedding)",
            (identifier, str(reference_path), blob),
        )
        self.connection.commit()

    def student_exists(self, identifier: str) -> bool:
        return bool(
            self.connection.execute(
                "SELECT 1 FROM students WHERE identifier=?", (identifier,)
            ).fetchone()
        )

    def students(self) -> list[tuple[str, Path]]:
        return [
            (str(row[0]), Path(row[1]))
            for row in self.connection.execute(
                "SELECT identifier,reference_path FROM students ORDER BY identifier"
            ).fetchall()
        ]

    def album_summaries(self) -> list[tuple[str, int]]:
        return [
            (str(row[0]), int(row[1]))
            for row in self.connection.execute(
                """
                SELECT s.identifier,COUNT(a.photo_id)
                FROM students s
                LEFT JOIN assignments a ON a.student_id=s.identifier
                GROUP BY s.identifier
                ORDER BY s.identifier
                """
            ).fetchall()
        ]

    def photos_for_student(self, identifier: str) -> list[ReviewPhoto]:
        rows = self.connection.execute(
            """
            SELECT p.id,p.path,p.filename,
                   COALESCE(GROUP_CONCAT(all_a.student_id, ','),'')
            FROM photos p
            JOIN assignments selected_a
              ON selected_a.photo_id=p.id AND selected_a.student_id=?
            LEFT JOIN assignments all_a ON all_a.photo_id=p.id
            WHERE p.processed=1
            GROUP BY p.id
            ORDER BY p.sort_key,p.path
            """,
            (identifier,),
        ).fetchall()
        return [
            ReviewPhoto(int(row[0]), Path(row[1]), row[2], tuple(filter(None, row[3].split(","))))
            for row in rows
        ]

    def add_photo(self, path: Path) -> int:
        self.connection.execute(
            "INSERT OR IGNORE INTO photos(path,filename,sort_key) VALUES(?,?,?)",
            (str(path), path.name, natural_key(path.name)),
        )
        row = self.connection.execute(
            "SELECT id FROM photos WHERE path=?", (str(path),)
        ).fetchone()
        assert row
        return int(row[0])

    def photo_processed(self, path: Path) -> bool:
        row = self.connection.execute(
            "SELECT processed FROM photos WHERE path=?", (str(path),)
        ).fetchone()
        return bool(row and row[0])

    def save_analysis(self, photo_id: int, observations: list, threshold: float) -> None:
        self.connection.execute("DELETE FROM faces WHERE photo_id=?", (photo_id,))
        self.connection.execute(
            "DELETE FROM assignments WHERE photo_id=? AND source='automatic'", (photo_id,)
        )
        for observation in observations:
            self.connection.execute(
                "INSERT INTO faces(photo_id,x1,y1,x2,y2,embedding,candidates_json) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    photo_id,
                    *observation.bbox,
                    np.asarray(observation.embedding, dtype=np.float32).tobytes(),
                    json.dumps(observation.candidates),
                ),
            )
            if observation.candidates and observation.candidates[0][1] >= threshold:
                identifier, score = observation.candidates[0]
                self.assign(photo_id, identifier, score, "automatic", commit=False)
        self.connection.execute(
            "UPDATE photos SET processed=1,error=NULL WHERE id=?", (photo_id,)
        )
        self.connection.commit()

    def save_error(self, photo_id: int, error: str) -> None:
        self.connection.execute(
            "UPDATE photos SET processed=1,error=? WHERE id=?", (error, photo_id)
        )
        self.connection.commit()

    def assign(
        self,
        photo_id: int,
        student_id: str,
        confidence: float = 1.0,
        source: str = "manual",
        commit: bool = True,
    ) -> None:
        self.connection.execute(
            "INSERT INTO assignments(photo_id,student_id,confidence,source) VALUES(?,?,?,?) "
            "ON CONFLICT(photo_id,student_id) DO UPDATE SET "
            "confidence=excluded.confidence,source=excluded.source",
            (photo_id, student_id, confidence, source),
        )
        if commit:
            self.connection.commit()

    def unassign(self, photo_id: int, student_id: str) -> None:
        self.connection.execute(
            "DELETE FROM assignments WHERE photo_id=? AND student_id=?",
            (photo_id, student_id),
        )
        self.connection.commit()

    def copy_assignments(self, source_photo_id: int, target_photo_id: int) -> tuple[str, ...]:
        rows = self.connection.execute(
            "SELECT student_id FROM assignments WHERE photo_id=? ORDER BY student_id",
            (source_photo_id,),
        ).fetchall()
        identifiers = tuple(str(row[0]) for row in rows)
        for identifier in identifiers:
            self.assign(target_photo_id, identifier, 1.0, "manual-neighbor", commit=False)
        self.connection.commit()
        return identifiers

    def set_album_excluded(self, photo_id: int, student_id: str, excluded: bool) -> None:
        if excluded:
            self.connection.execute(
                "INSERT OR IGNORE INTO album_exclusions(photo_id,student_id) VALUES(?,?)",
                (photo_id, student_id),
            )
        else:
            self.connection.execute(
                "DELETE FROM album_exclusions WHERE photo_id=? AND student_id=?",
                (photo_id, student_id),
            )
        self.connection.commit()

    def album_excluded(self, photo_id: int, student_id: str) -> bool:
        return bool(
            self.connection.execute(
                "SELECT 1 FROM album_exclusions WHERE photo_id=? AND student_id=?",
                (photo_id, student_id),
            ).fetchone()
        )

    def album_exclusion_count(self, student_id: str) -> int:
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM album_exclusions WHERE student_id=?", (student_id,)
            ).fetchone()[0]
        )

    def possible_background_photos(self) -> list[ReviewPhoto]:
        possible: list[ReviewPhoto] = []
        for photo in self.photos_for_review(False):
            faces = self.faces_for_photo(photo.id)
            if not faces:
                continue
            areas = [max(1, (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1])) for f in faces]
            largest = max(areas)
            suspicious = False
            for face, area in zip(faces, areas):
                candidates = face["candidates"]
                if not candidates or candidates[0][0] not in photo.assignments:
                    continue
                relative_size = area / largest
                best_score = float(candidates[0][1])
                second_score = float(candidates[1][1]) if len(candidates) > 1 else -1.0
                uncertain_identity = best_score < 0.52 or best_score - second_score < 0.08
                if relative_size < 0.70 or uncertain_identity:
                    suspicious = True
                    break
            if suspicious:
                possible.append(photo)
        return possible

    def photos_for_review(self, only_unidentified: bool = False) -> list[ReviewPhoto]:
        where = (
            "WHERE p.processed=1 AND NOT EXISTS "
            "(SELECT 1 FROM assignments a2 WHERE a2.photo_id=p.id)"
            if only_unidentified
            else "WHERE p.processed=1"
        )
        rows = self.connection.execute(
            f"""
            SELECT p.id,p.path,p.filename,
                   COALESCE(GROUP_CONCAT(a.student_id, ','),'')
            FROM photos p LEFT JOIN assignments a ON a.photo_id=p.id
            {where}
            GROUP BY p.id ORDER BY p.sort_key,p.path
            """
        ).fetchall()
        return [
            ReviewPhoto(int(row[0]), Path(row[1]), row[2], tuple(filter(None, row[3].split(","))))
            for row in rows
        ]

    def faces_for_photo(self, photo_id: int) -> list[dict]:
        rows = self.connection.execute(
            "SELECT id,x1,y1,x2,y2,embedding,candidates_json FROM faces WHERE photo_id=?",
            (photo_id,),
        ).fetchall()
        return [
            {
                "id": int(row[0]),
                "bbox": tuple(int(value) for value in row[1:5]),
                "embedding": np.frombuffer(row[5], dtype=np.float32).copy(),
                "candidates": tuple((item[0], float(item[1])) for item in json.loads(row[6])),
            }
            for row in rows
        ]

    def counts(self) -> tuple[int, int, int]:
        total = int(self.connection.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
        processed = int(
            self.connection.execute("SELECT COUNT(*) FROM photos WHERE processed=1").fetchone()[0]
        )
        unidentified = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM photos p WHERE processed=1 AND NOT EXISTS "
                "(SELECT 1 FROM assignments a WHERE a.photo_id=p.id)"
            ).fetchone()[0]
        )
        return total, processed, unidentified

    def export_rows(self) -> list[tuple[Path, tuple[str, ...]]]:
        rows: list[tuple[Path, tuple[str, ...]]] = []
        for photo in self.photos_for_review(False):
            assignments = tuple(
                identifier
                for identifier in photo.assignments
                if not self.album_excluded(photo.id, identifier)
            )
            # Uma foto reconhecida, mas descartada de todos os seus álbuns, não vira SEM ID.
            if photo.assignments and not assignments:
                continue
            rows.append((photo.path, assignments))
        return rows

    def rescan_unidentified(
        self,
        student_id: str,
        seed_embedding: np.ndarray,
        high_threshold: float = 0.55,
        medium_threshold: float = 0.45,
    ) -> tuple[int, list[tuple[int, int, float]]]:
        seed = np.asarray(seed_embedding, dtype=np.float32)
        seed /= max(float(np.linalg.norm(seed)), 1e-12)
        rows = self.connection.execute(
            """
            SELECT f.id,f.photo_id,f.embedding
            FROM faces f
            WHERE NOT EXISTS (
                SELECT 1 FROM assignments a WHERE a.photo_id=f.photo_id
            )
            """
        ).fetchall()
        auto_photos: set[int] = set()
        suggestions: list[tuple[int, int, float]] = []
        for face_id, photo_id, blob in rows:
            candidate = np.frombuffer(blob, dtype=np.float32).copy()
            candidate /= max(float(np.linalg.norm(candidate)), 1e-12)
            score = float(seed @ candidate)
            if score >= high_threshold:
                self.assign(int(photo_id), student_id, score, "confirmed-rescan", commit=False)
                auto_photos.add(int(photo_id))
            elif score >= medium_threshold:
                suggestions.append((int(photo_id), int(face_id), score))
        self.connection.commit()
        suggestions.sort(key=lambda item: item[2], reverse=True)
        return len(auto_photos), suggestions
