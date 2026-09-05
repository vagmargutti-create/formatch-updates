from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from formatura_distribuidor.models import DistributionConfig
from formatura_distribuidor.exporter import export_project
from formatura_distribuidor.project_analyzer import analyze_project
from formatura_distribuidor.project_store import ProjectStore, natural_key


@dataclass
class Observation:
    bbox: tuple[int, int, int, int]
    embedding: np.ndarray
    candidates: tuple[tuple[str, float], ...]


class FakeAnalysisEngine:
    def build_index(self, recognition_dir: Path) -> list[str]:
        return []

    def analyze(self, photo: Path, candidate_count: int = 5) -> list[Observation]:
        matches = {
            "IMG_9.JPG": (("004", 0.82), ("007", 0.41)),
            "IMG_10.JPG": (("007", 0.78), ("004", 0.40)),
            "IMG_11.JPG": (),
        }
        return [
            Observation((10, 20, 50, 70), np.ones(512, dtype=np.float32), matches[photo.name])
        ]


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image")


class ProjectTests(unittest.TestCase):
    def test_possible_background_uses_relative_face_size(self) -> None:
        store = ProjectStore(self.root / "background.sqlite3")
        store.add_student("001", self.root / "001.jpg")
        store.add_student("002", self.root / "002.jpg")
        photo = self.root / "IMG_20.JPG"
        touch(photo)
        photo_id = store.add_photo(photo)
        observations = [
            Observation((0, 0, 200, 200), np.ones(512, dtype=np.float32), (("002", 0.80),)),
            Observation((300, 20, 420, 140), np.ones(512, dtype=np.float32), (("001", 0.75),)),
        ]
        store.save_analysis(photo_id, observations, 0.45)
        self.assertEqual([item.filename for item in store.possible_background_photos()], ["IMG_20.JPG"])
        store.close()

    def test_neighbor_copy_and_album_exclusion_are_per_student(self) -> None:
        store = ProjectStore(self.root / "project.sqlite3")
        store.add_student("001", self.root / "001.jpg")
        store.add_student("002", self.root / "002.jpg")
        first = self.root / "IMG_1.JPG"
        second = self.root / "IMG_2.JPG"
        touch(first)
        touch(second)
        first_id = store.add_photo(first)
        second_id = store.add_photo(second)
        store.connection.execute("UPDATE photos SET processed=1")
        store.connection.commit()
        store.assign(first_id, "001")
        store.assign(first_id, "002")
        copied = store.copy_assignments(first_id, second_id)
        self.assertEqual(copied, ("001", "002"))
        store.set_album_excluded(second_id, "001", True)
        rows = dict(store.export_rows())
        self.assertEqual(rows[second], ("002",))
        self.assertTrue(store.album_excluded(second_id, "001"))
        self.assertFalse(store.album_excluded(second_id, "002"))
        store.close()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_natural_filename_order(self) -> None:
        names = ["IMG_10.JPG", "IMG_9.JPG", "IMG_100.JPG", "IMG_11.JPG"]
        self.assertEqual(
            sorted(names, key=natural_key),
            ["IMG_9.JPG", "IMG_10.JPG", "IMG_11.JPG", "IMG_100.JPG"],
        )

    def test_analysis_saves_virtual_assignments_without_copying(self) -> None:
        recognition = self.root / "Reconhecimento"
        events = self.root / "Eventos"
        project = self.root / "Projeto"
        touch(recognition / "004.JPG")
        touch(recognition / "007.JPG")
        touch(events / "IMG_11.JPG")
        touch(events / "IMG_10.JPG")
        touch(events / "IMG_9.JPG")
        store = ProjectStore(project / "projeto.sqlite3")

        analyze_project(
            DistributionConfig(recognition, events, project),
            FakeAnalysisEngine(),
            store,
        )

        photos = store.photos_for_review()
        self.assertEqual([item.filename for item in photos], ["IMG_9.JPG", "IMG_10.JPG", "IMG_11.JPG"])
        self.assertEqual(photos[0].assignments, ("004",))
        self.assertEqual(photos[1].assignments, ("007",))
        self.assertEqual(photos[2].assignments, ())
        self.assertEqual([item.filename for item in store.photos_for_review(True)], ["IMG_11.JPG"])
        self.assertEqual(store.counts(), (3, 3, 1))
        self.assertEqual(store.album_summaries(), [("004", 1), ("007", 1)])
        self.assertEqual(
            [item.filename for item in store.photos_for_student("004")], ["IMG_9.JPG"]
        )
        self.assertFalse((project / "DISTRIBUÍDOS").exists())
        store.close()

    def test_manual_assignment_is_reversible(self) -> None:
        store = ProjectStore(self.root / "project.sqlite3")
        photo_path = self.root / "IMG_1.JPG"
        touch(photo_path)
        photo_id = store.add_photo(photo_path)
        store.connection.execute("UPDATE photos SET processed=1 WHERE id=?", (photo_id,))
        store.connection.commit()

        store.assign(photo_id, "085")
        self.assertEqual(store.photos_for_review()[0].assignments, ("085",))
        store.unassign(photo_id, "085")
        self.assertEqual(store.photos_for_review()[0].assignments, ())
        store.close()

    def test_export_happens_only_when_requested(self) -> None:
        store = ProjectStore(self.root / "project.sqlite3")
        first = self.root / "IMG_1.JPG"
        second = self.root / "IMG_2.JPG"
        touch(first)
        touch(second)
        first_id = store.add_photo(first)
        second_id = store.add_photo(second)
        store.connection.execute("UPDATE photos SET processed=1")
        store.assign(first_id, "004")
        store.assign(first_id, "007")
        store.connection.commit()
        destination = self.root / "export"
        self.assertFalse(destination.exists())

        copied, counts = export_project(store, destination)

        self.assertEqual(copied, 3)
        self.assertEqual(counts, {"004": 1, "007": 1})
        self.assertTrue((destination / "DISTRIBUÍDOS" / "004" / "IMG_1.JPG").exists())
        self.assertTrue((destination / "DISTRIBUÍDOS" / "007" / "IMG_1.JPG").exists())
        self.assertTrue((destination / "SEM IDENTIFICAÇÃO" / "IMG_2.JPG").exists())
        store.close()

    def test_optional_backup_mirrors_all_albums_but_not_unidentified(self) -> None:
        store = ProjectStore(self.root / "backup.sqlite3")
        recognized = self.root / "IMG_1.JPG"
        new_id = self.root / "IMG_2.JPG"
        unidentified = self.root / "IMG_3.JPG"
        for photo in (recognized, new_id, unidentified):
            touch(photo)
            store.add_photo(photo)
        store.connection.execute("UPDATE photos SET processed=1")
        store.connection.commit()
        store.assign(1, "001")
        store.assign(2, "SI001")

        destination = self.root / "export-backup"
        export_project(store, destination, create_backup=True)

        backup = destination / "OUTROS" / "CÓPIA DE SEGURANÇA"
        self.assertTrue((backup / "001" / "IMG_1.JPG").exists())
        self.assertTrue((backup / "SI001" / "IMG_2.JPG").exists())
        self.assertFalse((backup / "SEM IDENTIFICAÇÃO").exists())
        self.assertFalse((backup / "IMG_3.JPG").exists())
        self.assertTrue((destination / "SEM IDENTIFICAÇÃO" / "IMG_3.JPG").exists())
        store.close()

    def test_new_id_rescans_unidentified_faces(self) -> None:
        store = ProjectStore(self.root / "project.sqlite3")
        seed = np.ones(512, dtype=np.float32)
        seed /= np.linalg.norm(seed)
        for index, similarity in enumerate((1.0, 0.8, 0.3), start=1):
            photo = self.root / f"IMG_{index}.JPG"
            touch(photo)
            photo_id = store.add_photo(photo)
            vector = seed.copy()
            if similarity < 0.5:
                vector = np.concatenate((np.ones(256), -np.ones(256))).astype(np.float32)
                vector /= np.linalg.norm(vector)
            observation = Observation((0, 0, 10, 10), vector, ())
            store.save_analysis(photo_id, [observation], 0.45)

        automatic, suggestions = store.rescan_unidentified("085", seed)

        self.assertEqual(automatic, 2)
        self.assertEqual(suggestions, [])
        self.assertEqual(len(store.photos_for_review(True)), 1)
        store.close()


if __name__ == "__main__":
    unittest.main()
