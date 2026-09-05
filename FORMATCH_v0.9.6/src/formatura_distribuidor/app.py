from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPalette, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QToolButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .animation import DistributionAnimation
from .distributor import DistributionCancelled, validate_config
from .discard_window import DiscardWindow
from .exporter import export_project
from .face_engine import InsightFaceEngine
from .models import DistributionConfig
from .project_analyzer import analyze_project
from .project_store import ProjectStore, ReviewPhoto
from .updater import (
    UpdateInfo,
    configured_manifest_url,
    download_update,
    fetch_update,
    launch_update,
)
from . import __version__


STYLE = """
QWidget { background: #090d0b; color: #f2f5f3; font: 11pt 'Segoe UI'; }
QMainWindow { background: #090d0b; }
QLabel#title { font-size: 25pt; font-weight: 700; color: #ffffff; }
QLabel#subtitle { color: #9ca8a1; }
QLineEdit, QListWidget, QComboBox {
  background: #111713; border: 1px solid #2b3830; border-radius: 7px;
  padding: 8px; selection-background-color: #1dad43;
}
QPushButton, QToolButton {
  background: #121a15; border: 1px solid #3c4c41; border-radius: 7px;
  padding: 9px 14px; font-weight: 600;
}
QPushButton:hover, QToolButton:hover { border-color: #25df58; color: #33e861; }
QPushButton#start {
  background: #16b83d; border-color: #28e258; color: #051408;
  font-size: 13pt; padding: 13px 26px;
}
QPushButton#start:hover { background: #28db52; }
QPushButton:disabled { color: #617066; border-color: #243029; background: #0d120f; }
QProgressBar {
  background: #1b221e; border: none; border-radius: 6px; height: 12px; text-align: center;
}
QProgressBar::chunk { background: #25d951; border-radius: 6px; }
QSplitter::handle { background: #1d2a22; width: 2px; }
"""


def duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "calculando..."
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class PhotoLabel(QLabel):
    face_clicked = Signal(int)

    def __init__(self) -> None:
        super().__init__("Selecione uma foto")
        self.original = QPixmap()
        self.boxes: list[tuple[int, int, int, int]] = []
        self.selected = -1
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_photo(
        self, pixmap: QPixmap, boxes: list[tuple[int, int, int, int]], selected: int = 0
    ) -> None:
        self.original = pixmap
        self.boxes = boxes
        self.selected = selected if boxes else -1
        self.render_photo()

    def render_photo(self) -> None:
        if self.original.isNull():
            self.clear()
            self.setText("Não foi possível abrir a foto")
            return
        scaled = self.original.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        canvas = QPixmap(scaled)
        painter = QPainter(canvas)
        scale_x = scaled.width() / self.original.width()
        scale_y = scaled.height() / self.original.height()
        for index, (x1, y1, x2, y2) in enumerate(self.boxes):
            color = QColor("#22df55") if index == self.selected else QColor("#e8a317")
            painter.setPen(QPen(color, 3))
            painter.drawRect(
                int(x1 * scale_x),
                int(y1 * scale_y),
                int((x2 - x1) * scale_x),
                int((y2 - y1) * scale_y),
            )
            painter.drawText(int(x1 * scale_x), max(18, int(y1 * scale_y) - 5), str(index + 1))
        painter.end()
        self.setPixmap(canvas)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.original.isNull() or not self.boxes or not self.pixmap():
            return super().mousePressEvent(event)
        shown = self.pixmap()
        offset_x = (self.width() - shown.width()) / 2
        offset_y = (self.height() - shown.height()) / 2
        px = (event.position().x() - offset_x) * self.original.width() / shown.width()
        py = (event.position().y() - offset_y) * self.original.height() / shown.height()
        for index, (x1, y1, x2, y2) in enumerate(self.boxes):
            if x1 <= px <= x2 and y1 <= py <= y2:
                self.selected = index
                self.render_photo()
                self.face_clicked.emit(index)
                return


class AnalysisWorker(QObject):
    progressed = Signal(int, int, int, float, float, str)
    finished = Signal(str, bool, str)

    def __init__(self, config: DistributionConfig, database: Path) -> None:
        super().__init__()
        self.config = config
        self.database = database
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        store: ProjectStore | None = None
        started = time.monotonic()
        try:
            validate_config(self.config)
            store = ProjectStore(self.database)
            engine = InsightFaceEngine(self.config.confidence_threshold)

            def report(
                done: int, total: int, unidentified: int, photo: Path, matches: set[str]
            ) -> None:
                elapsed = max(time.monotonic() - started, 0.001)
                speed = done / elapsed
                remaining = (total - done) / speed if done >= 5 and speed > 0 else -1.0
                ids = ", ".join(sorted(matches)) if matches else "Sem identificação"
                self.progressed.emit(
                    done, total, unidentified, speed, remaining, f"{photo.name} → {ids}"
                )

            analyze_project(
                self.config,
                engine,
                store,
                progress=report,
                stop_event=self.stop_event,
            )
            self.finished.emit(str(self.database), engine.using_gpu, "")
        except DistributionCancelled as exc:
            self.finished.emit(str(self.database), False, str(exc))
        except Exception as exc:
            self.finished.emit(str(self.database), False, str(exc))
        finally:
            if store:
                store.close()


class UpdateCheckWorker(QObject):
    finished = Signal(object, str)

    def __init__(self, manifest_url: str) -> None:
        super().__init__()
        self.manifest_url = manifest_url

    def run(self) -> None:
        try:
            self.finished.emit(fetch_update(self.manifest_url, __version__), "")
        except Exception as exc:
            self.finished.emit(None, str(exc))


class UpdateDownloadWorker(QObject):
    progressed = Signal(int, int)
    finished = Signal(object, str)

    def __init__(self, info: object) -> None:
        super().__init__()
        self.info = info

    def run(self) -> None:
        try:
            package = download_update(
                self.info,  # type: ignore[arg-type]
                lambda done, total: self.progressed.emit(done, total),
            )
            self.finished.emit(package, "")
        except Exception as exc:
            self.finished.emit(None, str(exc))


class FolderRow(QWidget):
    def __init__(self, title: str, helper: str) -> None:
        super().__init__()
        layout = QGridLayout(self)
        label = QLabel(title)
        label.setStyleSheet("font-size: 14pt; font-weight: 700;")
        hint = QLabel(helper)
        hint.setObjectName("subtitle")
        self.path = QLineEdit()
        self.path.setPlaceholderText("Selecione uma pasta...")
        button = QPushButton("Selecionar pasta")
        button.clicked.connect(self.select)
        layout.addWidget(label, 0, 0)
        layout.addWidget(hint, 1, 0)
        layout.addWidget(self.path, 2, 0)
        layout.addWidget(button, 2, 1)

    def select(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Selecionar pasta", self.path.text())
        if folder:
            self.path.setText(folder)


class ReviewWindow(QMainWindow):
    def __init__(self, database: Path) -> None:
        super().__init__()
        self.store = ProjectStore(database)
        self.all_photos: list[ReviewPhoto] = []
        self.album_photos: list[ReviewPhoto] = []
        self.unknown_photos: list[ReviewPhoto] = []
        self.background_photos: list[ReviewPhoto] = []
        self.current: ReviewPhoto | None = None
        self.current_faces: list[dict] = []
        self.reference_paths = dict(self.store.students())
        self.setWindowTitle("Revisão da Distribuição")
        self.resize(1450, 880)

        root = QWidget()
        outer = QVBoxLayout(root)
        header = QHBoxLayout()
        title = QLabel("Revisão da Distribuição")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()
        export_button = QPushButton("EXPORTAR PASTAS")
        export_button.setObjectName("start")
        export_button.clicked.connect(self.export)
        header.addWidget(export_button)
        outer.addLayout(header)

        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self.tab_changed)
        recognized_page = QWidget()
        recognized_layout = QVBoxLayout(recognized_page)
        recognized_layout.addWidget(QLabel("Pastas dos formandos"))
        self.album_list = QListWidget()
        self.album_list.currentRowChanged.connect(self.select_album)
        recognized_layout.addWidget(self.album_list, 2)
        recognized_layout.addWidget(QLabel("Fotos do álbum selecionado"))
        self.album_photo_list = QListWidget()
        self.album_photo_list.currentRowChanged.connect(self.select_album_photo)
        recognized_layout.addWidget(self.album_photo_list, 3)
        self.tabs.addTab(recognized_page, "Reconhecidos")
        unknown_page = QWidget()
        unknown_layout = QVBoxLayout(unknown_page)
        unknown_layout.addWidget(QLabel("Fotos sem formando identificado"))
        self.unknown_list = QListWidget()
        self.unknown_list.currentRowChanged.connect(self.select_unknown_photo)
        unknown_layout.addWidget(self.unknown_list)
        self.tabs.addTab(unknown_page, "Sem ID")
        background_page = QWidget()
        background_layout = QVBoxLayout(background_page)
        background_layout.addWidget(QLabel("Reconhecidas, mas possivelmente ao fundo"))
        self.background_list = QListWidget()
        self.background_list.currentRowChanged.connect(self.select_background_photo)
        background_layout.addWidget(self.background_list)
        self.tabs.addTab(background_page, "Possível fundo")
        shortcuts_page = QWidget()
        shortcuts_layout = QVBoxLayout(shortcuts_page)
        shortcuts_layout.addWidget(QLabel(
            "A  • copiar identificação da foto anterior\n"
            "D  • copiar identificação da próxima foto\n"
            "1 a 5  • escolher um dos cinco formandos sugeridos\n"
            "Delete  • remover a identificação selecionada\n"
            "Os atalhos ficam pausados enquanto você digita um ID."
        ))
        shortcuts_layout.addStretch()
        self.tabs.addTab(shortcuts_page, "Atalhos")
        left_layout.addWidget(self.tabs)
        splitter.addWidget(left)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        self.image = PhotoLabel()
        self.image.face_clicked.connect(self.face_selector_from_image)
        self.image.setMinimumSize(650, 560)
        self.image.setStyleSheet("background:#050806;border:1px solid #26352b;")
        center_layout.addWidget(self.image, 1)
        navigation = QHBoxLayout()
        previous = QPushButton("◀ Foto anterior")
        previous.clicked.connect(lambda: self.move(-1))
        following = QPushButton("Próxima foto ▶")
        following.clicked.connect(lambda: self.move(1))
        navigation.addWidget(previous)
        navigation.addWidget(following)
        center_layout.addLayout(navigation)
        splitter.addWidget(center)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Identificações da foto"))
        self.assignment_list = QListWidget()
        self.assignment_list.setMaximumHeight(120)
        right_layout.addWidget(self.assignment_list)
        remove = QPushButton("Remover identificação selecionada")
        remove.clicked.connect(self.remove_assignment)
        right_layout.addWidget(remove)
        right_layout.addWidget(QLabel("Rosto selecionado"))
        self.face_selector = QComboBox()
        self.face_selector.currentIndexChanged.connect(self.show_candidates)
        right_layout.addWidget(self.face_selector)
        right_layout.addWidget(QLabel("5 formandos mais parecidos"))
        self.candidate_layout = QGridLayout()
        self.candidate_buttons: list[QToolButton] = []
        for index in range(5):
            button = QToolButton()
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(92, 92))
            button.clicked.connect(lambda checked=False, i=index: self.assign_candidate(i))
            self.candidate_buttons.append(button)
            self.candidate_layout.addWidget(button, index // 2, index % 2)
        right_layout.addLayout(self.candidate_layout)
        right_layout.addWidget(QLabel("Adicionar ou criar ID manualmente"))
        self.manual_id = QLineEdit()
        self.manual_id.setPlaceholderText("Exemplo: 085")
        right_layout.addWidget(self.manual_id)
        add = QPushButton("Adicionar ao álbum")
        add.clicked.connect(self.add_manual)
        right_layout.addWidget(add)
        create = QPushButton("Criar novo ID e procurar semelhantes")
        create.clicked.connect(self.create_and_rescan)
        right_layout.addWidget(create)
        right_layout.addStretch()
        splitter.addWidget(right)
        splitter.setSizes([280, 820, 350])
        outer.addWidget(splitter, 1)
        self.status = QLabel()
        outer.addWidget(self.status)
        self.setCentralWidget(root)
        QShortcut(QKeySequence("A"), self, activated=lambda: self.copy_neighbor(-1))
        QShortcut(QKeySequence("D"), self, activated=lambda: self.copy_neighbor(1))
        QShortcut(QKeySequence("Delete"), self, activated=self.shortcut_remove)
        for index in range(5):
            QShortcut(
                QKeySequence(str(index + 1)), self,
                activated=lambda i=index: self.shortcut_candidate(i),
            )
        self.reload()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.store.close()
        super().closeEvent(event)

    def reload(self) -> None:
        selected_id = self.current.id if self.current else None
        selected_album = (
            self.album_list.currentItem().data(Qt.ItemDataRole.UserRole)
            if self.album_list.currentItem()
            else None
        )
        self.all_photos = self.store.photos_for_review(False)
        summaries = self.store.album_summaries()
        self.album_list.blockSignals(True)
        self.album_list.clear()
        album_row = 0
        for row, (identifier, count) in enumerate(summaries):
            self.album_list.addItem(f"{identifier}  •  {count} fotos")
            self.album_list.item(row).setData(Qt.ItemDataRole.UserRole, identifier)
            if identifier == selected_album:
                album_row = row
        self.album_list.blockSignals(False)
        self.unknown_photos = self.store.photos_for_review(True)
        self.background_photos = self.store.possible_background_photos()
        self.unknown_list.blockSignals(True)
        self.unknown_list.clear()
        unknown_row = 0
        for row, photo in enumerate(self.unknown_photos):
            self.unknown_list.addItem(photo.filename)
            if photo.id == selected_id:
                unknown_row = row
        self.unknown_list.blockSignals(False)
        self.background_list.blockSignals(True)
        self.background_list.clear()
        background_row = 0
        for row, photo in enumerate(self.background_photos):
            self.background_list.addItem(f"{photo.filename} • {', '.join(photo.assignments)}")
            if photo.id == selected_id:
                background_row = row
        self.background_list.blockSignals(False)
        total, processed, unidentified = self.store.counts()
        self.tabs.setTabText(0, f"Reconhecidos ({len(summaries)})")
        self.tabs.setTabText(1, f"Sem ID ({unidentified})")
        self.tabs.setTabText(2, f"Possível fundo ({len(self.background_photos)})")
        self.status.setText(
            f"{processed}/{total} analisadas • {unidentified} sem identificação • "
            "alterações salvas automaticamente"
        )
        if summaries:
            self.album_list.setCurrentRow(min(album_row, len(summaries) - 1))
        if self.tabs.currentIndex() == 1 and self.unknown_photos:
            self.unknown_list.setCurrentRow(min(unknown_row, len(self.unknown_photos) - 1))
        elif self.tabs.currentIndex() == 2 and self.background_photos:
            self.background_list.setCurrentRow(min(background_row, len(self.background_photos) - 1))
        elif not self.all_photos:
            self.current = None
            self.image.setText("Nenhuma foto analisada")

    def select_album(self, row: int) -> None:
        if row < 0 or not self.album_list.item(row):
            return
        identifier = self.album_list.item(row).data(Qt.ItemDataRole.UserRole)
        self.album_photos = self.store.photos_for_student(identifier)
        self.album_photo_list.blockSignals(True)
        self.album_photo_list.clear()
        for photo in self.album_photos:
            self.album_photo_list.addItem(photo.filename)
        self.album_photo_list.blockSignals(False)
        if self.album_photos:
            self.album_photo_list.setCurrentRow(0)
            self.select_album_photo(0)

    def select_album_photo(self, row: int) -> None:
        if 0 <= row < len(self.album_photos):
            self.display_photo(self.album_photos[row])

    def select_unknown_photo(self, row: int) -> None:
        if 0 <= row < len(self.unknown_photos):
            self.display_photo(self.unknown_photos[row])

    def select_background_photo(self, row: int) -> None:
        if 0 <= row < len(self.background_photos):
            self.display_photo(self.background_photos[row])

    def tab_changed(self, index: int) -> None:
        if index == 0 and self.album_photos:
            self.display_photo(self.album_photos[max(0, self.album_photo_list.currentRow())])
        elif index == 1 and self.unknown_photos:
            row = max(0, self.unknown_list.currentRow())
            self.unknown_list.setCurrentRow(row)
            self.display_photo(self.unknown_photos[row])
        elif index == 2 and self.background_photos:
            row = max(0, self.background_list.currentRow())
            self.background_list.setCurrentRow(row)
            self.display_photo(self.background_photos[row])

    def display_photo(self, photo: ReviewPhoto) -> None:
        self.current = photo
        self.assignment_list.clear()
        self.assignment_list.addItems(self.current.assignments)
        self.current_faces = self.store.faces_for_photo(self.current.id)
        pixmap = QPixmap(str(self.current.path))
        self.image.set_photo(pixmap, [face["bbox"] for face in self.current_faces])
        self.face_selector.blockSignals(True)
        self.face_selector.clear()
        self.face_selector.addItems(
            [f"Rosto {index + 1}" for index in range(len(self.current_faces))]
        )
        self.face_selector.blockSignals(False)
        self.show_candidates()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.image.render_photo()

    def move(self, offset: int) -> None:
        if not self.current or not self.all_photos:
            return
        index = next(
            (row for row, photo in enumerate(self.all_photos) if photo.id == self.current.id),
            0,
        )
        target = max(0, min(len(self.all_photos) - 1, index + offset))
        self.display_photo(self.all_photos[target])

    def copy_neighbor(self, offset: int) -> None:
        if not self.current or not self.all_photos or self.manual_id.hasFocus():
            return
        index = next(
            (row for row, photo in enumerate(self.all_photos) if photo.id == self.current.id), -1
        )
        target = index + offset
        if index < 0 or not (0 <= target < len(self.all_photos)):
            return
        source = self.all_photos[target]
        identifiers = self.store.copy_assignments(source.id, self.current.id)
        if not identifiers:
            QMessageBox.information(self, "Atalho", "A foto vizinha também está sem identificação.")
            return
        self.reload()
        self.status.setText(
            f"Identificação copiada de {source.filename}: {', '.join(identifiers)}"
        )

    def shortcut_candidate(self, index: int) -> None:
        if not self.manual_id.hasFocus():
            self.assign_candidate(index)

    def shortcut_remove(self) -> None:
        if not self.manual_id.hasFocus():
            self.remove_assignment()

    def selected_face(self) -> dict | None:
        index = self.face_selector.currentIndex()
        return self.current_faces[index] if 0 <= index < len(self.current_faces) else None

    def show_candidates(self) -> None:
        self.image.selected = self.face_selector.currentIndex()
        self.image.render_photo()
        face = self.selected_face()
        candidates = face["candidates"] if face else ()
        for index, button in enumerate(self.candidate_buttons):
            if index >= len(candidates):
                button.setText("—")
                button.setIcon(QIcon())
                button.setEnabled(False)
                continue
            identifier, score = candidates[index]
            button.setText(f"{identifier}\n{score * 100:.1f}%")
            reference = self.reference_paths.get(identifier)
            button.setIcon(QIcon(str(reference)) if reference else QIcon())
            button.setEnabled(True)

    def face_selector_from_image(self, index: int) -> None:
        self.face_selector.setCurrentIndex(index)

    def assign_candidate(self, index: int) -> None:
        face = self.selected_face()
        if not self.current or not face or index >= len(face["candidates"]):
            return
        identifier, score = face["candidates"][index]
        self.store.assign(self.current.id, identifier, score, "manual-candidate")
        self.reload()

    def add_manual(self) -> None:
        if not self.current:
            return
        identifier = self.manual_id.text().strip()
        if not identifier:
            return
        if not self.store.student_exists(identifier):
            QMessageBox.warning(
                self, "ID não cadastrado", "Use “Criar novo ID” para um número ainda não cadastrado."
            )
            return
        self.store.assign(self.current.id, identifier)
        self.manual_id.clear()
        self.reload()

    def remove_assignment(self) -> None:
        if not self.current or not self.assignment_list.currentItem():
            return
        self.store.unassign(self.current.id, self.assignment_list.currentItem().text())
        self.reload()

    def create_and_rescan(self) -> None:
        if not self.current:
            return
        face = self.selected_face()
        identifier = self.manual_id.text().strip()
        if not face or not identifier:
            QMessageBox.information(self, "Novo ID", "Digite o número e selecione um rosto.")
            return
        if self.store.student_exists(identifier):
            QMessageBox.warning(self, "Novo ID", "Esse número já existe.")
            return
        self.store.add_student(identifier, self.current.path, face["embedding"])
        self.store.assign(self.current.id, identifier, 1.0, "new-id")
        automatic, suggestions = self.store.rescan_unidentified(
            identifier, face["embedding"]
        )
        self.reference_paths = dict(self.store.students())
        self.manual_id.clear()
        self.reload()
        QMessageBox.information(
            self,
            "Nova varredura",
            f"{automatic} fotos adicionadas automaticamente.\n"
            f"{len(suggestions)} correspondências médias permaneceram para revisão.",
        )

    def export(self) -> None:
        options = QMessageBox(self)
        options.setWindowTitle("Exportar contrato")
        options.setIcon(QMessageBox.Icon.Question)
        options.setText("Deseja criar também a cópia de segurança dos álbuns?")
        options.setInformativeText(
            "Ela será um espelho dos álbuns dentro de OUTROS\\CÓPIA DE SEGURANÇA. "
            "A pasta SEM IDENTIFICAÇÃO não será incluída."
        )
        backup_option = QCheckBox("Criar cópia de segurança dos álbuns")
        backup_option.setChecked(False)
        options.setCheckBox(backup_option)
        options.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        options.setDefaultButton(QMessageBox.StandardButton.Ok)
        if options.exec() != QMessageBox.StandardButton.Ok:
            return
        create_backup = backup_option.isChecked()

        destination = QFileDialog.getExistingDirectory(self, "Escolher pasta de exportação")
        if not destination:
            return
        rows = self.store.export_rows()
        dialog = QProgressDialog("Exportando...", "Cancelar", 0, len(rows), self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setCancelButton(None)

        def report(done: int, total: int, filename: str) -> None:
            dialog.setValue(done)
            dialog.setLabelText(f"Exportando {filename}")
            QApplication.processEvents()

        copied, album_counts = export_project(
            self.store,
            Path(destination),
            report,
            create_backup=create_backup,
        )
        dialog.setValue(len(rows))
        low = [(identifier, count) for identifier, count in album_counts.items() if count < 12]
        QMessageBox.information(
            self,
            "Exportação concluída",
            f"{copied} arquivos copiados.\n"
            f"{len(album_counts)} álbuns.\n"
            f"{len(low)} álbuns com menos de 12 fotos.\n"
            f"Cópia de segurança: {'criada' if create_backup else 'não solicitada'}.",
        )
        os.startfile(destination)  # type: ignore[attr-defined]


class DistributionWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FORMATCH — Distribuição 0.9.6")
        self.resize(1080, 810)
        self.thread: QThread | None = None
        self.worker: AnalysisWorker | None = None
        self.review_window: ReviewWindow | None = None

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(34, 25, 34, 25)
        layout.setSpacing(10)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("FORMATCH — Distribuição")
        title.setObjectName("title")
        titles.addWidget(title)
        subtitle = QLabel("Analisar → revisar → exportar • originais preservados • NVIDIA")
        subtitle.setObjectName("subtitle")
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        self.animation = DistributionAnimation()
        header.addWidget(self.animation)
        layout.addLayout(header)

        self.recognition = FolderRow(
            "Reconhecimento", "Fotos nítidas; o nome do arquivo é o número do formando"
        )
        self.events = FolderRow("Eventos", "Fotos brutas na ordem numérica original")
        self.project = FolderRow("Projeto", "Local onde a análise e suas correções serão salvas")
        layout.addWidget(self.recognition)
        layout.addWidget(self.events)
        layout.addWidget(self.project)
        actions = QHBoxLayout()
        self.start_button = QPushButton("INICIAR ANÁLISE")
        self.start_button.setObjectName("start")
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Parar com segurança")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        self.review_button = QPushButton("Abrir revisão")
        self.review_button.clicked.connect(self.open_review)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.review_button)
        layout.addLayout(actions)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.stats_label = QLabel("Pronto para iniciar")
        layout.addWidget(self.stats_label)
        self.current_label = QLabel("")
        self.current_label.setObjectName("subtitle")
        layout.addWidget(self.current_label)
        self.setCentralWidget(root)

    def database_path(self) -> Path | None:
        raw = self.project.path.text().strip()
        return Path(raw) / "projeto_distribuicao.sqlite3" if raw else None

    def start(self) -> None:
        raw = (
            self.recognition.path.text().strip(),
            self.events.path.text().strip(),
            self.project.path.text().strip(),
        )
        if not all(raw):
            QMessageBox.warning(self, "Pastas", "Selecione as três pastas.")
            return
        config = DistributionConfig(Path(raw[0]), Path(raw[1]), Path(raw[2]))
        database = self.database_path()
        assert database
        self.progress.setValue(0)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.review_button.setEnabled(False)
        self.animation.set_running(True)
        self.thread = QThread(self)
        self.worker = AnalysisWorker(config, database)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progressed.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.finished.connect(self.thread.quit)
        self.thread.start()

    def stop(self) -> None:
        if self.worker:
            self.worker.stop()
            self.stop_button.setEnabled(False)
            self.stats_label.setText("Interrompendo após a foto atual...")

    def on_progress(
        self, done: int, total: int, unidentified: int, speed: float, remaining: float, line: str
    ) -> None:
        self.progress.setValue(round(done * 100 / total) if total else 100)
        self.stats_label.setText(
            f"{done}/{total} • tempo restante estimado {duration(remaining)} • "
            f"{unidentified} sem identificação"
        )
        self.current_label.setText(line)

    def on_finished(self, database: str, gpu: bool, error: str) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.review_button.setEnabled(True)
        self.animation.set_complete()
        if error:
            QMessageBox.warning(self, "Análise", error)
            return
        device = "RTX/CUDA" if gpu else "processador"
        self.stats_label.setText(f"Análise concluída com {device}. Pronta para revisão.")
        self.open_review()

    def open_review(self) -> None:
        database = self.database_path()
        if not database or not database.exists():
            QMessageBox.information(self, "Revisão", "Ainda não existe uma análise neste projeto.")
            return
        self.review_window = ReviewWindow(database)
        self.review_window.show()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FORMATCH 0.9.6")
        self.setFixedSize(760, 480)
        self.module_window: QMainWindow | None = None
        self.update_info: UpdateInfo | None = None
        self.update_thread: QThread | None = None
        self.update_worker: QObject | None = None
        self.update_progress: QProgressDialog | None = None

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(55, 45, 55, 45)
        layout.setSpacing(18)

        title = QLabel("FORMATCH")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        subtitle = QLabel("Do reconhecimento ao descarte, pronto para diagramação.")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("font-size: 13pt; color: #aebbb3;")
        layout.addWidget(subtitle)
        layout.addStretch()

        distribute = QPushButton("DISTRIBUIR")
        distribute.setObjectName("start")
        distribute.setMinimumHeight(78)
        distribute.setToolTip("Reconhecer, revisar e exportar as fotos dos formandos")
        distribute.clicked.connect(self.open_distribution)
        layout.addWidget(distribute)

        discard = QPushButton("DESCARTAR")
        discard.setMinimumHeight(78)
        discard.setStyleSheet("font-size: 13pt; padding: 13px 26px;")
        discard.setToolTip("Selecionar e mover fotos repetidas de um álbum")
        discard.clicked.connect(self.open_discard)
        layout.addWidget(discard)
        layout.addStretch()
        self.setCentralWidget(root)
        QTimer.singleShot(1200, self.check_for_updates)

    def check_for_updates(self) -> None:
        manifest_url = configured_manifest_url()
        if not manifest_url:
            return
        self.update_thread = QThread(self)
        worker = UpdateCheckWorker(manifest_url)
        self.update_worker = worker
        worker.moveToThread(self.update_thread)
        self.update_thread.started.connect(worker.run)
        worker.finished.connect(self.update_check_finished)
        worker.finished.connect(self.update_thread.quit)
        self.update_thread.start()

    def update_check_finished(self, info: object, error: str) -> None:
        if error or not isinstance(info, UpdateInfo):
            return
        self.update_info = info
        message = QMessageBox(self)
        message.setWindowTitle("Atualização do FORMATCH")
        message.setIcon(QMessageBox.Icon.Information)
        message.setText(f"A versão {info.version} está disponível.")
        message.setInformativeText(
            (info.notes.strip() + "\n\n" if info.notes.strip() else "")
            + "Deseja atualizar agora? O FORMATCH será fechado e aberto novamente."
        )
        now = message.addButton("ATUALIZAR AGORA", QMessageBox.ButtonRole.AcceptRole)
        message.addButton("MAIS TARDE", QMessageBox.ButtonRole.RejectRole)
        message.exec()
        if message.clickedButton() is now:
            self.download_available_update()

    def download_available_update(self) -> None:
        if not self.update_info:
            return
        self.update_progress = QProgressDialog(
            "Baixando atualização...", "", 0, 100, self
        )
        self.update_progress.setWindowTitle("Atualização do FORMATCH")
        self.update_progress.setCancelButton(None)
        self.update_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.update_progress.setValue(0)
        self.update_thread = QThread(self)
        worker = UpdateDownloadWorker(self.update_info)
        self.update_worker = worker
        worker.moveToThread(self.update_thread)
        self.update_thread.started.connect(worker.run)
        worker.progressed.connect(self.update_download_progressed)
        worker.finished.connect(self.update_download_finished)
        worker.finished.connect(self.update_thread.quit)
        self.update_thread.start()

    def update_download_progressed(self, done: int, total: int) -> None:
        if self.update_progress:
            self.update_progress.setValue(round(done * 100 / total) if total else 0)

    def update_download_finished(self, package: object, error: str) -> None:
        if self.update_progress:
            self.update_progress.close()
        if error or not isinstance(package, Path) or not self.update_info:
            QMessageBox.warning(
                self,
                "Atualização",
                error or "Não foi possível baixar a atualização.",
            )
            return
        # O sinal finished chega antes de a QThread processar quit(). Encerre-a
        # agora para que o processo realmente termine e libere os arquivos.
        if self.update_thread and self.update_thread.isRunning():
            self.update_thread.quit()
            self.update_thread.wait(5000)
        try:
            launch_update(package, self.update_info.version)
        except Exception as exc:
            QMessageBox.warning(self, "Atualização", str(exc))
            return
        QApplication.closeAllWindows()
        os._exit(0)

    def open_distribution(self) -> None:
        self._open_module(DistributionWindow())

    def open_discard(self) -> None:
        self._open_module(DiscardWindow())

    def _open_module(self, window: QMainWindow) -> None:
        self.module_window = window
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.destroyed.connect(self._module_closed)
        self.hide()
        window.show()

    def _module_closed(self, *args: object) -> None:
        self.module_window = None
        self.show()
        self.raise_()
        self.activateWindow()


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#090d0b"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f2f5f3"))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()
