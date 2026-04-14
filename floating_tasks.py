"""
FloatTask - A floating task manager desktop app.
Single circular button that stays on top of all windows.
Click to open/close a task management popup panel.
"""

import sys
import json
import math
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QFrame,
    QCheckBox, QGraphicsDropShadowEffect, QSizePolicy
)
from PyQt6.QtCore import (
    Qt, QPoint, QTimer, pyqtSignal
)
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QPen, QFont,
    QRadialGradient, QCursor
)


# --- Data file path ---
DATA_FILE = Path.home() / "floating_tasks_data.json"


def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"tasks": [], "position": None}
            if "tasks" not in data:
                data["tasks"] = []
            return data
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return {"tasks": [], "position": None}


def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


# ============================================================
# TaskItem
# ============================================================
class TaskItem(QWidget):
    toggled = pyqtSignal()
    deleted = pyqtSignal(object)

    def __init__(self, text, done=False, parent=None):
        super().__init__(parent)
        self.task_text = text
        self.done = done
        self.setFixedHeight(44)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 10, 6)
        layout.setSpacing(10)

        # Custom circle checkbox
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(self.done)
        self.checkbox.setFixedSize(22, 22)
        self.checkbox.stateChanged.connect(self._on_toggle)
        self.checkbox.setStyleSheet("""
            QCheckBox { background: transparent; }
            QCheckBox::indicator {
                width: 18px; height: 18px;
                border: 2px solid #4a4a6a;
                border-radius: 9px;
                background: transparent;
            }
            QCheckBox::indicator:hover {
                border-color: #a78bfa;
            }
            QCheckBox::indicator:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #a78bfa, stop:1 #6366f1);
                border-color: #a78bfa;
                image: none;
            }
        """)
        layout.addWidget(self.checkbox)

        # Task label
        self.label = QLabel(self.task_text)
        self.label.setWordWrap(True)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.label)

        # Delete button
        self.del_btn = QPushButton("\u2212")
        self.del_btn.setFixedSize(26, 26)
        self.del_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.del_btn.clicked.connect(lambda: self.deleted.emit(self))
        self.del_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #ef4444;
                border: 1px solid transparent;
                font-size: 16px;
                font-weight: bold;
                border-radius: 13px;
            }
            QPushButton:hover {
                background: rgba(239, 68, 68, 0.12);
                border: 1px solid rgba(239, 68, 68, 0.3);
            }
        """)
        self.del_btn.setVisible(False)
        layout.addWidget(self.del_btn)

    def _apply_style(self):
        if self.done:
            self.label.setStyleSheet(
                "color: #3a3a50; font-size: 13px; font-family: 'Segoe UI';"
                "text-decoration: line-through; background: transparent;"
            )
        else:
            self.label.setStyleSheet(
                "color: #d4d4e8; font-size: 13px; font-family: 'Segoe UI';"
                "background: transparent;"
            )

    def _on_toggle(self, state):
        self.done = bool(state)
        self._apply_style()
        self.toggled.emit()

    def enterEvent(self, event):
        self.del_btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.del_btn.setVisible(False)
        super().leaveEvent(event)

    def to_dict(self):
        return {"text": self.task_text, "done": self.done}

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 1, -2, -1)

        if self.done:
            painter.setBrush(QColor(20, 20, 35, 120))
            painter.setPen(QPen(QColor(50, 50, 70, 60), 1))
        elif self.underMouse():
            painter.setBrush(QColor(167, 139, 250, 18))
            painter.setPen(QPen(QColor(167, 139, 250, 50), 1))
        else:
            painter.setBrush(QColor(255, 255, 255, 8))
            painter.setPen(QPen(QColor(255, 255, 255, 15), 1))

        painter.drawRoundedRect(rect, 10, 10)
        painter.end()


# ============================================================
# PopupPanel
# ============================================================
class PopupPanel(QWidget):
    closed = pyqtSignal()
    data_changed = pyqtSignal()

    PANEL_W = 340
    PANEL_H = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.PANEL_W, self.PANEL_H)
        self.task_items = []
        self._build_ui()

    def paintEvent(self, event):
        """Draw dark rounded background manually to avoid white leaks."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Shadow layers
        for i in range(5):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 12 - i * 2), 1))
            painter.drawRoundedRect(self.rect().adjusted(i, i, -i, -i), 20, 20)

        # Main background
        painter.setPen(QPen(QColor(167, 139, 250, 40), 1))
        painter.setBrush(QColor(13, 13, 25, 248))
        painter.drawRoundedRect(self.rect().adjusted(5, 5, -5, -5), 18, 18)

        # Top accent line
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(167, 139, 250, 25))
        painter.drawRoundedRect(self.rect().adjusted(20, 6, -20, -(self.PANEL_H - 9)), 2, 2)

        painter.end()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 18, 20, 16)
        main_layout.setSpacing(12)

        # --- Header ---
        header = QHBoxLayout()
        header.setSpacing(8)

        # App icon - small purple dot
        icon_dot = QLabel()
        icon_dot.setFixedSize(10, 10)
        icon_dot.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #a78bfa, stop:1 #6366f1);
            border-radius: 5px;
        """)
        header.addWidget(icon_dot)

        title = QLabel("FloatTask")
        title.setStyleSheet(
            "color: #e2e0f0; font-size: 15px; font-weight: bold;"
            "font-family: 'Segoe UI'; letter-spacing: 0.5px;"
        )
        header.addWidget(title)
        header.addStretch()

        close_btn = QPushButton("\u00d7")
        close_btn.setFixedSize(30, 30)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self._close)
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.04);
                color: #555;
                border: none;
                border-radius: 15px;
                font-size: 18px;
                font-family: 'Segoe UI';
            }
            QPushButton:hover {
                background: rgba(239, 68, 68, 0.15);
                color: #ef4444;
            }
        """)
        header.addWidget(close_btn)
        main_layout.addLayout(header)

        # --- Separator ---
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(167, 139, 250, 0.1);")
        main_layout.addWidget(sep)

        # --- Input row ---
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("What needs to be done?")
        self.input_field.setFixedHeight(40)
        self.input_field.returnPressed.connect(self._add_task)
        self.input_field.setStyleSheet("""
            QLineEdit {
                background: rgba(255,255,255,0.04);
                color: #d4d4e8;
                border: 1px solid rgba(167,139,250,0.12);
                border-radius: 12px;
                padding: 0 16px;
                font-size: 13px;
                font-family: 'Segoe UI';
                selection-background-color: #6366f1;
            }
            QLineEdit:focus {
                border-color: rgba(167,139,250,0.4);
                background: rgba(167,139,250,0.06);
            }
            QLineEdit::placeholder {
                color: #4a4a60;
            }
        """)
        input_row.addWidget(self.input_field)

        add_btn = QPushButton("+")
        add_btn.setFixedSize(40, 40)
        add_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        add_btn.clicked.connect(self._add_task)
        add_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #a78bfa, stop:1 #6366f1);
                color: white;
                border: none;
                border-radius: 12px;
                font-size: 22px;
                font-weight: bold;
                font-family: 'Segoe UI';
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #b89dff, stop:1 #7577ff);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #9678e6, stop:1 #5558d9);
            }
        """)
        input_row.addWidget(add_btn)
        main_layout.addLayout(input_row)

        # --- Scrollable task list ---
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QWidget {
                background: transparent;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 4px;
                margin: 4px 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(167,139,250,0.25);
                border-radius: 2px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(167,139,250,0.4);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
                height: 0;
            }
        """)

        self.task_container = QWidget()
        self.task_layout = QVBoxLayout(self.task_container)
        self.task_layout.setContentsMargins(0, 4, 0, 4)
        self.task_layout.setSpacing(4)
        self.task_layout.addStretch()

        self.scroll.setWidget(self.task_container)
        main_layout.addWidget(self.scroll)

        # --- Footer separator ---
        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet("background: rgba(167, 139, 250, 0.08);")
        main_layout.addWidget(sep2)

        # --- Footer ---
        footer = QHBoxLayout()
        footer.setContentsMargins(4, 2, 4, 0)
        self.status_label = QLabel("0 pending \u00b7 0 done")
        self.status_label.setStyleSheet(
            "color: #4a4a60; font-size: 11px; font-family: 'Segoe UI';"
        )
        footer.addWidget(self.status_label)
        footer.addStretch()

        self.clear_btn = QPushButton("Clear done")
        self.clear_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.clear_btn.clicked.connect(self._clear_done)
        self.clear_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #6366f1;
                border: none;
                font-size: 11px;
                font-family: 'Segoe UI';
                padding: 4px 10px;
                border-radius: 8px;
            }
            QPushButton:hover {
                background: rgba(99,102,241,0.1);
                color: #a78bfa;
            }
        """)
        footer.addWidget(self.clear_btn)
        main_layout.addLayout(footer)

    def load_tasks(self, tasks):
        for t in tasks:
            self._create_task_widget(t["text"], t.get("done", False))
        self._update_footer()

    def _add_task(self):
        text = self.input_field.text().strip()
        if not text:
            return
        self.input_field.clear()
        self._create_task_widget(text, False)
        self._update_footer()
        self.data_changed.emit()

    def _create_task_widget(self, text, done):
        item = TaskItem(text, done)
        item.toggled.connect(self._on_task_changed)
        item.deleted.connect(self._delete_task)
        self.task_items.append(item)
        self.task_layout.insertWidget(self.task_layout.count() - 1, item)
        self._sort_tasks()

    def _on_task_changed(self):
        self._sort_tasks()
        self._update_footer()
        self.data_changed.emit()

    def _delete_task(self, item):
        self.task_items.remove(item)
        self.task_layout.removeWidget(item)
        item.deleteLater()
        self._update_footer()
        self.data_changed.emit()

    def _clear_done(self):
        done_items = [i for i in self.task_items if i.done]
        for item in done_items:
            self.task_items.remove(item)
            self.task_layout.removeWidget(item)
            item.deleteLater()
        self._update_footer()
        self.data_changed.emit()

    def _sort_tasks(self):
        for item in self.task_items:
            self.task_layout.removeWidget(item)
        pending = [i for i in self.task_items if not i.done]
        done = [i for i in self.task_items if i.done]
        for i, item in enumerate(pending + done):
            self.task_layout.insertWidget(i, item)

    def _update_footer(self):
        pending = sum(1 for i in self.task_items if not i.done)
        done = sum(1 for i in self.task_items if i.done)
        self.status_label.setText(f"{pending} pending \u00b7 {done} done")

    def get_tasks_data(self):
        return [i.to_dict() for i in self.task_items]

    def get_pending_count(self):
        return sum(1 for i in self.task_items if not i.done)

    def _close(self):
        self.closed.emit()

    def show_at(self, circle_pos, circle_size):
        screen = QApplication.primaryScreen().geometry()
        cx, cy = circle_pos.x(), circle_pos.y()

        if cx + circle_size + 10 + self.width() <= screen.width():
            x = cx + circle_size + 10
        else:
            x = cx - self.width() - 10

        y = cy - 20
        if y + self.height() > screen.height():
            y = screen.height() - self.height() - 10
        if y < 0:
            y = 10

        self.move(x, y)
        self.show()
        self.input_field.setFocus()


# ============================================================
# FloatingCircle
# ============================================================
class FloatingCircle(QWidget):
    CIRCLE_SIZE = 56

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.CIRCLE_SIZE + 8, self.CIRCLE_SIZE + 8)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self._dragging = False
        self._drag_pos = QPoint()
        self._was_dragged = False
        self._pending_count = 0
        self._hover = False

        # Panel
        self.panel = PopupPanel()
        self.panel.closed.connect(self._close_panel)
        self.panel.data_changed.connect(self._on_data_changed)
        self._panel_visible = False

        # Load saved data
        data = load_data()
        self.panel.load_tasks(data.get("tasks", []))
        self._pending_count = self.panel.get_pending_count()

        # Restore position or default to top-right
        pos = data.get("position")
        if pos and isinstance(pos, list) and len(pos) == 2:
            self.move(pos[0], pos[1])
        else:
            screen = QApplication.primaryScreen().geometry()
            self.move(screen.width() - self.CIRCLE_SIZE - 40, 30)

        # Glow animation
        self._glow_phase = 0.0
        self._glow_timer = QTimer()
        self._glow_timer.timeout.connect(self._tick_glow)
        self._glow_timer.start(40)

    def _tick_glow(self):
        self._glow_phase += 0.04
        if self._glow_phase > 6.283:
            self._glow_phase -= 6.283
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self.width() / 2
        cy = self.height() / 2
        r = self.CIRCLE_SIZE / 2

        # Soft outer glow (breathing)
        glow_strength = 0.5 + 0.5 * math.sin(self._glow_phase)
        for i in range(3):
            alpha = int((15 + 10 * glow_strength) * (3 - i) / 3)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(167, 139, 250, alpha))
            painter.drawEllipse(int(cx - r - 2 - i), int(cy - r - 2 - i),
                                int((r + 2 + i) * 2), int((r + 2 + i) * 2))

        # Main circle gradient
        gradient = QRadialGradient(cx - 4, cy - 6, r * 1.2)
        if self._hover:
            gradient.setColorAt(0, QColor(55, 40, 110, 250))
            gradient.setColorAt(0.6, QColor(25, 22, 50, 252))
            gradient.setColorAt(1, QColor(15, 12, 35, 255))
        else:
            gradient.setColorAt(0, QColor(40, 30, 85, 245))
            gradient.setColorAt(0.6, QColor(18, 16, 38, 250))
            gradient.setColorAt(1, QColor(10, 8, 25, 252))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

        # Border ring
        ring_alpha = int(80 + 40 * glow_strength)
        if self._hover:
            ring_alpha = min(ring_alpha + 40, 200)
        painter.setPen(QPen(QColor(167, 139, 250, ring_alpha), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(int(cx - r + 1), int(cy - r + 1),
                            int((r - 1) * 2), int((r - 1) * 2))

        # Center content
        if self._pending_count > 0:
            # Badge count
            painter.setPen(QColor(220, 210, 255))
            font = QFont("Segoe UI", 17, QFont.Weight.Bold)
            painter.setFont(font)
            from PyQt6.QtCore import QRectF
            painter.drawText(QRectF(0, 0, self.width(), self.height()),
                             Qt.AlignmentFlag.AlignCenter, str(self._pending_count))
        else:
            # Checkmark
            painter.setPen(QPen(QColor(99, 102, 241), 2.5))
            painter.drawLine(int(cx - 6), int(cy + 1), int(cx - 1), int(cy + 6))
            painter.drawLine(int(cx - 1), int(cy + 6), int(cx + 8), int(cy - 5))

        painter.end()

    # --- Mouse events ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.pos()
            self._was_dragged = False

    def mouseMoveEvent(self, event):
        if self._dragging:
            new_pos = event.globalPosition().toPoint() - self._drag_pos
            self.move(new_pos)
            self._was_dragged = True

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            if self._was_dragged:
                self._save_position()
            else:
                self._toggle_panel()

    def _toggle_panel(self):
        if self._panel_visible:
            self._close_panel()
        else:
            self.panel.show_at(self.pos(), self.CIRCLE_SIZE)
            self._panel_visible = True

    def _close_panel(self):
        self.panel.hide()
        self._panel_visible = False

    def _on_data_changed(self):
        self._pending_count = self.panel.get_pending_count()
        self.update()
        self._save_all()

    def _save_position(self):
        self._save_all()

    def _save_all(self):
        data = {
            "tasks": self.panel.get_tasks_data(),
            "position": [self.pos().x(), self.pos().y()]
        }
        save_data(data)


# ============================================================
# Main
# ============================================================
def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    circle = FloatingCircle()
    circle.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
