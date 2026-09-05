from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from formatura_distribuidor.distributor import distribute, validate_config
from formatura_distribuidor.models import DistributionConfig


class FakeEngine:
    def __init__(self, results: dict[str, set[str]]) -> None:
        self.results = results

    def build_index(self, recognition_dir: Path) -> list[str]:
        return []

    def recognize(self, photo: Path) -> set[str]:
        return self.results.get(photo.name, set())


def touch(path: Path, content: bytes = b"photo") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class DistributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_distributes_to_all_students_and_unknown_only_when_none(self) -> None:
        recognition = self.root / "Reconhecimento"
        events = self.root / "Eventos"
        output = self.root / "Saida"
        touch(recognition / "004.JPG")
        touch(recognition / "007.JPG")
        touch(events / "individual.jpg")
        touch(events / "grupo.jpg")
        touch(events / "professor.jpg")
        engine = FakeEngine({
            "individual.jpg": {"004"},
            "grupo.jpg": {"004", "007"},
            "professor.jpg": set(),
        })

        stats, _ = distribute(DistributionConfig(recognition, events, output), engine)

        self.assertTrue((output / "DISTRIBUÍDOS" / "004" / "individual.jpg").exists())
        self.assertTrue((output / "DISTRIBUÍDOS" / "004" / "grupo.jpg").exists())
        self.assertTrue((output / "DISTRIBUÍDOS" / "007" / "grupo.jpg").exists())
        self.assertTrue((output / "SEM IDENTIFICAÇÃO" / "professor.jpg").exists())
        self.assertFalse((output / "SEM IDENTIFICAÇÃO" / "grupo.jpg").exists())
        self.assertEqual(stats.processed, 3)
        self.assertEqual(stats.copied_files, 4)
        self.assertEqual(stats.album_counts, {"004": 2, "007": 1})

    def test_creates_album_even_with_zero_matches(self) -> None:
        recognition = self.root / "r"
        events = self.root / "e"
        output = self.root / "o"
        touch(recognition / "009.JPG")
        touch(events / "professor.jpg")

        stats, _ = distribute(
            DistributionConfig(recognition, events, output),
            FakeEngine({"professor.jpg": set()}),
        )

        self.assertTrue((output / "DISTRIBUÍDOS" / "009").is_dir())
        self.assertEqual(stats.album_counts, {"009": 0})

    def test_never_changes_originals(self) -> None:
        recognition = self.root / "r"
        events = self.root / "e"
        output = self.root / "o"
        touch(recognition / "004.JPG")
        original = events / "foto.jpg"
        touch(original, b"original")

        distribute(
            DistributionConfig(recognition, events, output),
            FakeEngine({"foto.jpg": {"004"}}),
        )

        self.assertEqual(original.read_bytes(), b"original")
        self.assertEqual(
            (output / "DISTRIBUÍDOS" / "004" / "foto.jpg").read_bytes(), b"original"
        )

    def test_duplicate_filenames_do_not_overwrite(self) -> None:
        recognition = self.root / "r"
        events = self.root / "e"
        output = self.root / "o"
        touch(recognition / "004.JPG")
        touch(events / "dia1" / "foto.jpg", b"one")
        touch(events / "dia2" / "foto.jpg", b"two")

        distribute(
            DistributionConfig(recognition, events, output),
            FakeEngine({"foto.jpg": {"004"}}),
        )

        album = output / "DISTRIBUÍDOS" / "004"
        self.assertEqual(
            sorted(path.name for path in album.iterdir()), ["foto.jpg", "foto__2.jpg"]
        )
        self.assertEqual({path.read_bytes() for path in album.iterdir()}, {b"one", b"two"})

    def test_rerun_does_not_duplicate_existing_copies(self) -> None:
        recognition = self.root / "r"
        events = self.root / "e"
        output = self.root / "o"
        touch(recognition / "004.JPG")
        touch(events / "foto.jpg", b"same photo")
        config = DistributionConfig(recognition, events, output)
        engine = FakeEngine({"foto.jpg": {"004"}})

        first, _ = distribute(config, engine)
        second, _ = distribute(config, engine)

        album = output / "DISTRIBUÍDOS" / "004"
        self.assertEqual([path.name for path in album.iterdir()], ["foto.jpg"])
        self.assertEqual(first.copied_files, 1)
        self.assertEqual(second.copied_files, 0)

    def test_rejects_invalid_folder_nesting(self) -> None:
        same = self.root / "same"
        same.mkdir()
        with self.assertRaises(ValueError):
            validate_config(DistributionConfig(same, same, self.root / "out"))

        recognition = self.root / "r"
        events = self.root / "e"
        recognition.mkdir()
        events.mkdir()
        with self.assertRaises(ValueError):
            validate_config(DistributionConfig(recognition, events, events / "out"))


if __name__ == "__main__":
    unittest.main()
