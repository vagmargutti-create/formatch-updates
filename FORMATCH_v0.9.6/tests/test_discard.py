from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFilter

from formatura_distribuidor.discard_engine import (
    PhotoMetric,
    _similarity,
    apply_discard,
    find_groups,
    undo_discard,
)


def image(path: Path, blur: bool = False, variation: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (480, 320), "black")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((80 + variation, 60, 380 + variation, 270), fill=(60, 180, 220))
    draw.ellipse((185 + variation, 95, 295 + variation, 205), fill=(245, 245, 245))
    draw.text((205 + variation, 145), "FOTO", fill="black")
    if blur:
        canvas = canvas.filter(ImageFilter.GaussianBlur(5))
    canvas.save(path, "JPEG", quality=94)


class DiscardTests(unittest.TestCase):
    def test_same_scene_with_different_crop_is_only_a_suggestion(self) -> None:
        histogram = tuple([0.0] * 128)
        left = PhotoMetric(Path("foto_2251.jpg"), 80, 100, 0, histogram, "a", 0, ())
        right = PhotoMetric(Path("foto_2253.jpg"), 90, 110, (1 << 40) - 1, histogram, "b", 0, ())
        with patch("formatura_distribuidor.discard_engine._scene_related", return_value=True):
            self.assertEqual(_similarity(left, right), "suggestion")

    def test_accepts_one_individual_album_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            album = Path(directory) / "ALBUNS" / "001"
            album.mkdir(parents=True)
            image = Image.new("RGB", (100, 100), "red")
            image.save(album / "foto_1.jpg")
            image.save(album / "foto_2.jpg")
            groups = find_groups(album)
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].album_id, "001")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_keeps_sharper_photo_and_moves_other_reversibly(self) -> None:
        album = self.root / "DISTRIBUÍDOS" / "001"
        sharp = album / "IMG_100.JPG"
        blurred = album / "IMG_101.JPG"
        image(sharp)
        image(blurred, blur=True)

        groups = find_groups(self.root / "DISTRIBUÍDOS")

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].keep, sharp)
        self.assertEqual(groups[0].discard, (blurred,))
        moved, manifest = apply_discard(groups, self.root)
        self.assertEqual(moved, 1)
        self.assertFalse(blurred.exists())
        self.assertTrue((self.root / "OUTROS" / "DESCARTE POR ÁLBUM" / "DESC 001" / "IMG_101.JPG").exists())
        self.assertEqual(undo_discard(manifest), 1)
        self.assertTrue(blurred.exists())

    def test_different_photos_are_not_grouped(self) -> None:
        album = self.root / "DISTRIBUÍDOS" / "002"
        image(album / "IMG_1.JPG", variation=0)
        image(album / "IMG_2.JPG", variation=80)
        self.assertEqual(find_groups(self.root / "DISTRIBUÍDOS"), [])


if __name__ == "__main__":
    unittest.main()
