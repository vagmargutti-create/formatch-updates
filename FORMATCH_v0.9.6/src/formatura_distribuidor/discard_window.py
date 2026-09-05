from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time

from PySide6.QtCore import QObject, QSize, QThread, Signal, Qt
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .discard_engine import DuplicateGroup, apply_discard, find_groups, undo_discard
from .models import IMAGE_EXTENSIONS
from .project_store import natural_key


class DiscardWorker(QObject):
    progressed = Signal(int, int, str)
    finished = Signal(object, str)

    def __init__(self, distributed_dir: Path) -> None:
        super().__init__()
        self.distributed_dir = distributed_dir

    def run(self) -> None:
        try:
            groups = find_groups(
                self.distributed_dir,
                lambda done, total, name: self.progressed.emit(done, total, name),
            )
            self.finished.emit(groups, "")
        except Exception as exc:
            self.finished.emit([], str(exc))


class DiscardWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FORMATCH — Descarte 0.9.6")
        self.resize(1050, 760)
        self.groups: list[DuplicateGroup] = []
        self.all_photos: list[Path] = []
        self.discard_items: list[tuple[QListWidgetItem, int, Path]] = []
        self.preview_items: list[QListWidgetItem] = []
        self.preview_pixmap = QPixmap()
        self.thread: QThread | None = None
        self.worker: DiscardWorker | None = None
        self.last_manifest: Path | None = None
        self.analysis_started = 0.0

        root = QWidget()
        layout = QVBoxLayout(root)
        title = QLabel("Seleção e descarte")
        title.setObjectName("title")
        layout.addWidget(title)
        helper = QLabel(
            "Localiza sequências iguais ou muito semelhantes, mantém a foto de melhor "
            "qualidade e move as outras. Nenhuma foto será apagada e nenhuma cópia de "
            "segurança será criada."
        )
        helper.setWordWrap(True)
        helper.setObjectName("subtitle")
        layout.addWidget(helper)

        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText("Selecione a pasta de um formando, por exemplo ALBUNS\\001...")
        choose = QPushButton("Selecionar pasta")
        choose.clicked.connect(self.choose_folder)
        row.addWidget(self.path, 1)
        row.addWidget(choose)
        layout.addLayout(row)

        buttons = QHBoxLayout()
        self.analyze_button = QPushButton("ANALISAR REPETIDAS")
        self.analyze_button.setObjectName("start")
        self.analyze_button.clicked.connect(self.analyze)
        self.apply_button = QPushButton("MOVER SELECIONADAS PARA DESCARTE")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply)
        self.undo_button = QPushButton("Desfazer último descarte")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self.undo)
        buttons.addWidget(self.analyze_button)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.undo_button)
        layout.addLayout(buttons)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.status = QLabel("Pronto para analisar")
        layout.addWidget(self.status)
        layout.addWidget(QLabel("Resultado separado por decisão — confira antes de mover"))
        splitter = QSplitter()
        self.tabs = QTabWidget()
        self.keep_results = QListWidget()
        self.discard_results = QListWidget()
        self.suggestion_results = QListWidget()
        self.result_lists = (
            self.keep_results,
            self.discard_results,
            self.suggestion_results,
        )
        for result_list in self.result_lists:
            result_list.currentItemChanged.connect(self.show_preview)
            result_list.itemChanged.connect(self.preview_decision_changed)
        self.tabs.addTab(self.keep_results, "MANTER (0)")
        self.tabs.addTab(self.discard_results, "DESCARTE (0)")
        self.tabs.addTab(self.suggestion_results, "SUGESTÕES (0)")
        self.tabs.currentChanged.connect(self.tab_changed)
        splitter.addWidget(self.tabs)
        preview_box = QWidget()
        preview_layout = QVBoxLayout(preview_box)
        self.preview = QLabel("Clique em uma fotografia da lista")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(QSize(560, 500))
        self.preview.setStyleSheet("background:#050806;border:4px solid #26352b;")
        preview_layout.addWidget(self.preview, 1)
        self.preview_name = QLabel("")
        self.preview_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.preview_name)
        navigation = QHBoxLayout()
        previous = QPushButton("◀ Foto anterior")
        previous.clicked.connect(lambda: self.move_preview(-1))
        following = QPushButton("Próxima foto ▶")
        following.clicked.connect(lambda: self.move_preview(1))
        navigation.addWidget(previous)
        navigation.addWidget(following)
        preview_layout.addLayout(navigation)
        splitter.addWidget(preview_box)
        splitter.setSizes([430, 610])
        layout.addWidget(splitter, 1)
        self.setCentralWidget(root)
        QShortcut(QKeySequence("Left"), self, activated=lambda: self.move_preview(-1))
        QShortcut(QKeySequence("Right"), self, activated=lambda: self.move_preview(1))

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Selecionar a pasta individual do formando", self.path.text()
        )
        if folder:
            self.path.setText(folder)

    def analyze(self) -> None:
        distributed = Path(self.path.text().strip())
        if not distributed.is_dir():
            QMessageBox.warning(self, "Pasta", "Selecione uma pasta de álbum válida.")
            return
        self.groups = []
        self.all_photos = sorted(
            (
                photo
                for photo in distributed.iterdir()
                if photo.is_file() and photo.suffix.lower() in IMAGE_EXTENSIONS
            ),
            key=lambda photo: natural_key(photo.name),
        )
        self.discard_items = []
        self.preview_items = []
        for result_list in self.result_lists:
            result_list.clear()
        self.progress.setValue(0)
        self.status.setText("Analisando qualidade e semelhança...")
        self.analysis_started = time.monotonic()
        self.analyze_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.thread = QThread(self)
        self.worker = DiscardWorker(distributed)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progressed.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.finished.connect(self.thread.quit)
        self.thread.start()

    def on_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setValue(round(done * 100 / total) if total else 100)
        elapsed = max(time.monotonic() - self.analysis_started, 0.001)
        speed = done / elapsed
        remaining = (total - done) / speed if done >= 5 and speed > 0 else -1.0
        self.status.setText(
            f"{done}/{total} analisadas • tempo restante estimado {self._duration(remaining)}"
        )

    @staticmethod
    def _duration(seconds: float) -> str:
        if seconds < 0:
            return "calculando..."
        seconds = int(seconds)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def on_finished(self, groups: object, error: str) -> None:
        self.analyze_button.setEnabled(True)
        if error:
            self.status.setText("A análise não foi concluída")
            QMessageBox.warning(self, "Descarte", error)
            return
        self.groups = list(groups)  # type: ignore[arg-type]
        total_discard = sum(
            len(group.discard) for group in self.groups if group.category == "discard"
        )
        suggestion_count = sum(
            len(group.discard) for group in self.groups if group.category == "suggestion"
        )
        recommended_discards = {
            photo for group in self.groups for photo in group.discard
        }
        keep_paths = [photo for photo in self.all_photos if photo not in recommended_discards]
        for photo in keep_paths:
            item = QListWidgetItem(f"MANTER • {photo.name}")
            item.setData(Qt.ItemDataRole.UserRole, str(photo))
            item.setData(Qt.ItemDataRole.UserRole + 1, "keep")
            self.keep_results.addItem(item)
            self.preview_items.append(item)
        for group_index, group in enumerate(self.groups):
            target_list = (
                self.discard_results
                if group.category == "discard"
                else self.suggestion_results
            )
            keep_prefix = "REFERÊNCIA MANTIDA" if group.category == "discard" else "RECOMENDAÇÃO: MANTER"
            keep_item = QListWidgetItem(f"{keep_prefix} • {group.keep.name}")
            keep_item.setData(Qt.ItemDataRole.UserRole, str(group.keep))
            keep_item.setData(Qt.ItemDataRole.UserRole + 1, "keep")
            target_list.addItem(keep_item)
            self.preview_items.append(keep_item)
            notes = dict(group.notes)
            for photo in group.discard:
                prefix = "DESCARTAR" if group.category == "discard" else "SUGESTÃO"
                item = QListWidgetItem(
                    f"{prefix} • {photo.name} • {notes.get(photo.name, 'qualidade inferior')}"
                )
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked
                    if group.category == "discard"
                    else Qt.CheckState.Unchecked
                )
                item.setData(Qt.ItemDataRole.UserRole, str(photo))
                item.setData(Qt.ItemDataRole.UserRole + 1, group.category)
                target_list.addItem(item)
                self.discard_items.append((item, group_index, photo))
                self.preview_items.append(item)
        self.tabs.setTabText(0, f"MANTER ({len(keep_paths)})")
        self.tabs.setTabText(1, f"DESCARTE ({total_discard})")
        self.tabs.setTabText(2, f"SUGESTÕES ({suggestion_count})")
        self.progress.setValue(100)
        self.status.setText(
            f"{total_discard} para descarte • {suggestion_count} sugestões para conferir"
        )
        self.apply_button.setEnabled(total_discard > 0)
        if self.preview_items:
            self.keep_results.setCurrentItem(self.preview_items[0])

    def show_preview(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None = None
    ) -> None:
        if not current:
            return
        raw_path = current.data(Qt.ItemDataRole.UserRole)
        if not raw_path:
            return
        self.preview_pixmap = QPixmap(str(raw_path))
        self.render_preview(current)

    def preview_decision_changed(self, item: QListWidgetItem) -> None:
        self._update_apply_button()
        if item is self._current_list().currentItem():
            self.render_preview(item)

    def _update_apply_button(self) -> None:
        self.apply_button.setEnabled(
            any(item.checkState() == Qt.CheckState.Checked for item, _, _ in self.discard_items)
        )

    def tab_changed(self, _index: int) -> None:
        current_list = self._current_list()
        if current_list.currentItem() is None and current_list.count():
            current_list.setCurrentRow(0)
        self.show_preview(current_list.currentItem())

    def _current_list(self) -> QListWidget:
        current = self.tabs.currentWidget()
        return current if isinstance(current, QListWidget) else self.keep_results

    def render_preview(self, item: QListWidgetItem | None = None) -> None:
        item = item or self._current_list().currentItem()
        if not item or self.preview_pixmap.isNull():
            return
        self.preview.setPixmap(
            self.preview_pixmap.scaled(
                self.preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        role = item.data(Qt.ItemDataRole.UserRole + 1)
        discard = role in {"discard", "suggestion"} and item.checkState() == Qt.CheckState.Checked
        color = "#e43d3d" if discard else "#25df58"
        decision = "DESCARTAR" if discard else "MANTER"
        path = Path(str(item.data(Qt.ItemDataRole.UserRole)))
        self.preview.setStyleSheet(f"background:#050806;border:4px solid {color};")
        self.preview_name.setText(f"{decision} • {path.name}")
        self.preview_name.setStyleSheet(f"font-size:14pt;font-weight:700;color:{color};")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.render_preview()

    def move_preview(self, offset: int) -> None:
        if not self.preview_items:
            return
        current = self._current_list().currentItem()
        try:
            index = self.preview_items.index(current) if current else 0
        except ValueError:
            index = 0
        index = max(0, min(len(self.preview_items) - 1, index + offset))
        target = self.preview_items[index]
        owner = target.listWidget()
        if owner is self.keep_results:
            self.tabs.setCurrentIndex(0)
        elif owner is self.discard_results:
            self.tabs.setCurrentIndex(1)
        else:
            self.tabs.setCurrentIndex(2)
        owner.setCurrentItem(target)

    def apply(self) -> None:
        if not self.groups:
            return
        selected_by_group: dict[int, list[Path]] = {}
        for item, group_index, photo in self.discard_items:
            if item.checkState() == Qt.CheckState.Checked:  # type: ignore[attr-defined]
                selected_by_group.setdefault(group_index, []).append(photo)
        selected_groups = [
            replace(group, discard=tuple(selected_by_group.get(index, [])))
            for index, group in enumerate(self.groups)
            if selected_by_group.get(index)
        ]
        count = sum(len(group.discard) for group in selected_groups)
        if not count:
            QMessageBox.information(self, "Descarte", "Nenhuma foto está marcada para mover.")
            return
        answer = QMessageBox.question(
            self,
            "Confirmar descarte",
            f"Mover {count} fotos repetidas para OUTROS\\DESCARTE POR ÁLBUM?\n\n"
            "As melhores permanecerão nos álbuns. Esta ação poderá ser desfeita.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        album_dir = Path(self.path.text().strip())
        # Estrutura habitual: CONTRATO/ALBUNS/001. O descarte fica em CONTRATO/OUTROS.
        contract_root = (
            album_dir.parent.parent
            if album_dir.parent.name.upper() in {"ALBUNS", "DISTRIBUÍDOS", "DISTRIBUIDOS"}
            else album_dir.parent
        )
        try:
            moved, manifest = apply_discard(selected_groups, contract_root)
        except Exception as exc:
            QMessageBox.warning(self, "Descarte", str(exc))
            return
        self.last_manifest = manifest
        self.apply_button.setEnabled(False)
        self.undo_button.setEnabled(True)
        self.status.setText(f"Concluído: {moved} fotos movidas; nenhuma foto apagada.")
        QMessageBox.information(
            self,
            "Descarte concluído",
            f"{moved} fotos foram movidas.\nA melhor de cada sequência ficou no álbum.",
        )

    def undo(self) -> None:
        if not self.last_manifest:
            return
        try:
            restored = undo_discard(self.last_manifest)
        except Exception as exc:
            QMessageBox.warning(self, "Desfazer", str(exc))
            return
        self.undo_button.setEnabled(False)
        self.status.setText(f"Desfeito: {restored} fotos voltaram aos álbuns.")
        QMessageBox.information(self, "Desfeito", f"{restored} fotos foram restauradas.")
