"""
FloatTask - A floating task manager desktop app.
Single circular button that stays on top of all windows.
Click to open/close a task management popup panel.
Supports project hierarchy - tasks organized under projects.
"""

import sys
import json
import math
import tempfile
import msvcrt
import sqlite3
import ctypes
import ctypes.wintypes
import base64
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QFrame,
    QCheckBox, QGraphicsDropShadowEffect, QSizePolicy, QTextEdit,
    QStackedWidget
)
from PyQt6.QtCore import (
    Qt, QPoint, QTimer, pyqtSignal
)
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QPen, QFont,
    QRadialGradient, QCursor
)


# --- Data file paths ---
DATA_FILE = Path.home() / "floating_tasks_data.json"
ONEDRIVE_DIR = Path.home() / "OneDrive" / "Documents"
DB_FILE = ONEDRIVE_DIR / "floattask.db" if ONEDRIVE_DIR.exists() else Path.home() / "floattask.db"


# --- Windows DPAPI encryption ---
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]

def _dpapi_encrypt(plaintext: str) -> str:
    data = plaintext.encode("utf-8")
    blob_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
    blob_out = DATA_BLOB()
    if ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return base64.b64encode(encrypted).decode("ascii")
    return plaintext

def _dpapi_decrypt(encrypted_b64: str) -> str:
    try:
        data = base64.b64decode(encrypted_b64)
    except Exception:
        return encrypted_b64
    blob_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
    blob_out = DATA_BLOB()
    if ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return decrypted.decode("utf-8")
    return encrypted_b64


# ============================================================
# SQLite Database Layer
# ============================================================
class FloatTaskDB:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_FILE))
        self.conn.execute("PRAGMA journal_mode=DELETE")
        self._create_tables()
        self._migrate_json_if_needed()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                deleted_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                completed_at TEXT,
                deleted_at TEXT,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
            CREATE TABLE IF NOT EXISTS task_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                project_id INTEGER,
                action TEXT NOT NULL,
                detail TEXT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL,
                username TEXT NOT NULL,
                password_enc TEXT NOT NULL,
                url TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                deleted_at TEXT
            );
        """)
        self.conn.commit()

    def _migrate_json_if_needed(self):
        cursor = self.conn.execute("SELECT COUNT(*) FROM projects WHERE deleted_at IS NULL")
        if cursor.fetchone()[0] > 0:
            return
        if not DATA_FILE.exists():
            return
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            if "tasks" in data and "projects" not in data:
                data = {"projects": [{"name": "General", "tasks": data["tasks"]}],
                        "position": data.get("position")}
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for proj in data.get("projects", []):
                cur = self.conn.execute(
                    "INSERT INTO projects (name, created_at) VALUES (?, ?)",
                    (proj["name"], now))
                pid = cur.lastrowid
                for task in proj.get("tasks", []):
                    done = 1 if task.get("done", False) else 0
                    comp = now if done else None
                    self.conn.execute(
                        "INSERT INTO tasks (project_id, text, done, created_at, completed_at) VALUES (?, ?, ?, ?, ?)",
                        (pid, task["text"], done, now, comp))
            if data.get("position"):
                self.conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("position", json.dumps(data["position"])))
            self.conn.commit()
            self._log("system", None, None, "migrated_from_json")
        except (json.JSONDecodeError, OSError):
            pass

    def _log(self, action, task_id, project_id, detail=None):
        self.conn.execute(
            "INSERT INTO task_history (task_id, project_id, action, detail) VALUES (?, ?, ?, ?)",
            (task_id, project_id, action, detail))

    # --- Projects ---
    def get_projects(self):
        rows = self.conn.execute(
            "SELECT id, name FROM projects WHERE deleted_at IS NULL ORDER BY id").fetchall()
        projects = []
        for pid, name in rows:
            tasks = self.conn.execute(
                "SELECT text, done FROM tasks WHERE project_id=? AND deleted_at IS NULL ORDER BY id",
                (pid,)).fetchall()
            projects.append({
                "id": pid, "name": name,
                "tasks": [{"text": t, "done": bool(d)} for t, d in tasks]
            })
        return projects

    def add_project(self, name):
        cur = self.conn.execute("INSERT INTO projects (name) VALUES (?)", (name,))
        self.conn.commit()
        self._log("project_created", None, cur.lastrowid)
        self.conn.commit()
        return cur.lastrowid

    def delete_project(self, project_id):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute("UPDATE projects SET deleted_at=? WHERE id=?", (now, project_id))
        self.conn.execute("UPDATE tasks SET deleted_at=? WHERE project_id=? AND deleted_at IS NULL",
                          (now, project_id))
        self._log("project_deleted", None, project_id)
        self.conn.commit()

    def get_project_id_by_name(self, name):
        row = self.conn.execute(
            "SELECT id FROM projects WHERE name=? AND deleted_at IS NULL", (name,)).fetchone()
        return row[0] if row else None

    # --- Tasks ---
    def add_task(self, project_id, text):
        cur = self.conn.execute(
            "INSERT INTO tasks (project_id, text) VALUES (?, ?)", (project_id, text))
        self.conn.commit()
        self._log("task_created", cur.lastrowid, project_id, text)
        self.conn.commit()
        return cur.lastrowid

    def toggle_task(self, project_id, text, done):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        comp = now if done else None
        self.conn.execute(
            "UPDATE tasks SET done=?, completed_at=? WHERE project_id=? AND text=? AND deleted_at IS NULL",
            (1 if done else 0, comp, project_id, text))
        action = "task_completed" if done else "task_uncompleted"
        self._log(action, None, project_id, text)
        self.conn.commit()

    def delete_task(self, project_id, text):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "UPDATE tasks SET deleted_at=? WHERE project_id=? AND text=? AND deleted_at IS NULL",
            (now, project_id, text))
        self._log("task_deleted", None, project_id, text)
        self.conn.commit()

    def update_task_text(self, project_id, old_text, new_text):
        self.conn.execute(
            "UPDATE tasks SET text=? WHERE project_id=? AND text=? AND deleted_at IS NULL",
            (new_text, project_id, old_text))
        self._log("task_edited", None, project_id, f"{old_text} -> {new_text}")
        self.conn.commit()

    def clear_done_tasks(self, project_id):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "UPDATE tasks SET deleted_at=? WHERE project_id=? AND done=1 AND deleted_at IS NULL",
            (now, project_id))
        self._log("cleared_done", None, project_id)
        self.conn.commit()

    def get_task_created_at(self, project_id, text):
        row = self.conn.execute(
            "SELECT created_at FROM tasks WHERE project_id=? AND text=? AND deleted_at IS NULL",
            (project_id, text)).fetchone()
        return row[0] if row else None

    # --- Credentials ---
    def get_credentials(self):
        rows = self.conn.execute(
            "SELECT id, company, username, password_enc, url, created_at FROM credentials WHERE deleted_at IS NULL ORDER BY id DESC"
        ).fetchall()
        return [{"id": r[0], "company": r[1], "username": r[2],
                 "password_enc": r[3], "url": r[4], "created_at": r[5]} for r in rows]

    def add_credential(self, company, username, password, url=""):
        enc = _dpapi_encrypt(password)
        cur = self.conn.execute(
            "INSERT INTO credentials (company, username, password_enc, url) VALUES (?, ?, ?, ?)",
            (company, username, enc, url))
        self.conn.commit()
        return cur.lastrowid

    def update_credential(self, cred_id, company, username, password, url=""):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        enc = _dpapi_encrypt(password)
        self.conn.execute(
            "UPDATE credentials SET company=?, username=?, password_enc=?, url=?, updated_at=? WHERE id=?",
            (company, username, enc, url, now, cred_id))
        self.conn.commit()

    def delete_credential(self, cred_id):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute("UPDATE credentials SET deleted_at=? WHERE id=?", (now, cred_id))
        self.conn.commit()

    def decrypt_password(self, password_enc):
        return _dpapi_decrypt(password_enc)

    # --- Settings ---
    def get_position(self):
        row = self.conn.execute("SELECT value FROM settings WHERE key='position'").fetchone()
        if row:
            try:
                return json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    def save_position(self, pos):
        self.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("position", json.dumps(pos)))
        self.conn.commit()

    def save_all(self, projects, position):
        self.save_position(position)
        self.conn.commit()

    def close(self):
        self.conn.close()


# Global DB instance
_db = None

def get_db():
    global _db
    if _db is None:
        _db = FloatTaskDB()
    return _db


def load_data():
    db = get_db()
    projects = db.get_projects()
    position = db.get_position()
    return {"projects": projects, "position": position}


def save_data(data):
    db = get_db()
    db.save_all(data.get("projects", []), data.get("position"))


# ============================================================
# ClickableLabel
# ============================================================
class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ============================================================
# TaskItem
# ============================================================
class TaskItem(QWidget):
    toggled = pyqtSignal()
    deleted = pyqtSignal(object)
    opened = pyqtSignal(object)

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

        self.label = ClickableLabel(self.task_text)
        self.label.setWordWrap(True)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.label.clicked.connect(lambda: self.opened.emit(self))
        layout.addWidget(self.label)

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

    def set_text(self, text):
        self.task_text = text
        self.label.setText(text)

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
# ProjectItem - clickable project row
# ============================================================
class ProjectItem(QWidget):
    clicked = pyqtSignal(object)
    deleted = pyqtSignal(object)

    def __init__(self, name, task_count=0, pending_count=0, parent=None):
        super().__init__(parent)
        self.project_name = name
        self.task_count = task_count
        self.pending_count = pending_count
        self.setFixedHeight(52)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 12, 8)
        layout.setSpacing(10)

        # Folder icon
        icon = QLabel("\U0001F4C1")
        icon.setFixedSize(24, 24)
        icon.setStyleSheet("font-size: 16px; background: transparent;")
        layout.addWidget(icon)

        # Name + count column
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        self.name_label = QLabel(self.project_name)
        self.name_label.setStyleSheet(
            "color: #e2e0f0; font-size: 14px; font-weight: bold;"
            "font-family: 'Segoe UI'; background: transparent;"
        )
        text_layout.addWidget(self.name_label)

        self.count_label = QLabel(self._count_text())
        self.count_label.setStyleSheet(
            "color: #5a5a7a; font-size: 11px; font-family: 'Segoe UI'; background: transparent;"
        )
        text_layout.addWidget(self.count_label)

        layout.addLayout(text_layout, 1)

        # Badge for pending
        if self.pending_count > 0:
            badge = QLabel(str(self.pending_count))
            badge.setFixedSize(24, 24)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet("""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #a78bfa, stop:1 #6366f1);
                color: white; font-size: 11px; font-weight: bold;
                font-family: 'Segoe UI'; border-radius: 12px;
            """)
            layout.addWidget(badge)

        # Arrow
        arrow = QLabel("\u203A")
        arrow.setStyleSheet("color: #4a4a6a; font-size: 18px; background: transparent;")
        layout.addWidget(arrow)

        # Delete btn
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

    def _count_text(self):
        if self.task_count == 0:
            return "No tasks"
        return f"{self.pending_count} pending \u00b7 {self.task_count - self.pending_count} done"

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self)

    def enterEvent(self, event):
        self.del_btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.del_btn.setVisible(False)
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 1, -2, -1)

        if self.underMouse():
            painter.setBrush(QColor(167, 139, 250, 18))
            painter.setPen(QPen(QColor(167, 139, 250, 50), 1))
        else:
            painter.setBrush(QColor(255, 255, 255, 8))
            painter.setPen(QPen(QColor(255, 255, 255, 15), 1))

        painter.drawRoundedRect(rect, 12, 12)
        painter.end()


# ============================================================
# TaskDetailOverlay
# ============================================================
class TaskDetailOverlay(QWidget):
    saved = pyqtSignal(object, str)
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._task_item = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._build_ui()
        self.hide()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)

        title = QLabel("Edit task")
        title.setStyleSheet(
            "color: #e2e0f0; font-size: 15px; font-weight: bold;"
            "font-family: 'Segoe UI'; letter-spacing: 0.5px;"
            "background: transparent;"
        )
        header.addWidget(title)
        header.addStretch()

        close_btn = QPushButton("\u00d7")
        close_btn.setFixedSize(30, 30)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self._on_close)
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.04);
                color: #888;
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
        layout.addLayout(header)

        self.date_label = QLabel("")
        self.date_label.setStyleSheet(
            "color: #5a5a7a; font-size: 11px; font-family: 'Segoe UI';"
            "background: transparent; padding: 0 2px;"
        )
        layout.addWidget(self.date_label)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(167, 139, 250, 0.1);")
        layout.addWidget(sep)

        self.text_edit = QTextEdit()
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background: rgba(255,255,255,0.04);
                color: #d4d4e8;
                border: 1px solid rgba(167,139,250,0.12);
                border-radius: 12px;
                padding: 10px 12px;
                font-size: 13px;
                font-family: 'Segoe UI';
                selection-background-color: #6366f1;
            }
            QTextEdit:focus {
                border-color: rgba(167,139,250,0.4);
                background: rgba(167,139,250,0.06);
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
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
                height: 0;
            }
        """)
        layout.addWidget(self.text_edit, 1)

        footer = QHBoxLayout()
        footer.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel_btn.clicked.connect(self._on_close)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #9ca3af;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
                padding: 0 16px;
                font-size: 12px;
                font-family: 'Segoe UI';
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.05);
                color: #d4d4e8;
            }
        """)
        footer.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setFixedHeight(34)
        save_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save_btn.clicked.connect(self._on_save)
        save_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #a78bfa, stop:1 #6366f1);
                color: white;
                border: none;
                border-radius: 10px;
                padding: 0 20px;
                font-size: 12px;
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
        footer.addWidget(save_btn)
        layout.addLayout(footer)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for i in range(5):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 12 - i * 2), 1))
            painter.drawRoundedRect(self.rect().adjusted(i, i, -i, -i), 20, 20)

        painter.setPen(QPen(QColor(167, 139, 250, 60), 1))
        painter.setBrush(QColor(13, 13, 25, 252))
        painter.drawRoundedRect(self.rect().adjusted(5, 5, -5, -5), 18, 18)
        painter.end()

    def open_for(self, task_item, project_id=None):
        self._task_item = task_item
        self.text_edit.setPlainText(task_item.task_text)
        date_text = ""
        if project_id is not None:
            created = get_db().get_task_created_at(project_id, task_item.task_text)
            if created:
                try:
                    dt = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
                    date_text = f"Created: {dt.strftime('%d %b %Y, %I:%M %p')}"
                except ValueError:
                    date_text = f"Created: {created}"
        self.date_label.setText(date_text)
        if self.parent() is not None:
            self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()
        self.text_edit.setFocus()
        cursor = self.text_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.text_edit.setTextCursor(cursor)

    def _on_save(self):
        new_text = self.text_edit.toPlainText().strip()
        if new_text and self._task_item is not None:
            self.saved.emit(self._task_item, new_text)
        self._task_item = None
        self.hide()
        self.closed.emit()

    def _on_close(self):
        self._task_item = None
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._on_close()
            return
        super().keyPressEvent(event)


# ============================================================
# CredentialItem - single credential row
# ============================================================
class CredentialItem(QWidget):
    deleted = pyqtSignal(object)
    edit_requested = pyqtSignal(object)

    def __init__(self, cred_data, parent=None):
        super().__init__(parent)
        self.cred = cred_data
        self._password_visible = False
        self.setFixedHeight(62)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 10, 8)
        layout.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self.company_label = QLabel(self.cred["company"])
        self.company_label.setStyleSheet(
            "color: #e2e0f0; font-size: 13px; font-weight: bold;"
            "font-family: 'Segoe UI'; background: transparent;"
        )
        top_row.addWidget(self.company_label, 1)

        self.copy_user_btn = QPushButton("ID")
        self.copy_user_btn.setFixedSize(28, 22)
        self.copy_user_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.copy_user_btn.setToolTip("Copy username")
        self.copy_user_btn.clicked.connect(self._copy_username)
        self.copy_user_btn.setStyleSheet("""
            QPushButton {
                background: rgba(167,139,250,0.12);
                color: #a78bfa; border: none; border-radius: 6px;
                font-size: 10px; font-weight: bold; font-family: 'Segoe UI';
            }
            QPushButton:hover { background: rgba(167,139,250,0.25); }
        """)
        top_row.addWidget(self.copy_user_btn)

        self.copy_pass_btn = QPushButton("PW")
        self.copy_pass_btn.setFixedSize(28, 22)
        self.copy_pass_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.copy_pass_btn.setToolTip("Copy password")
        self.copy_pass_btn.clicked.connect(self._copy_password)
        self.copy_pass_btn.setStyleSheet("""
            QPushButton {
                background: rgba(99,102,241,0.15);
                color: #6366f1; border: none; border-radius: 6px;
                font-size: 10px; font-weight: bold; font-family: 'Segoe UI';
            }
            QPushButton:hover { background: rgba(99,102,241,0.3); }
        """)
        top_row.addWidget(self.copy_pass_btn)

        self.del_btn = QPushButton("−")
        self.del_btn.setFixedSize(22, 22)
        self.del_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.del_btn.clicked.connect(lambda: self.deleted.emit(self))
        self.del_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #ef4444;
                border: none; font-size: 16px; font-weight: bold; border-radius: 11px;
            }
            QPushButton:hover { background: rgba(239,68,68,0.12); }
        """)
        self.del_btn.setVisible(False)
        top_row.addWidget(self.del_btn)
        layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)
        self.user_label = QLabel(self.cred["username"])
        self.user_label.setStyleSheet(
            "color: #5a5a7a; font-size: 11px; font-family: 'Segoe UI'; background: transparent;"
        )
        bottom_row.addWidget(self.user_label, 1)

        if self.cred.get("created_at"):
            try:
                dt = datetime.strptime(self.cred["created_at"], "%Y-%m-%d %H:%M:%S")
                date_str = dt.strftime("%d %b %Y")
            except ValueError:
                date_str = ""
            if date_str:
                date_lbl = QLabel(date_str)
                date_lbl.setStyleSheet(
                    "color: #3a3a50; font-size: 10px; font-family: 'Segoe UI'; background: transparent;"
                )
                bottom_row.addWidget(date_lbl)
        layout.addLayout(bottom_row)

    def _copy_username(self):
        QApplication.clipboard().setText(self.cred["username"])
        self.copy_user_btn.setText("✓")
        QTimer.singleShot(1000, lambda: self.copy_user_btn.setText("ID"))

    def _copy_password(self):
        pw = get_db().decrypt_password(self.cred["password_enc"])
        QApplication.clipboard().setText(pw)
        self.copy_pass_btn.setText("✓")
        QTimer.singleShot(1000, lambda: self.copy_pass_btn.setText("PW"))

    def enterEvent(self, event):
        self.del_btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.del_btn.setVisible(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.edit_requested.emit(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 1, -2, -1)
        if self.underMouse():
            painter.setBrush(QColor(167, 139, 250, 18))
            painter.setPen(QPen(QColor(167, 139, 250, 50), 1))
        else:
            painter.setBrush(QColor(255, 255, 255, 8))
            painter.setPen(QPen(QColor(255, 255, 255, 15), 1))
        painter.drawRoundedRect(rect, 10, 10)
        painter.end()


# ============================================================
# CredentialFormOverlay - add/edit credential
# ============================================================
class CredentialFormOverlay(QWidget):
    saved = pyqtSignal(dict)
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._edit_id = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._build_ui()
        self.hide()

    def _make_input(self, placeholder, is_password=False):
        inp = QLineEdit()
        inp.setPlaceholderText(placeholder)
        inp.setFixedHeight(36)
        if is_password:
            inp.setEchoMode(QLineEdit.EchoMode.Password)
        inp.setStyleSheet("""
            QLineEdit {
                background: rgba(255,255,255,0.04); color: #d4d4e8;
                border: 1px solid rgba(167,139,250,0.12); border-radius: 10px;
                padding: 0 12px; font-size: 12px; font-family: 'Segoe UI';
                selection-background-color: #6366f1;
            }
            QLineEdit:focus {
                border-color: rgba(167,139,250,0.4);
                background: rgba(167,139,250,0.06);
            }
            QLineEdit::placeholder { color: #4a4a60; }
        """)
        return inp

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.form_title = QLabel("Add Credential")
        self.form_title.setStyleSheet(
            "color: #e2e0f0; font-size: 15px; font-weight: bold;"
            "font-family: 'Segoe UI'; letter-spacing: 0.5px; background: transparent;"
        )
        header.addWidget(self.form_title)
        header.addStretch()
        close_btn = QPushButton("×")
        close_btn.setFixedSize(30, 30)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self._on_close)
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.04); color: #888;
                border: none; border-radius: 15px; font-size: 18px; font-family: 'Segoe UI';
            }
            QPushButton:hover { background: rgba(239,68,68,0.15); color: #ef4444; }
        """)
        header.addWidget(close_btn)
        layout.addLayout(header)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(167, 139, 250, 0.1);")
        layout.addWidget(sep)

        self.company_input = self._make_input("Company name")
        layout.addWidget(self.company_input)
        self.username_input = self._make_input("Username / Email")
        layout.addWidget(self.username_input)

        pw_row = QHBoxLayout()
        pw_row.setSpacing(6)
        self.password_input = self._make_input("Password", is_password=True)
        pw_row.addWidget(self.password_input)
        self.toggle_pw_btn = QPushButton("\U0001F441")
        self.toggle_pw_btn.setFixedSize(36, 36)
        self.toggle_pw_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.toggle_pw_btn.clicked.connect(self._toggle_password_visibility)
        self.toggle_pw_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.04); color: #5a5a7a;
                border: 1px solid rgba(167,139,250,0.12); border-radius: 10px;
                font-size: 14px;
            }
            QPushButton:hover { background: rgba(167,139,250,0.12); }
        """)
        pw_row.addWidget(self.toggle_pw_btn)
        layout.addLayout(pw_row)

        self.url_input = self._make_input("URL (optional)")
        layout.addWidget(self.url_input)

        layout.addStretch()

        footer = QHBoxLayout()
        footer.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel_btn.clicked.connect(self._on_close)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #9ca3af;
                border: 1px solid rgba(255,255,255,0.08); border-radius: 10px;
                padding: 0 16px; font-size: 12px; font-family: 'Segoe UI';
            }
            QPushButton:hover { background: rgba(255,255,255,0.05); color: #d4d4e8; }
        """)
        footer.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setFixedHeight(34)
        save_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save_btn.clicked.connect(self._on_save)
        save_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #a78bfa,stop:1 #6366f1);
                color: white; border: none; border-radius: 10px;
                padding: 0 20px; font-size: 12px; font-weight: bold; font-family: 'Segoe UI';
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #b89dff,stop:1 #7577ff);
            }
        """)
        footer.addWidget(save_btn)
        layout.addLayout(footer)

    def _toggle_password_visibility(self):
        if self.password_input.echoMode() == QLineEdit.EchoMode.Password:
            self.password_input.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            self.password_input.setEchoMode(QLineEdit.EchoMode.Password)

    def open_new(self):
        self._edit_id = None
        self.form_title.setText("Add Credential")
        self.company_input.clear()
        self.username_input.clear()
        self.password_input.clear()
        self.url_input.clear()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        if self.parent() is not None:
            self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()
        self.company_input.setFocus()

    def open_edit(self, cred):
        self._edit_id = cred["id"]
        self.form_title.setText("Edit Credential")
        self.company_input.setText(cred["company"])
        self.username_input.setText(cred["username"])
        self.password_input.setText(get_db().decrypt_password(cred["password_enc"]))
        self.url_input.setText(cred.get("url", ""))
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        if self.parent() is not None:
            self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()
        self.company_input.setFocus()

    def _on_save(self):
        company = self.company_input.text().strip()
        username = self.username_input.text().strip()
        password = self.password_input.text()
        url = self.url_input.text().strip()
        if not company or not username or not password:
            return
        self.saved.emit({
            "id": self._edit_id, "company": company,
            "username": username, "password": password, "url": url
        })
        self._edit_id = None
        self.hide()
        self.closed.emit()

    def _on_close(self):
        self._edit_id = None
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._on_close()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i in range(5):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 12 - i * 2), 1))
            painter.drawRoundedRect(self.rect().adjusted(i, i, -i, -i), 20, 20)
        painter.setPen(QPen(QColor(167, 139, 250, 60), 1))
        painter.setBrush(QColor(13, 13, 25, 252))
        painter.drawRoundedRect(self.rect().adjusted(5, 5, -5, -5), 18, 18)
        painter.end()


# ============================================================
# PopupPanel - with project/task navigation
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

        # Data
        self.projects = []  # list of {"name": str, "tasks": list}
        self._current_project_idx = None
        self._vault_mode = False

        self._build_ui()

        # Detail overlay
        self.detail = TaskDetailOverlay(self)
        self.detail.saved.connect(self._on_detail_saved)
        self.detail.setGeometry(self.rect())

        # Credential form overlay
        self.cred_form = CredentialFormOverlay(self)
        self.cred_form.saved.connect(self._on_cred_saved)
        self.cred_form.setGeometry(self.rect())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for i in range(5):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 12 - i * 2), 1))
            painter.drawRoundedRect(self.rect().adjusted(i, i, -i, -i), 20, 20)

        painter.setPen(QPen(QColor(167, 139, 250, 40), 1))
        painter.setBrush(QColor(13, 13, 25, 248))
        painter.drawRoundedRect(self.rect().adjusted(5, 5, -5, -5), 18, 18)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(167, 139, 250, 25))
        painter.drawRoundedRect(self.rect().adjusted(20, 6, -20, -(self.PANEL_H - 9)), 2, 2)
        painter.end()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 18, 20, 16)
        main_layout.setSpacing(12)

        # --- Header ---
        self.header_layout = QHBoxLayout()
        self.header_layout.setSpacing(8)

        self.back_btn = QPushButton("\u2039")
        self.back_btn.setFixedSize(30, 30)
        self.back_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.back_btn.clicked.connect(self._go_back)
        self.back_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.04);
                color: #a78bfa;
                border: none;
                border-radius: 15px;
                font-size: 20px;
                font-family: 'Segoe UI';
            }
            QPushButton:hover {
                background: rgba(167,139,250,0.12);
            }
        """)
        self.back_btn.setVisible(False)
        self.header_layout.addWidget(self.back_btn)

        icon_dot = QLabel()
        icon_dot.setFixedSize(10, 10)
        icon_dot.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #a78bfa, stop:1 #6366f1);
            border-radius: 5px;
        """)
        self.header_layout.addWidget(icon_dot)

        self.title_label = QLabel("FloatTask")
        self.title_label.setStyleSheet(
            "color: #e2e0f0; font-size: 15px; font-weight: bold;"
            "font-family: 'Segoe UI'; letter-spacing: 0.5px;"
        )
        self.header_layout.addWidget(self.title_label)
        self.header_layout.addStretch()

        self.vault_btn = QPushButton("\U0001F512")
        self.vault_btn.setFixedSize(30, 30)
        self.vault_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.vault_btn.setToolTip("Password Vault")
        self.vault_btn.clicked.connect(self._toggle_vault)
        self.vault_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.04);
                color: #5a5a7a;
                border: none;
                border-radius: 15px;
                font-size: 14px;
                font-family: 'Segoe UI';
            }
            QPushButton:hover {
                background: rgba(167,139,250,0.12);
                color: #a78bfa;
            }
        """)
        self.header_layout.addWidget(self.vault_btn)

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
        self.header_layout.addWidget(close_btn)
        main_layout.addLayout(self.header_layout)

        # Separator
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(167, 139, 250, 0.1);")
        main_layout.addWidget(sep)

        # --- Input row ---
        self.input_row = QHBoxLayout()
        self.input_row.setSpacing(8)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("New project name...")
        self.input_field.setFixedHeight(40)
        self.input_field.returnPressed.connect(self._on_add)
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
        self.input_row.addWidget(self.input_field)

        self.add_btn = QPushButton("+")
        self.add_btn.setFixedSize(40, 40)
        self.add_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.add_btn.clicked.connect(self._on_add)
        self.add_btn.setStyleSheet("""
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
        self.input_row.addWidget(self.add_btn)

        self.task_input_widget = QWidget()
        self.task_input_widget.setLayout(self.input_row)
        main_layout.addWidget(self.task_input_widget)

        # --- Vault input fields (hidden by default) ---
        vault_input_style = """
            QLineEdit {
                background: rgba(255,255,255,0.04); color: #d4d4e8;
                border: 1px solid rgba(167,139,250,0.12); border-radius: 10px;
                padding: 0 12px; font-size: 12px; font-family: 'Segoe UI';
                selection-background-color: #6366f1;
            }
            QLineEdit:focus {
                border-color: rgba(167,139,250,0.4);
                background: rgba(167,139,250,0.06);
            }
            QLineEdit::placeholder { color: #4a4a60; }
        """
        self.vault_input_widget = QWidget()
        vault_layout = QVBoxLayout(self.vault_input_widget)
        vault_layout.setContentsMargins(0, 0, 0, 0)
        vault_layout.setSpacing(6)

        self.vault_company = QLineEdit()
        self.vault_company.setPlaceholderText("Company / Title")
        self.vault_company.setFixedHeight(34)
        self.vault_company.setStyleSheet(vault_input_style)
        vault_layout.addWidget(self.vault_company)

        cred_row = QHBoxLayout()
        cred_row.setSpacing(6)
        self.vault_username = QLineEdit()
        self.vault_username.setPlaceholderText("Email / Username")
        self.vault_username.setFixedHeight(34)
        self.vault_username.setStyleSheet(vault_input_style)
        cred_row.addWidget(self.vault_username)

        self.vault_password = QLineEdit()
        self.vault_password.setPlaceholderText("Password")
        self.vault_password.setFixedHeight(34)
        self.vault_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.vault_password.setStyleSheet(vault_input_style)
        cred_row.addWidget(self.vault_password)

        self.vault_add_btn = QPushButton("+")
        self.vault_add_btn.setFixedSize(34, 34)
        self.vault_add_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.vault_add_btn.clicked.connect(self._vault_add_credential)
        self.vault_add_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #a78bfa,stop:1 #6366f1);
                color: white; border: none; border-radius: 10px;
                font-size: 20px; font-weight: bold; font-family: 'Segoe UI';
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #b89dff,stop:1 #7577ff);
            }
        """)
        cred_row.addWidget(self.vault_add_btn)
        vault_layout.addLayout(cred_row)

        self.vault_input_widget.setVisible(False)
        main_layout.addWidget(self.vault_input_widget)

        # --- Scrollable content ---
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

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 4, 0, 4)
        self.list_layout.setSpacing(4)
        self.list_layout.addStretch()

        self.scroll.setWidget(self.list_container)
        main_layout.addWidget(self.scroll)

        # --- Footer ---
        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet("background: rgba(167, 139, 250, 0.08);")
        main_layout.addWidget(sep2)

        footer = QHBoxLayout()
        footer.setContentsMargins(4, 2, 4, 0)
        self.status_label = QLabel("")
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
        self.clear_btn.setVisible(False)
        footer.addWidget(self.clear_btn)
        main_layout.addLayout(footer)

    # --- Data loading ---
    def load_projects(self, projects):
        self.projects = projects
        self._show_projects_view()

    def _show_projects_view(self):
        self._current_project_idx = None
        self._vault_mode = False
        self.vault_btn.setText("\U0001F512")
        self.back_btn.setVisible(False)
        self.title_label.setText("FloatTask")
        self.input_field.setPlaceholderText("New project name...")
        self.input_field.clear()
        self.clear_btn.setVisible(False)
        self.task_input_widget.setVisible(True)
        self.vault_input_widget.setVisible(False)
        self._clear_list()

        for proj in reversed(self.projects):
            pending = sum(1 for t in proj["tasks"] if not t.get("done", False))
            item = ProjectItem(proj["name"], len(proj["tasks"]), pending)
            item.clicked.connect(self._open_project)
            item.deleted.connect(self._delete_project)
            self.list_layout.insertWidget(0, item)

        total_projects = len(self.projects)
        total_pending = sum(
            1 for p in self.projects for t in p["tasks"] if not t.get("done", False)
        )
        self.status_label.setText(f"{total_projects} projects \u00b7 {total_pending} pending")

    def _show_tasks_view(self, project_idx):
        self._current_project_idx = project_idx
        proj = self.projects[project_idx]
        self.back_btn.setVisible(True)
        self.title_label.setText(proj["name"])
        self.input_field.setPlaceholderText("What needs to be done?")
        self.clear_btn.setVisible(True)
        self._clear_list()

        self._task_items = []
        for t in proj["tasks"]:
            self._create_task_widget(t["text"], t.get("done", False))
        self._update_task_footer()

    def _clear_list(self):
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # --- Project actions ---
    def _on_add(self):
        text = self.input_field.text().strip()
        if not text:
            return
        self.input_field.clear()

        if self._current_project_idx is None:
            pid = get_db().add_project(text)
            self.projects.append({"id": pid, "name": text, "tasks": []})
            self._show_projects_view()
        else:
            proj = self.projects[self._current_project_idx]
            get_db().add_task(proj["id"], text)
            self._create_task_widget(text, False)
            self._update_task_footer()

        self.data_changed.emit()

    def _open_project(self, item):
        for i, proj in enumerate(self.projects):
            if proj["name"] == item.project_name:
                self._show_tasks_view(i)
                return

    def _delete_project(self, item):
        for i, proj in enumerate(self.projects):
            if proj["name"] == item.project_name:
                get_db().delete_project(proj["id"])
                self.projects.pop(i)
                break
        self._show_projects_view()
        self.data_changed.emit()

    def _go_back(self):
        # Save current tasks back to project data
        if self._current_project_idx is not None:
            self._sync_tasks_to_data()
        self._show_projects_view()

    # --- Task actions ---
    def _create_task_widget(self, text, done):
        item = TaskItem(text, done)
        item.toggled.connect(self._on_task_changed)
        item.deleted.connect(self._delete_task)
        item.opened.connect(self._open_detail)
        if not hasattr(self, '_task_items'):
            self._task_items = []
        self._task_items.append(item)
        self.list_layout.insertWidget(self.list_layout.count() - 1, item)
        self._sort_tasks()

    def _open_detail(self, item):
        pid = None
        if self._current_project_idx is not None:
            pid = self.projects[self._current_project_idx].get("id")
        self.detail.open_for(item, pid)

    def _on_detail_saved(self, item, new_text):
        if self._current_project_idx is not None:
            proj = self.projects[self._current_project_idx]
            get_db().update_task_text(proj["id"], item.task_text, new_text)
        item.set_text(new_text)
        self._sync_tasks_to_data()
        self.data_changed.emit()

    def _on_task_changed(self):
        sender = self.sender() if hasattr(self, 'sender') else None
        if self._current_project_idx is not None and sender:
            proj = self.projects[self._current_project_idx]
            get_db().toggle_task(proj["id"], sender.task_text, sender.done)
        self._sort_tasks()
        self._update_task_footer()
        self._sync_tasks_to_data()
        self.data_changed.emit()

    def _delete_task(self, item):
        if self._current_project_idx is not None:
            proj = self.projects[self._current_project_idx]
            get_db().delete_task(proj["id"], item.task_text)
        self._task_items.remove(item)
        self.list_layout.removeWidget(item)
        item.deleteLater()
        self._update_task_footer()
        self._sync_tasks_to_data()
        self.data_changed.emit()

    def _clear_done(self):
        if not hasattr(self, '_task_items'):
            return
        if self._current_project_idx is not None:
            proj = self.projects[self._current_project_idx]
            get_db().clear_done_tasks(proj["id"])
        done_items = [i for i in self._task_items if i.done]
        for item in done_items:
            self._task_items.remove(item)
            self.list_layout.removeWidget(item)
            item.deleteLater()
        self._update_task_footer()
        self._sync_tasks_to_data()
        self.data_changed.emit()

    def _sort_tasks(self):
        if not hasattr(self, '_task_items'):
            return
        for item in self._task_items:
            self.list_layout.removeWidget(item)
        pending = [i for i in self._task_items if not i.done]
        done = [i for i in self._task_items if i.done]
        # Newest first — reverse pending so last-added shows on top
        for i, item in enumerate(list(reversed(pending)) + done):
            self.list_layout.insertWidget(i, item)

    def _update_task_footer(self):
        if not hasattr(self, '_task_items'):
            return
        pending = sum(1 for i in self._task_items if not i.done)
        done = sum(1 for i in self._task_items if i.done)
        self.status_label.setText(f"{pending} pending \u00b7 {done} done")

    def _sync_tasks_to_data(self):
        if self._current_project_idx is not None and hasattr(self, '_task_items'):
            self.projects[self._current_project_idx]["tasks"] = [
                i.to_dict() for i in self._task_items
            ]

    # --- Vault actions ---
    def _toggle_vault(self):
        if self._vault_mode:
            self._vault_mode = False
            self.vault_btn.setText("\U0001F512")
            self._show_projects_view()
        else:
            self._vault_mode = True
            self.vault_btn.setText("\U0001F4CB")
            self._show_vault_view()

    def _show_vault_view(self):
        self._current_project_idx = None
        self.back_btn.setVisible(False)
        self.title_label.setText("Vault")
        self.clear_btn.setVisible(False)
        self.task_input_widget.setVisible(False)
        self.vault_input_widget.setVisible(True)
        self.vault_company.clear()
        self.vault_username.clear()
        self.vault_password.clear()
        self._clear_list()
        self._load_credentials()

    def _vault_add_credential(self):
        company = self.vault_company.text().strip()
        username = self.vault_username.text().strip()
        password = self.vault_password.text()
        if not company or not username or not password:
            return
        get_db().add_credential(company, username, password)
        self.vault_company.clear()
        self.vault_username.clear()
        self.vault_password.clear()
        self.vault_company.setFocus()
        self._load_credentials()

    def _load_credentials(self):
        self._clear_list()
        creds = get_db().get_credentials()
        for cred in creds:
            item = CredentialItem(cred)
            item.deleted.connect(self._delete_credential)
            item.edit_requested.connect(self._edit_credential)
            self.list_layout.insertWidget(self.list_layout.count() - 1, item)
        self.status_label.setText(f"{len(creds)} credentials")

    def _edit_credential(self, item):
        self.cred_form.open_edit(item.cred)

    def _delete_credential(self, item):
        get_db().delete_credential(item.cred["id"])
        self._load_credentials()

    def _on_cred_saved(self, data):
        if data["id"] is not None:
            get_db().update_credential(data["id"], data["company"], data["username"],
                                       data["password"], data.get("url", ""))
        self._load_credentials()

    # --- Public API ---
    def get_all_pending_count(self):
        return sum(
            1 for p in self.projects for t in p["tasks"] if not t.get("done", False)
        )

    def get_projects_data(self):
        # Make sure current view is synced
        if self._current_project_idx is not None and hasattr(self, '_task_items'):
            self._sync_tasks_to_data()
        return self.projects

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "detail"):
            self.detail.setGeometry(self.rect())
        if hasattr(self, "cred_form"):
            self.cred_form.setGeometry(self.rect())

    def _close(self):
        self._sync_tasks_to_data()
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
        self.panel.load_projects(data.get("projects", []))
        self._pending_count = self.panel.get_all_pending_count()

        # Restore position (clamp to visible screen area)
        screen = QApplication.primaryScreen().geometry()
        pos = data.get("position")
        if pos and isinstance(pos, list) and len(pos) == 2:
            x = max(0, min(pos[0], screen.width() - self.CIRCLE_SIZE))
            y = max(0, min(pos[1], screen.height() - self.CIRCLE_SIZE))
            self.move(x, y)
        else:
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

        glow_strength = 0.5 + 0.5 * math.sin(self._glow_phase)
        for i in range(3):
            alpha = int((15 + 10 * glow_strength) * (3 - i) / 3)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(167, 139, 250, alpha))
            painter.drawEllipse(int(cx - r - 2 - i), int(cy - r - 2 - i),
                                int((r + 2 + i) * 2), int((r + 2 + i) * 2))

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

        ring_alpha = int(80 + 40 * glow_strength)
        if self._hover:
            ring_alpha = min(ring_alpha + 40, 200)
        painter.setPen(QPen(QColor(167, 139, 250, ring_alpha), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(int(cx - r + 1), int(cy - r + 1),
                            int((r - 1) * 2), int((r - 1) * 2))

        if self._pending_count > 0:
            painter.setPen(QColor(220, 210, 255))
            font = QFont("Segoe UI", 17, QFont.Weight.Bold)
            painter.setFont(font)
            from PyQt6.QtCore import QRectF
            painter.drawText(QRectF(0, 0, self.width(), self.height()),
                             Qt.AlignmentFlag.AlignCenter, str(self._pending_count))
        else:
            painter.setPen(QPen(QColor(99, 102, 241), 2.5))
            painter.drawLine(int(cx - 6), int(cy + 1), int(cx - 1), int(cy + 6))
            painter.drawLine(int(cx - 1), int(cy + 6), int(cx + 8), int(cy - 5))

        painter.end()

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
        self._pending_count = self.panel.get_all_pending_count()
        self.update()
        self._save_all()

    def _save_position(self):
        self._save_all()

    def _save_all(self):
        get_db().save_position([self.pos().x(), self.pos().y()])


# ============================================================
# Main
# ============================================================
def acquire_single_instance_lock():
    """Acquire a lock file to prevent multiple instances. Returns file handle or None."""
    lock_path = Path(tempfile.gettempdir()) / "floattask.lock"
    try:
        lock_file = open(lock_path, "w")
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        lock_file.write(str(sys.executable))
        lock_file.flush()
        return lock_file
    except (OSError, IOError):
        try:
            lock_file.close()
        except Exception:
            pass
        # Stale lock from unclean shutdown — remove and retry
        try:
            lock_path.unlink(missing_ok=True)
            lock_file = open(lock_path, "w")
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            lock_file.write(str(sys.executable))
            lock_file.flush()
            return lock_file
        except (OSError, IOError):
            return None


def main():
    lock = acquire_single_instance_lock()
    if lock is None:
        print("FloatTask already running.")
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    circle = FloatingCircle()
    circle.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
