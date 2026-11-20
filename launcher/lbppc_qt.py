#!/usr/bin/env python3
"""LBPPC's launcher for Linux, in Qt.

The Tk launcher (launcher/lbppc_launcher.py) is the Windows one: it draws everything itself and runs through
XWayland on a Wayland desktop. This one uses Qt's own widgets: on KDE or GNOME it is a native Wayland window, on X11
a native X11 one. Both use the toolkit-free lbppc_core.py for settings, the game check and the environment run.py
gets, but neither imports the other, so the Linux package needs no Tk and the Windows package needs no Qt.

It offers the game folder, every graphics setting, language, save files, the online account (including the
lbppc.com sign-in) and friends, the controls and the Debug page's overlay and diagnostics.

usage: python launcher/lbppc_qt.py [--play]
"""
import os
from pathlib import Path
import queue
import re
import runpy
import subprocess
import sys
import threading
import webbrowser

# Frozen with PyInstaller: LBPPC sits in the package folder and runs run.py and the preparation scripts itself
# (--script), with the Python frozen into it. lbppc_core.py brings the same layout as the Windows launcher.
FROZEN = getattr(sys, 'frozen', False)
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
if not FROZEN:
    sys.path[:0] = [str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]


def run_script(argv):
    """LBPPC --script <file.py> [args]: runs a Python script of the package with the frozen Python."""
    path = Path(argv[0]).resolve()
    sys.argv = [str(path), *argv[1:]]
    sys.path.insert(0, str(path.parent))
    runpy.run_path(str(path), run_name='__main__')


if __name__ == '__main__':
    # These work before Qt is imported (and before a display is needed).
    if len(sys.argv) > 2 and sys.argv[1] == '--script':
        run_script(sys.argv[2:])
        sys.exit(0)
    if len(sys.argv) > 3 and sys.argv[1] == '--apply-update':
        import updater
        updater.run_apply(sys.argv[2:])
        sys.exit(0)

import lbppc_core as core  # noqa: E402  (the shared settings, game check and launch environment; no Tk here)
from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QIcon, QPixmap  # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QTabWidget, QVBoxLayout, QWidget)  # noqa: E402

# The launcher's look: night felt, lime edges (the same colours as lbppc_theme.py).
STYLE = """
QWidget {
    background: #161a2a;
    color: #f0f0e8;
    font-size: 10.5pt;
}
QLabel#title {
    color: #f2a33a;
    font-size: 25pt;
    font-weight: 700;
}
QLabel#subtitle { color: #aab2c4; }
QLabel#version { color: #6d768c; }
QLabel#hint { color: #aab2c4; font-size: 9pt; }
QLabel#status { padding-top: 5px; }
QLabel#status[state="ready"] { color: #9ccc65; }
QLabel#status[state="warn"], QLabel#status[state="setup"], QLabel#status[state="busy"] { color: #f2a33a; }
QLabel#status[state="missing"], QLabel#status[state="error"] { color: #ff7b6b; }
QLabel#section { color: #a0d246; font-weight: 600; }
QGroupBox {
    background: #1c2234;
    border: 1px solid #3e4a68;
    border-radius: 10px;
    margin-top: 12px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: #a0d246;
}
QLineEdit, QComboBox, QDoubleSpinBox {
    background: #242a3e;
    border: 1px solid #4e5a78;
    border-radius: 7px;
    padding: 7px 9px;
    selection-background-color: #a0d246;
    selection-color: #10140a;
}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus { border-color: #a0d246; }
QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled { color: #6d768c; background: #202638; }
QComboBox::drop-down { border: 0; width: 22px; }
QComboBox QAbstractItemView {
    background: #242a3e;
    color: #f0f0e8;
    selection-background-color: #4b5a24;
    border: 1px solid #4e5a78;
}
QPushButton {
    background: #2a3350;
    border: 1px solid #4e5a78;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 600;
}
QPushButton:hover { background: #354064; border-color: #a0d246; }
QPushButton:pressed { background: #1d2540; }
QPushButton:disabled { color: #6d768c; background: #202638; border-color: #333c52; }
QPushButton#play {
    background: #6d9c1f;
    border: 1px solid #a0d246;
    color: #10140a;
    font-size: 14pt;
    padding: 12px 26px;
}
QPushButton#play:hover { background: #86bf2a; }
QPushButton#play:disabled { background: #2c361d; border-color: #3d4b28; color: #718052; }
QPushButton#stop {
    background: #5a2a2a;
    border: 1px solid #a05050;
    font-size: 12pt;
    padding: 12px 20px;
}
QPushButton#stop:hover { background: #743434; }
QPushButton#stop:disabled { background: #2c2024; border-color: #453033; color: #7b6668; }
QCheckBox { color: #d7dce8; spacing: 8px; }
QPlainTextEdit {
    background: #0d1019;
    border: 1px solid #333c52;
    border-radius: 8px;
    color: #c8ceda;
    font-family: "Consolas", "DejaVu Sans Mono", monospace;
    font-size: 9pt;
    padding: 6px;
}
QProgressBar {
    background: #242a3e;
    border: 1px solid #4e5a78;
    border-radius: 6px;
    height: 16px;
    text-align: center;
    color: #f0f0e8;
}
QProgressBar::chunk { background: #a0d246; border-radius: 5px; }
QTabWidget::pane {
    border: 1px solid #3e4a68;
    border-radius: 10px;
    background: #1c2234;
    top: -1px;
}
QTabBar::tab {
    background: #242a3e;
    color: #aab2c4;
    padding: 8px 18px;
    margin-right: 4px;
    border: 1px solid #3e4a68;
    border-bottom: 0;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 600;
}
QTabBar::tab:selected { background: #2a3350; color: #f0f0e8; border-color: #a0d246; }
QScrollArea { border: 0; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
"""


def label(text, name='hint', wrap=True):
    widget = QLabel(core.t(text))
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def fill_combo(combo, options, value):
    combo.blockSignals(True)
    combo.clear()
    for name, code in options:
        combo.addItem(core.t(name), code)
    index = combo.findData(value)
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def plain_notes(notes, limit=520):
    """Release notes (Markdown) as a few plain lines."""
    lines = []
    for line in notes.splitlines():
        if line.lstrip().startswith('#'):
            continue
        line = re.sub(r'[*_`>]+', '', re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', line)).strip()
        line = re.sub(r'^[-+]\s*', '', line)
        if line:
            lines.append(line.rstrip('.'))
    text = '  •  '.join(lines)
    return text if len(text) <= limit else text[:limit].rsplit(' ', 1)[0] + '...'


class SetupDialog(QDialog):
    """Set up...: the game and its 1.28 update, checked and run with scripts/game_setup.py (the same work as the Tk
    launcher's wizard). Neither folder is changed; only DLC is copied into LBPPC's user folder."""

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.settings = settings
        self.game_root = ''
        self.update_root = ''
        self.plan = None
        self.running = False
        self.cancel_requested = False
        self.events = queue.Queue()
        self.setWindowTitle(core.t('Set up your game'))
        self.setMinimumWidth(680)
        self._build()
        self._load()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        intro = QLabel(core.t('LBPPC runs LittleBigPlanet 3 version 1.28 (US CUSA00473, or European CUSA00063). '
                              'Choose your game and its 1.28 update: as in shadPS4, the update is read from its own '
                              'folder and neither folder is changed.'))
        intro.setWordWrap(True)
        intro.setObjectName('hint')
        layout.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(8)
        self.game = QLineEdit()
        self.game.setPlaceholderText(core.t('Game folder (holding eboot.bin)'))
        self.game.textChanged.connect(self._schedule_check)
        self.update = QLineEdit()
        self.update.setPlaceholderText(core.t('Update folder (the 1.28 update)'))
        self.update.textChanged.connect(self._schedule_check)
        self.dlc = QLineEdit()
        self.dlc.setPlaceholderText(core.t('DLC folder (one extracted DLC, or a folder of them)'))
        self.dlc.textChanged.connect(self._schedule_check)
        self.notes = {}
        for key, title, field in (('game', core.t('Game folder'), self.game),
                                  ('update', core.t('The 1.28 update folder'), self.update),
                                  ('dlc', core.t('DLC (optional)'), self.dlc)):
            row = QHBoxLayout()
            row.addWidget(field, 1)
            browse = QPushButton(core.t('Browse...'))
            browse.clicked.connect(lambda _checked=False, k=key: self._browse(k))
            row.addWidget(browse)
            box = QWidget()
            box.setLayout(row)
            note = QLabel()
            note.setWordWrap(True)
            note.setObjectName('hint')
            self.notes[key] = note
            form.addRow(title, box)
            form.addRow('', note)
        layout.addLayout(form)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.cancel = QPushButton(core.t('Cancel'))
        self.cancel.clicked.connect(self._cancel)
        self.go = QPushButton(core.t('Set up'))
        self.go.setDefault(True)
        self.go.clicked.connect(self._start)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel)
        buttons.addWidget(self.go)
        layout.addLayout(buttons)
        self.check_timer = QTimer(self)
        self.check_timer.setSingleShot(True)
        self.check_timer.timeout.connect(self._check)

    def _load(self):
        self.game.setText(self.settings.get('game', ''))
        self.update.setText(self.settings.get('update', ''))
        if not self.update.text().strip() and self.game.text().strip():
            found = core.game_setup.find_update(core.game_setup.find_root(self.game.text().strip()))
            if found:
                self.update.setText(str(found))
        self._check()

    def _schedule_check(self):
        if not self.running:
            self.check_timer.start(250)

    def _browse(self, key):
        if self.running:
            return
        field = {'game': self.game, 'update': self.update, 'dlc': self.dlc}[key]
        start = field.text().strip() or self.game.text().strip()
        folder = QFileDialog.getExistingDirectory(self, self.notes[key].text() or key, start)
        if folder:
            field.setText(folder)
            if key == 'game' and not self.update.text().strip():
                found = core.game_setup.find_update(core.game_setup.find_root(folder))
                if found:
                    self.update.setText(str(found))

    def _note(self, key, text, colour):
        self.notes[key].setText(text)
        self.notes[key].setStyleSheet(f'color: {colour};')

    def _check(self):
        if self.running:
            return
        game_text, update_text, dlc_text = (field.text().strip() for field in (self.game, self.update, self.dlc))
        game = core.game_setup.Folder(game_text) if game_text else None
        update = core.game_setup.Folder(update_text) if update_text else None
        for key, folder in (('game', game), ('update', update)):
            if not folder:
                self._note(key, core.t('Not chosen yet.') if key == 'game' else
                           core.t('Not needed if your game is already version 1.28.'), '#aab2c4')
                continue
            problem = folder.problem(key)
            if problem and not (key == 'update' and folder.root):
                self._note(key, problem, '#ff7b6b')
            else:
                inner = folder.root.relative_to(folder.chosen).as_posix() if folder.root != folder.chosen else ''
                text = core.t('Found: {what}', what=folder.describe()) + (
                    core.t(' (in {folder})', folder=inner) if inner else '')
                self._note(key, text, '#9ccc65')
        if not dlc_text:
            self._note('dlc', core.t('Optional. Choose an extracted DLC, or a folder holding several.'), '#aab2c4')
        else:
            try:
                found = core.game_setup.find_dlc(dlc_text)
                names = ', '.join(dlc.label for dlc in found[:4]) + (
                    core.t(' and {count} more', count=len(found) - 4) if len(found) > 4 else '')
                self._note('dlc', core.t('Found {count} DLC: {names}', count=len(found), names=names), '#9ccc65')
            except core.game_setup.SetupError as error:
                self._note('dlc', str(error), '#ff7b6b')
            except OSError as error:
                self._note('dlc', core.t('Can\'t read that folder: {error}', error=error), '#ff7b6b')
        self.plan = None
        try:
            plan = core.game_setup.Plan(game_text, update_text or None, dlc_folder=dlc_text or None,
                                        user_dir=core.USER) if game_text else None
            if plan:
                plan.check_space()
                self.plan = plan
                self.summary.setText(plan.summary() + ('.' if plan.steps else ''))
                self.summary.setStyleSheet('color: #f0f0e8;' if plan.steps else 'color: #9ccc65;')
            else:
                self.summary.setText(core.t('Choose your game folder to begin.'))
                self.summary.setStyleSheet('color: #aab2c4;')
        except core.game_setup.SetupError as error:
            self.summary.setText(str(error))
            self.summary.setStyleSheet('color: #f2a33a;')
        except OSError as error:
            self.summary.setText(core.t('Can\'t read the folders: {error}', error=error))
            self.summary.setStyleSheet('color: #ff7b6b;')
        ready = bool(self.plan)
        self.go.setEnabled(ready)
        self.go.setText(core.t('Use these folders') if self.plan and not self.plan.steps else core.t('Set up'))

    def _start(self):
        if self.running or not self.plan:
            return
        plan = self.plan
        if not plan.steps:
            self._finish(True, '')
            return
        self.running, self.cancel_requested = True, False
        for field in (self.game, self.update, self.dlc):
            field.setEnabled(False)
        self.go.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.cancel.setEnabled(True)
        thread = threading.Thread(target=self._work, args=(plan,), daemon=True)
        thread.start()
        self.pump = QTimer(self)
        self.pump.timeout.connect(self._poll)
        self.pump.start(80)

    def _work(self, plan):
        try:
            done = core.game_setup.run(plan, lambda d, total, name: self.events.put(('progress', d, total, name)),
                                       lambda: self.cancel_requested)
            self.events.put(('done', done, ''))
        except core.game_setup.SetupError as error:
            self.events.put(('done', False, str(error)))
        except OSError as error:
            self.events.put(('done', False, core.t('Setup stopped: {error} ({file}). Close any program using the '
                                                   'game files and try again.',
                                                   error=error.strerror or error, file=error.filename or '')))

    def _poll(self):
        last = None
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == 'done':
                self.pump.stop()
                self.running = False
                self.cancel_requested = False
                for field in (self.game, self.update, self.dlc):
                    field.setEnabled(True)
                self.progress.setVisible(False)
                self._check()
                self._finish(bool(event[1]), event[2])
                return
            last = event
        if last:
            done, total, name = last
            self.progress.setValue(done * 100 // max(total, 1))
            self.summary.setText(f'{done * 100 // max(total, 1)}%  {name}')
            self.summary.setStyleSheet('color: #f0f0e8;')

    def _finish(self, ok, message):
        if ok:
            self.game_root = str(self.plan.root)
            self.update_root = str(self.plan.update.root) if self.plan.update else ''
            self.accept()
            return
        self.summary.setText(message or core.t('Set up did not finish. Try again.'))
        self.summary.setStyleSheet('color: #ff7b6b;')
        self.go.setEnabled(bool(self.plan))
        self.cancel.setEnabled(True)

    def _cancel(self):
        if self.running:
            self.cancel_requested = True
            self.cancel.setEnabled(False)
            return
        self.reject()


class SaveFileDialog(QDialog):
    """New save file, or Rename or delete one (scripts/save_files.py). `current` None makes a new one."""

    def __init__(self, parent, current=None):
        super().__init__(parent)
        self.current = current
        self.result_name = None
        self.setWindowTitle(core.t('Rename or delete {name}', name=current) if current else core.t('New save file'))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        intro = label(core.t('Its progress stays in its own folder. Deleting keeps a copy in user\\save_backups, so it '
                             'can be brought back.') if current else
                      core.t('A new save file starts LittleBigPlanet 3 from the beginning. Your other save files are '
                             'kept as they are. Settings, DLC and your online account are shared.'))
        intro.setObjectName('hint')
        layout.addWidget(intro)
        layout.addWidget(label('Name', 'section', False))
        self.name = QLineEdit(current or '')
        self.name.returnPressed.connect(self._confirm)
        layout.addWidget(self.name)
        self.note = label(core.t('1 to 24 letters, digits, spaces, - or _.'))
        layout.addWidget(self.note)
        buttons = QHBoxLayout()
        if current:
            delete = QPushButton(core.t('Delete'))
            delete.setStyleSheet('background:#5a2a2a; border-color:#a05050;')
            delete.clicked.connect(self._remove)
            buttons.addWidget(delete)
        buttons.addStretch(1)
        cancel = QPushButton(core.t('Cancel'))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.go = QPushButton(core.t('Rename') if current else core.t('Create'))
        self.go.setDefault(True)
        self.go.clicked.connect(self._confirm)
        buttons.addWidget(self.go)
        layout.addLayout(buttons)
        QTimer.singleShot(60, self._focus)

    def _focus(self):
        self.name.setFocus()
        self.name.selectAll()

    def _say(self, text, colour='#ff7b6b'):
        self.note.setText(text)
        self.note.setStyleSheet(f'color: {colour};')

    def _confirm(self):
        try:
            if self.current:
                name = core.save_files.rename(core.USER, self.current, self.name.text())
            else:
                name = core.save_files.create(core.USER, self.name.text())
        except core.save_files.SaveFileError as error:
            self._say(str(error))
            return
        except OSError as error:
            self._say(core.t('The save file could not be changed: {error}', error=error))
            return
        self.result_name = name
        self.accept()

    def _remove(self):
        answer = QMessageBox.question(self, core.t('Delete save file'),
                                      core.t('Delete {name}? A copy is kept in user\\save_backups.',
                                             name=self.current))
        if answer != QMessageBox.Yes:
            return
        try:
            core.save_files.delete(core.USER, self.current)
        except (core.save_files.SaveFileError, OSError) as error:
            self._say(str(error))
            return
        self.result_name = core.save_files.MAIN
        self.accept()


class UpdateDialog(QDialog):
    """A newer LBPPC on GitHub (scripts/updater.py): what's new, and Update, which downloads it, checks its SHA-256,
    and lets the new version install itself once the launcher has closed."""

    def __init__(self, parent, release):
        super().__init__(parent)
        self.launcher, self.release = parent, release
        self.busy = False
        self.cancelled = False
        self.events = queue.Queue()
        self.setWindowTitle(core.t('LBPPC {version} is out!', version=release.version))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)
        title = QLabel(core.t('LBPPC {version} is out!', version=release.version))
        title.setObjectName('title')
        title.setStyleSheet('font-size: 20pt;')
        layout.addWidget(title)
        layout.addWidget(label(core.t('You have {version}. Updating takes a minute or two: LBPPC downloads it, '
                                      'closes, and opens again on the new version.', version=core.VERSION)))
        notes = plain_notes(release.notes)
        if notes:
            layout.addWidget(label(core.t("What's new"), 'section', False))
            body = QLabel(notes)
            body.setWordWrap(True)
            body.setObjectName('hint')
            layout.addWidget(body)
        self.note = label(core.t('Your saves, settings and controls are kept.'))
        layout.addWidget(self.note)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        news = QPushButton(core.t("What's new"))
        news.clicked.connect(lambda: webbrowser.open(release.page))
        self.later = QPushButton(core.t('Later'))
        self.later.clicked.connect(self.reject)
        self.go = QPushButton(core.t('Update'))
        self.go.setDefault(True)
        self.go.clicked.connect(self._start)
        buttons.addWidget(news)
        buttons.addStretch(1)
        buttons.addWidget(self.later)
        buttons.addWidget(self.go)
        layout.addLayout(buttons)
        self.pump = QTimer(self)
        self.pump.timeout.connect(self._poll)

    def _say(self, text, colour='#aab2c4'):
        self.note.setText(text)
        self.note.setStyleSheet(f'color: {colour};')

    def _start(self):
        if self.busy:
            return
        if self.launcher.process:
            self._say(core.t('Close the game first: the update replaces its files.'), '#f2a33a')
            return
        self.busy, self.cancelled = True, False
        self.go.setEnabled(False)
        self.later.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._say(core.t('Downloading LBPPC {version}...', version=self.release.version), '#f0f0e8')
        folder = core.DATA / 'out' / 'update'

        def work():
            try:
                path = core.updater.download(self.release, folder,
                                             lambda done, total: self.events.put(('progress', done, total)),
                                             lambda: self.cancelled)
                if not path:
                    self.events.put(('stopped',))
                    return
                self.events.put(('unpacking',))
                staging = core.updater.stage(path, folder / 'staging')
                self.events.put(('ready', staging))
            except (core.updater.UpdateError, OSError, core.zipfile_errors()) as error:
                self.events.put(('failed', str(error)))
        threading.Thread(target=work, daemon=True).start()
        self.pump.start(100)

    def _poll(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == 'progress':
                done, total = event[1], max(event[2], 1)
                self.progress.setValue(min(done * 100 // total, 100))
                self._say(core.t('Downloading LBPPC {version}... {done} of {total} MB', version=self.release.version,
                                 done=done >> 20, total=total >> 20), '#f0f0e8')
            elif kind == 'unpacking':
                self._say(core.t('Checked. Unpacking...'), '#f0f0e8')
            elif kind == 'ready':
                self.pump.stop()
                self._say(core.t('Installing: LBPPC closes and opens again in a moment.'), '#9ccc65')
                try:
                    core.updater.start_apply(event[1], core.ROOT)
                except OSError as error:
                    self._fail(core.t('The new version could not be started: {error}', error=error))
                    return
                QTimer.singleShot(300, self.launcher.close)
                self.accept()
                return
            elif kind in ('failed', 'stopped'):
                self._fail(event[1] if kind == 'failed' else core.t('Update cancelled.'))
                return
        if self.busy:
            self.pump.start(100)

    def _fail(self, message):
        self.busy = False
        self.pump.stop()
        self.go.setEnabled(True)
        self.later.setEnabled(True)
        self.progress.setVisible(False)
        self._say(message, '#ff7b6b')

    def closeEvent(self, event):
        if self.busy:
            self.cancelled = True
            self.busy = False
            self.pump.stop()
        event.accept()


class KeyButton(QPushButton):
    """A key cell: click to wait for the next key, right-click to clear it."""

    def __init__(self, page, output, slot):
        super().__init__()
        self.page, self.output, self.slot = page, output, slot
        self.setMinimumWidth(120)
        self.clicked.connect(lambda: page.start_capture(output, slot))

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton and not self.page.capturing:
            self.page.clear_key(self.output, self.slot)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if self.page.capturing == (self.output, self.slot):
            self.page.key_pressed(event)
            event.accept()
            return
        super().keyPressEvent(event)


# Qt's keys as input.ini's names (src/runtime_input_config.c: kKeyNames).
QT_KEYS = {
    Qt.Key_Escape: 'escape', Qt.Key_Backspace: 'backspace', Qt.Key_Delete: 'delete', Qt.Key_Insert: 'insert',
    Qt.Key_Home: 'home', Qt.Key_End: 'end', Qt.Key_PageUp: 'pgup', Qt.Key_PageDown: 'pgdown', Qt.Key_Tab: 'tab',
    Qt.Key_CapsLock: 'capslock', Qt.Key_Return: 'enter', Qt.Key_Enter: 'enter', Qt.Key_Space: 'space',
    Qt.Key_Shift: 'lshift', Qt.Key_Control: 'lctrl', Qt.Key_Alt: 'lalt', Qt.Key_Meta: 'lmeta',
    Qt.Key_Up: 'up', Qt.Key_Down: 'down', Qt.Key_Left: 'left', Qt.Key_Right: 'right',
    Qt.Key_Print: 'printscreen', Qt.Key_ScrollLock: 'scrolllock', Qt.Key_Pause: 'pausebreak',
    Qt.Key_QuoteLeft: 'grave', Qt.Key_Minus: 'minus', Qt.Key_Equal: 'equals',
    Qt.Key_BracketLeft: 'lbracket', Qt.Key_BracketRight: 'rbracket', Qt.Key_Backslash: 'backslash',
    Qt.Key_Semicolon: 'semicolon', Qt.Key_Apostrophe: 'apostrophe', Qt.Key_Comma: 'comma',
    Qt.Key_Period: 'period', Qt.Key_Slash: 'slash',
}
for _n in range(1, 13):
    QT_KEYS[getattr(Qt, f'Key_F{_n}')] = f'f{_n}'
for _n in range(10):
    QT_KEYS[getattr(Qt, f'Key_{_n}')] = str(_n)
QT_KEYPAD = {Qt.Key_Period: 'kpperiod', Qt.Key_Comma: 'kpcomma', Qt.Key_Slash: 'kpslash',
             Qt.Key_Asterisk: 'kpasterisk', Qt.Key_Minus: 'kpminus', Qt.Key_Plus: 'kpplus',
             Qt.Key_Equal: 'kpequals', Qt.Key_Enter: 'kpenter'}


def qt_key_name(event):
    """input.ini's name for a Qt key press, or None."""
    key = event.key()
    if event.modifiers() & Qt.KeypadModifier:
        text = event.text()
        if text.isdigit():
            return f'kp{text}'
        if key in QT_KEYPAD:
            return QT_KEYPAD[key]
    if key in QT_KEYS:
        return QT_KEYS[key]
    text = event.text().lower()
    if len(text) == 1 and text.isascii() and text.isalnum():
        return text
    return None


class ControlsPage(QWidget):
    """The Controls page: the keys, mouse buttons and controller buttons of each game button, written to input.ini
    at once (input_settings.py); F8 in the game reloads it."""

    def __init__(self, launcher):
        super().__init__()
        self.launcher = launcher
        self.model = core.input_settings.InputSettings.load(core.INPUT_INI)
        self.capturing = None
        self.key_buttons = {}
        self.pad_combos = {}
        self.pad_names = [None] + core.input_settings.PAD_BUTTONS + core.input_settings.PAD_HALF_AXES
        self.pad_labels = [core.t('None')] + [core.t(core.input_settings.PAD_LABELS[name]) for name in
                                              core.input_settings.PAD_BUTTONS + core.input_settings.PAD_HALF_AXES]
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(label(core.t('Click a key, then press the new key. Right-click a key to clear it.')))
        top.addStretch(1)
        reset = QPushButton(core.t('Reset to defaults'))
        reset.clicked.connect(self._reset)
        top.addWidget(reset)
        layout.addLayout(top)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.grid = QGridLayout(content)
        self.grid.setContentsMargins(8, 8, 8, 8)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(6)
        for column, title in enumerate((core.t('Game button'), core.t('Key or mouse'), core.t('Second key'),
                                        core.t('Controller'))):
            heading = QLabel(title)
            heading.setObjectName('section')
            self.grid.addWidget(heading, 0, column)
        for row, (output, title, hint) in enumerate(core.input_settings.ACTIONS, start=1):
            name = QLabel(core.t(title))
            name.setStyleSheet('font-weight: 600;')
            if hint:
                name.setToolTip(core.t(hint))
            self.grid.addWidget(name, row, 0)
            for slot in (0, 1):
                button = KeyButton(self, output, slot)
                self.key_buttons[(output, slot)] = button
                self.grid.addWidget(button, row, slot + 1)
            if output in core.input_settings.STICK_OUTPUTS:
                self.grid.addWidget(label(core.t('The stick itself')), row, 3)
            else:
                combo = QComboBox()
                combo.addItems(self.pad_labels)
                combo.currentIndexChanged.connect(lambda _index, o=output: self._pad_changed(o))
                self.pad_combos[output] = combo
                self.grid.addWidget(combo, row, 3)
        self.grid.setColumnStretch(0, 1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.status = label(core.t('Changes are saved as you make them.'))
        layout.addWidget(self.status)
        self.refresh()

    def refresh(self):
        for (output, slot), button in self.key_buttons.items():
            capturing = self.capturing == (output, slot)
            names = self.model.keys[output]
            has = slot < len(names)
            if slot == 1 and not names and not capturing:
                button.setText(core.t('Add'))
                button.setEnabled(False)
                continue
            button.setEnabled(True)
            button.setText(core.t('Press a key...') if capturing else
                           core.input_settings.key_label(names[slot]) if has else core.t('Add'))
            button.setStyleSheet('border-color: #f2a33a;' if capturing else '')
        for output, combo in self.pad_combos.items():
            current = self.model.pad[output][0] if self.model.pad[output] else None
            index = self.pad_names.index(current) if current in self.pad_names else 0
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)

    def _say(self, text, colour='#aab2c4'):
        self.status.setText(text)
        self.status.setStyleSheet(f'color: {colour};')

    def _save(self, done):
        try:
            self.model.save(core.INPUT_INI)
        except OSError as error:
            self._say(core.t('input.ini could not be saved: {error}', error=error), '#ff7b6b')
            return
        if self.launcher.process:
            done += ' ' + core.t('Press F8 in the game to use it now.')
        self._say(done, '#9ccc65')

    def start_capture(self, output, slot):
        if self.capturing:
            return
        self.capturing = (output, slot)
        self.refresh()
        button = self.key_buttons[(output, slot)]
        button.setFocus()
        button.grabKeyboard()

    def stop_capture(self):
        self.capturing = None
        for button in self.key_buttons.values():
            button.releaseKeyboard()
        self.refresh()

    def key_pressed(self, event):
        if event.key() == Qt.Key_Escape:
            self.stop_capture()
            self._say(core.t('Not changed.'))
            return
        name = qt_key_name(event)
        if not name:
            self._say(core.t('That key cannot be used. Press another, or Esc.'), '#ff7b6b')
            return
        self.assign(name)

    def clear_key(self, output, slot):
        if self.capturing:
            return
        self.model.clear_key(output, slot)
        self.refresh()
        self._save(core.t('{action}: key cleared.', action=self._label_of(output)))

    def assign(self, name):
        output, slot = self.capturing
        if name in core.input_settings.RESERVED_KEYS:
            self._say(core.t('{key} {what} in the game. Press another key, or Esc.',
                             key=core.input_settings.key_label(name),
                             what=core.t(core.input_settings.RESERVED_KEYS[name])), '#ff7b6b')
            return
        self.stop_capture()
        moved = self.model.bind_key(output, slot, name)
        self.refresh()
        label = core.input_settings.key_label(name)
        self._save(core.t('{key} moved from {old} to {new}.', key=label, old=self._label_of(moved),
                          new=self._label_of(output)) if moved else
                   core.t('{action}: {key}.', action=self._label_of(output), key=label))

    def _label_of(self, output):
        return core.t(next(label for o, label, _hint in core.input_settings.ACTIONS if o == output))

    def _pad_changed(self, output):
        combo = self.pad_combos[output]
        name = self.pad_names[combo.currentIndex()]
        self.model.set_pad(output, name)
        self._save(core.t('{action}: {button} on the controller.', action=self._label_of(output),
                          button=combo.currentText()))

    def _reset(self):
        if QMessageBox.question(self, core.t('Reset to defaults'),
                                core.t('Reset every key and controller button to the defaults?')) != QMessageBox.Yes:
            return
        self.model = core.input_settings.InputSettings()
        self.refresh()
        self._save(core.t('Controls reset to the defaults.'))


class DebugPage(QWidget):
    """The Debug page: the in-game performance overlay's switches (lbppc.ini, also changed by Insert in the game) and
    the diagnostics the game logs, passed to it on Play."""

    def __init__(self, launcher):
        super().__init__()
        self.launcher = launcher
        self.overlay = {key: core.ini_values().get(key, '1' if on else '0') == '1'
                        for key, _title, on in core.OVERLAY}
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        overlay_box = QGroupBox(core.t('Performance overlay'))
        overlay_grid = QGridLayout(overlay_box)
        overlay_grid.setSpacing(8)
        rows = (len(core.OVERLAY) + 1) // 2
        for index, (key, title, _on) in enumerate(core.OVERLAY):
            box = QCheckBox(core.t(title))
            box.setChecked(self.overlay[key])
            box.toggled.connect(lambda on, k=key: self._toggle_overlay(k, on))
            overlay_grid.addWidget(box, index % rows, index // rows)
        self.overlay_status = label(core.t('Shown in the top right of the game. Insert in the game changes these '
                                           'while you play.'))
        overlay_grid.addWidget(self.overlay_status, rows, 0, 1, 2)
        layout.addWidget(overlay_box)

        diagnostics_box = QGroupBox(core.t('Diagnostics'))
        diagnostics_layout = QVBoxLayout(diagnostics_box)
        diagnostics_layout.setSpacing(6)
        for key, _variable, title, hint, _on in core.DIAGNOSTICS:
            box = QCheckBox(core.t(title))
            box.setChecked(bool(self.launcher.settings['diagnostics'].get(key)))
            box.toggled.connect(lambda on, k=key: self._toggle_diagnostic(k, on))
            diagnostics_layout.addWidget(box)
            diagnostics_layout.addWidget(label(hint))
        self.diag_status = label(core.t('Used the next time you press Play. Logs go in the report from Make a '
                                        'report.'))
        diagnostics_layout.addWidget(self.diag_status)
        layout.addWidget(diagnostics_box)
        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _toggle_overlay(self, key, on):
        self.overlay[key] = on
        try:
            core.ini_set(key, '1' if on else '0')
        except OSError as error:
            self.overlay_status.setText(core.t('lbppc.ini could not be saved: {error}', error=error))
            self.overlay_status.setStyleSheet('color: #ff7b6b;')
            return
        text = core.t('Saved. Used the next time the game starts, or press Insert in the game.') if self.launcher.process \
            else core.t('Saved.')
        self.overlay_status.setText(text)
        self.overlay_status.setStyleSheet('color: #9ccc65;')

    def _toggle_diagnostic(self, key, on):
        self.launcher.settings['diagnostics'][key] = on
        core.save_settings(self.launcher.settings)
        text = core.t('Saved. Used the next time you press Play.') if self.launcher.process else core.t('Saved.')
        self.diag_status.setText(text)
        self.diag_status.setStyleSheet('color: #9ccc65;')


class FriendsPage(QWidget):
    """The player's shadNet friends: a name to send a request to, the requests to answer, the friends (online first),
    and the requests sent. shadNet takes one sign-in per account, so the list signs in only for a moment and never
    while the game runs."""

    REFRESH_MS = 60000
    RETRY_MS = 20000

    def __init__(self, launcher):
        super().__init__()
        self.launcher = launcher
        self.busy = self.paused = False
        self.friends = None
        self.signed_in_as = None
        self.confirm = None
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(self.REFRESH_MS)
        QTimer.singleShot(400, self.account_changed)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        row = QHBoxLayout()
        self.add_name = QLineEdit()
        self.add_name.setPlaceholderText(core.t('Add a friend by their online name.'))
        self.add_name.returnPressed.connect(self.send_request)
        add = QPushButton(core.t('Add'))
        add.clicked.connect(self.send_request)
        row.addWidget(self.add_name, 1)
        row.addWidget(add)
        layout.addLayout(row)
        self.status = label(core.t('Add a friend by their online name.'))
        layout.addWidget(self.status)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.scroll.setWidget(self.list_widget)
        layout.addWidget(self.scroll, 1)
        bottom = QVBoxLayout()
        bottom.addWidget(label(core.t('Find people to play with, get help and hear about updates:')))
        discord = QPushButton(core.t('Join the Discord'))
        discord.setStyleSheet('background: #5865f2; border-color: #7b87ff;')
        discord.clicked.connect(lambda: webbrowser.open(core.DISCORD))
        bottom.addWidget(discord, 0, Qt.AlignLeft)
        layout.addLayout(bottom)
        self.draw()

    TOKEN_MARKER = object()  # in account() when signed in through lbppc.com: run() swaps in a fresh token

    def account(self):
        settings = self.launcher.settings
        auth = self.launcher.auth
        try:
            server = core.online_accounts.server_endpoints(settings['server'])['shadnet']
            if auth.signed_in:
                # A marker, not the token: tokens change, the account doesn't (run() fetches the token).
                return server, auth.username or '', self.TOKEN_MARKER
            core.online_accounts.check_name_and_password(settings['online_name'], settings['password'])
        except core.online_accounts.AccountError:
            return None
        return server, settings['online_name'], settings['password']

    def account_changed(self, force=False):
        account = self.account()
        if not account:
            self.friends, self.signed_in_as = None, None
            self.draw()
            self.set_status(core.t('Add a friend by their online name.'))
            return
        if force or account != self.signed_in_as:
            self.reload()

    def tick(self):
        if self.account() and not self.paused:
            self.run(None, quiet=True)
        self.timer.start(self.REFRESH_MS)

    def game_running(self, running):
        self.paused = running
        if running:
            self.set_status(core.t('The list updates again when the game closes.'))
        self.draw()
        if not running:
            self.reload()

    def set_status(self, text, colour='#aab2c4'):
        self.status.setText(text)
        self.status.setStyleSheet(f'color: {colour};')

    def run(self, action, done_text=None, quiet=False):
        account = self.account()
        if self.busy or self.paused or not account:
            if not account and action:
                self.set_status(core.t('Create an account (Online, on the Game tab) to add friends.'), '#ff7b6b')
            return
        self.busy = True
        if not quiet:
            self.set_status(core.t('Working...') if action else core.t('Signing in...'))
        result = {}

        def work():
            client = f'LBPPC {core.VERSION} launcher'
            try:
                login = account
                if login[2] is self.TOKEN_MARKER:
                    login = (login[0], login[1], self.launcher.auth.game_password())
                if action:
                    with core.online_accounts.ShadnetSession(*login, client) as session:
                        action(session)
                with core.online_accounts.ShadnetSession(*login, client) as session:
                    result['friends'] = session.friends
            except (core.online_accounts.AccountError, core.lbppc_signin.SignInError) as error:
                result['error'] = str(error)
            except Exception as error:  # noqa: BLE001 - anything else must not leave the list stuck signing in
                result['error'] = core.t('Signing in failed: {error}', error=error or type(error).__name__)

        def finished():
            if thread.is_alive():
                QTimer.singleShot(150, finished)
                return
            self.busy = False
            if 'error' in result or 'friends' not in result:
                self.set_status(result.get('error', core.t('Signing in failed.')), '#ff7b6b')
                if not self.signed_in_as:
                    QTimer.singleShot(self.RETRY_MS, lambda: self.signed_in_as or self.run(None, quiet=True))
                return
            self.friends, self.signed_in_as = result['friends'], account
            self.draw()
            if done_text:
                self.set_status(done_text, '#9ccc65')
            elif not quiet:
                self.set_status(core.t('Signed in as {name}.', name=account[1]))

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        QTimer.singleShot(150, finished)

    def reload(self):
        self.run(None)

    def send_request(self):
        other = self.add_name.text().strip()
        account = self.account()
        if not re.fullmatch(core.online_accounts.NAME_PATTERN, other) or (
                account and other.lower() == account[1].lower()):
            self.set_status(core.t('Type another player\'s online name.'), '#ff7b6b')
            return
        self.add_name.clear()
        self.run(lambda session: session.add(other),
                 core.t('Request sent to {name}. They show as a friend once they accept.', name=other))

    def remove_friend(self, other):
        if self.confirm == other:
            self.confirm = None
            self.run(lambda session: session.remove(other), core.t('Removed {name}.', name=other))
        else:
            self.confirm = other
            self.draw()
            QTimer.singleShot(4000, self._clear_confirm)

    def _clear_confirm(self):
        if self.confirm:
            self.confirm = None
            self.draw()

    def draw(self):
        clear_layout(self.list_layout)
        if self.friends is None:
            message = core.t('Create an account (Online, on the Game tab) and your friends show here: requests to '
                             'answer, friends, and requests you sent.') if not self.account() else \
                core.t('Signing in...')
            self.list_layout.addWidget(label(message))
            self.list_layout.addStretch(1)
            return
        friends = self.friends
        rows = {'received': [(name, None) for name in friends.received],
                'friends': sorted(friends.friends, key=lambda f: (not f[1], f[0].lower())),
                'sent': [(name, None) for name in friends.sent]}
        sections = (('received', core.t('Friend requests')), ('friends', core.t('Friends')),
                    ('sent', core.t('Sent requests')))
        for key, title in sections:
            items = rows[key]
            if not items and key != 'friends':
                continue
            box = QGroupBox(f'{title}  ({len(items)})')
            inner = QVBoxLayout(box)
            inner.setSpacing(4)
            if not items:
                inner.addWidget(label(core.t('No friends yet. Type a friend\'s online name above and press Add; once '
                                             'they accept, they show here.')))
            for name, online in items:
                line = QHBoxLayout()
                name_label = QLabel(name)
                name_label.setStyleSheet('font-weight: 600;')
                line.addWidget(name_label)
                if key == 'friends':
                    state = QLabel(core.t('online') if online else core.t('offline'))
                    state.setStyleSheet(f'color: {"#9ccc65" if online else "#6d768c"}; font-size: 9pt;')
                    line.addWidget(state)
                line.addStretch(1)
                if key == 'received':
                    accept = QPushButton(core.t('Accept'))
                    accept.clicked.connect(lambda _checked=False, o=name: self.run(
                        lambda session: session.add(o), core.t('You and {name} are friends.', name=o)))
                    decline = QPushButton(core.t('Decline'))
                    decline.clicked.connect(lambda _checked=False, o=name: self.run(
                        lambda session: session.remove(o), core.t('Declined {name}.', name=o)))
                    line.addWidget(accept)
                    line.addWidget(decline)
                    for button in (accept, decline):
                        button.setEnabled(not self.paused)
                elif key == 'friends':
                    remove = QPushButton(core.t('Remove?') if self.confirm == name else core.t('Remove'))
                    remove.clicked.connect(lambda _checked=False, o=name: self.remove_friend(o))
                    remove.setEnabled(not self.paused)
                    line.addWidget(remove)
                else:
                    cancel = QPushButton(core.t('Cancel'))
                    cancel.clicked.connect(lambda _checked=False, o=name: self.run(
                        lambda session: session.remove(o), core.t('Cancelled the request to {name}.', name=o)))
                    cancel.setEnabled(not self.paused)
                    line.addWidget(cancel)
                inner.addLayout(line)
            self.list_layout.addWidget(box)
        self.list_layout.addStretch(1)


class LauncherWindow(QWidget):
    def __init__(self, settings, direct=False):
        super().__init__()
        self.settings = settings
        self.direct = direct
        self.process = None
        self.stopping = False
        self.lines = queue.Queue()
        self.scrub = None
        self.presence = None
        self._stored = ''
        self._loading = True
        self._loading_saves = False
        self.update_found = None
        self.update_shown = False
        # The lbppc.com sign-in (scripts/lbppc_signin.py): its refresh token lives in settings, DPAPI-locked on disk.
        self.auth = core.lbppc_signin.Account(load=lambda: self.settings.get('lbppc_signin'),
                                              save=self._save_signin)
        self._build()
        self._load()
        self._loading = False
        self._show_signin()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._pump)
        self.timer.start(100)
        self._after_update()
        if FROZEN or os.environ.get('LBPPC_UPDATE_CHECK') == '1':
            threading.Thread(target=self._check_update, daemon=True).start()
            self.update_timer = QTimer(self)
            self.update_timer.timeout.connect(self._offer_update)
            self.update_timer.start(1500)
        if direct:
            QTimer.singleShot(500, self._direct_play)

    def _build(self):
        self.setWindowTitle('LBPPC')
        self.setMinimumSize(820, 640)
        self.resize(980, 820)
        icon = core.ASSETS / 'logo.png'
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 14)
        outer.setSpacing(10)

        # Header
        header = QHBoxLayout()
        logo = QLabel()
        if icon.is_file():
            pixmap = QPixmap(str(icon))
            logo.setPixmap(pixmap.scaled(52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        header.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel('LBPPC')
        title.setObjectName('title')
        titles.addWidget(title)
        subtitle = QLabel(core.t('LittleBigPlanet 3 on PC'))
        subtitle.setObjectName('subtitle')
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        version = QLabel(f'v{core.VERSION}')
        version.setObjectName('version')
        header.addWidget(version, 0, Qt.AlignTop)
        outer.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._game_tab(), core.t('Game'))
        self.friends = FriendsPage(self)
        self.tabs.addTab(self.friends, core.t('Friends'))
        self.controls = ControlsPage(self)
        self.tabs.addTab(self.controls, core.t('Controls'))
        self.debug = DebugPage(self)
        self.tabs.addTab(self.debug, core.t('Debug'))
        outer.addWidget(self.tabs, 1)

        # Play bar and state
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.play_button = QPushButton(core.t('Play'))
        self.play_button.setObjectName('play')
        self.play_button.clicked.connect(self._play)
        self.stop_button = QPushButton(core.t('Stop'))
        self.stop_button.setObjectName('stop')
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        buttons.addWidget(self.play_button, 2)
        buttons.addWidget(self.stop_button, 1)
        outer.addLayout(buttons)
        self.status = QLabel()
        self.status.setObjectName('status')
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        # The game's output
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.log.setMinimumHeight(130)
        self.log.setVisible(False)
        outer.addWidget(self.log, 1)

    def _game_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        # Game folder
        game_box = QGroupBox(core.t('Game'))
        game_grid = QGridLayout(game_box)
        game_grid.setSpacing(8)
        self.game = QLineEdit()
        self.game.setPlaceholderText(core.t('Game folder (holding eboot.bin)'))
        self.game.returnPressed.connect(self._game_changed)
        game_grid.addWidget(self.game, 0, 0)
        browse = QPushButton(core.t('Browse...'))
        browse.clicked.connect(self._browse)
        game_grid.addWidget(browse, 0, 1)
        self.game_status = QLabel()
        self.game_status.setObjectName('status')
        self.game_status.setWordWrap(True)
        game_grid.addWidget(self.game_status, 1, 0, 1, 2)
        self.setup_button = QPushButton(core.t('Set up game files...'))
        self.setup_button.clicked.connect(self._open_setup)
        game_grid.addWidget(self.setup_button, 2, 0, 1, 2, Qt.AlignLeft)
        layout.addWidget(game_box)

        # Every graphics setting the Tk launcher offers
        settings_box = QGroupBox(core.t('Settings'))
        form = QFormLayout(settings_box)
        form.setSpacing(8)
        self.setting_combos = {}
        for key, title, values, hint in core.OPTIONS:
            combo = QComboBox()
            fill_combo(combo, values, self.settings.get(key))
            combo.currentIndexChanged.connect(lambda _index, k=key: self._setting_changed(k))
            self.setting_combos[key] = combo
            form.addRow(core.t(title), self._with_hint(combo, hint))
        layout.addWidget(settings_box)

        # Save file
        save_box = QGroupBox(core.t('Save file'))
        save_layout = QVBoxLayout(save_box)
        save_row = QHBoxLayout()
        self.save_file = QComboBox()
        self.save_file.currentIndexChanged.connect(self._save_file_changed)
        self.save_names = {}
        save_row.addWidget(self.save_file, 1)
        edit = QPushButton(core.t('Rename / delete'))
        edit.clicked.connect(self._edit_save_file)
        save_row.addWidget(edit)
        save_layout.addLayout(save_row)
        import_button = QPushButton(core.t('Import save...'))
        import_button.clicked.connect(self._import_save)
        save_layout.addWidget(import_button, 0, Qt.AlignLeft)
        save_layout.addWidget(label(core.t('Separate progress. Settings, DLC and your online account are shared.')))
        layout.addWidget(save_box)

        # Online
        online_box = QGroupBox(core.t('Online'))
        online_layout = QVBoxLayout(online_box)
        self.online = QCheckBox(core.t('Play online on the LBPPC server'))
        self.online.toggled.connect(self._online_changed)
        online_layout.addWidget(self.online)
        self.discord_presence = QCheckBox(core.t('Show what I\'m playing on Discord'))
        self.discord_presence.toggled.connect(self._presence_changed)
        online_layout.addWidget(self.discord_presence)
        account_form = QFormLayout()
        account_form.setSpacing(6)
        self.online_name = QLineEdit()
        self.online_name.editingFinished.connect(self._account_changed)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.editingFinished.connect(self._account_changed)
        account_form.addRow(core.t('Online name'), self.online_name)
        account_form.addRow(core.t('Password'), self.password)
        online_layout.addLayout(account_form)
        online_layout.addWidget(label(core.t('3 to 16 letters, digits, - or _, and a password of 8 or more '
                                             'characters. Only your friends can join your game.')))
        self.create_button = QPushButton(core.t('Create account'))
        self.create_button.clicked.connect(self._create_account)
        online_layout.addWidget(self.create_button, 0, Qt.AlignLeft)
        signin_row = QHBoxLayout()
        self.signin_button = QPushButton(core.t('Sign in with lbppc.com'))
        self.signin_button.clicked.connect(self._sign_in_browser)
        signin_row.addWidget(self.signin_button)
        self.signout_button = QPushButton(core.t('Sign out'))
        self.signout_button.clicked.connect(self._sign_out_browser)
        signin_row.addWidget(self.signout_button)
        signin_row.addStretch(1)
        online_layout.addLayout(signin_row)
        self.account_status = label(core.t('New here? Choose a name and password, then Create account.'))
        online_layout.addWidget(self.account_status)
        layout.addWidget(online_box)

        # Tools: the Tk launcher's links
        tools_box = QGroupBox(core.t('Tools'))
        tools_grid = QGridLayout(tools_box)
        tools_grid.setSpacing(8)
        self.show_log = QCheckBox(core.t('Show log'))
        self.show_log.toggled.connect(self._toggle_log)
        tools_grid.addWidget(self.show_log, 0, 0)
        for index, (text, action) in enumerate((
                (core.t('Open log folder'), self._open_log),
                (core.t('Log file'), self._open_log_file),
                (core.t('Saves folder'), self._open_saves),
                (core.t('Make a report'), self._make_report),
                (core.t('Desktop shortcut'), self._make_shortcut),
                ('Discord', lambda: webbrowser.open(core.DISCORD))), start=1):
            button = QPushButton(text)
            button.clicked.connect(action)
            tools_grid.addWidget(button, index // 3, index % 3)
        layout.addWidget(tools_box)
        layout.addStretch(1)
        scroll.setWidget(content)
        return scroll

    def _with_hint(self, widget, hint):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(widget)
        if hint:
            layout.addWidget(label(hint))
        return box

    def _load(self):
        self.game.setText(self.settings.get('game', ''))
        for key, combo in self.setting_combos.items():
            fill_combo(combo, next(values for k, _title, values, _hint in core.OPTIONS if k == key),
                       self.settings.get(key))
        self.online.setChecked(bool(self.settings.get('online')))
        self.discord_presence.setChecked(bool(self.settings.get('discord_presence', True)))
        self.online_name.setText(self.settings.get('online_name', ''))
        self.password.setText(self.settings.get('password', ''))
        self._fill_save_files()
        self._refresh()

    def _fill_save_files(self):
        current = self.settings.get('save_file') or core.save_files.MAIN
        names = core.save_files.names(core.USER)
        if current not in names:
            current = self.settings['save_file'] = core.save_files.MAIN
        shown = [core.t('Main') if n == core.save_files.MAIN else n for n in names]
        self._loading_saves = True
        self.save_file.clear()
        for name, shown_name in zip(names, shown):
            self.save_file.addItem(shown_name, name)
        self.save_file.addItem(core.t(self.NEW_SAVE), self.NEW_SAVE)
        self.save_names = dict(zip(shown, names))
        index = self.save_file.findData(current)
        self.save_file.setCurrentIndex(index if index >= 0 else 0)
        self._loading_saves = False

    NEW_SAVE = 'New save file...'

    def _save_file_changed(self):
        if self._loading_saves:
            return
        data = self.save_file.currentData()
        if data == self.NEW_SAVE:
            self._fill_save_files()
            if self.process:
                self._set_status(core.t('Close the game before changing save files'), 'warn')
            else:
                self._open_save_dialog(None)
            return
        if self.process and data != self.settings.get('save_file'):
            self._set_status(core.t('Close the game before changing save files'), 'warn')
            self._fill_save_files()
            return
        self.settings['save_file'] = data or core.save_files.MAIN
        self._save()
        self._fill_save_files()

    def _open_save_dialog(self, current):
        dialog = SaveFileDialog(self, current)
        if dialog.exec() == QDialog.Accepted and dialog.result_name:
            self.settings['save_file'] = dialog.result_name
            self._save()
            self._fill_save_files()
            self._set_status(core.t('Save file: {name}', name=core.t('Main') if dialog.result_name ==
                                    core.save_files.MAIN else dialog.result_name), 'ready')

    def _edit_save_file(self):
        current = self.settings.get('save_file') or core.save_files.MAIN
        if self.process:
            self._set_status(core.t('Close the game before changing save files'), 'warn')
        elif current == core.save_files.MAIN:
            self._set_status(core.t('The Main save file can\'t be renamed or deleted.'), 'warn')
        else:
            self._open_save_dialog(current)

    def _import_save(self):
        if self.process:
            self._set_status(core.t('Close the game before importing a save'), 'warn')
            return
        folder = QFileDialog.getExistingDirectory(self, core.t('Your decrypted LittleBigPlanet 3 save (or a folder '
                                                               'of them)'), str(Path.home()))
        if not folder:
            return
        try:
            saves = core.save_import.find_saves(folder)
        except core.save_import.ImportError_ as error:
            QMessageBox.warning(self, core.t('Import save'), str(error))
            return
        what = core.save_import.describe(saves)
        if QMessageBox.question(self, core.t('Import save'),
                                core.t('Import {what} from this folder?\n\nYour current LBPPC save of the same kind '
                                       'is kept first, in user\\save_backups.', what=what)) != QMessageBox.Yes:
            return
        try:
            game_root = core.game_setup.Folder(self.settings['game']).root if self.settings.get('game') else None
            chosen = self.settings.get('save_file') or core.save_files.MAIN
            title = core.save_import.game_title(game_root, self.settings.get('update') or None)
            backup = core.save_import.import_saves(
                saves, core.USER, title, None if chosen == core.save_files.MAIN else core.save_files.folder(
                    core.USER, chosen))
        except OSError as error:
            self._set_status(core.t('The save could not be imported: {error}', error=error), 'error')
            return
        self._set_status(core.t('Save imported: {what}', what=what) + (
            core.t(' (the old one is in {folder})', folder=backup.name) if backup else ''), 'ready')

    def _save(self):
        self.settings['game'] = self.game.text().strip()
        for key, combo in self.setting_combos.items():
            self.settings[key] = combo.currentData()
        self.settings['online'] = self.online.isChecked()
        self.settings['discord_presence'] = self.discord_presence.isChecked()
        self.settings['online_name'] = self.online_name.text().strip()
        self.settings['password'] = self.password.text()
        core.save_settings(self.settings)
        self._stored = self.settings['game']

    def _set_status(self, text, state):
        self.status.setText(text)
        self.status.setProperty('state', state)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _refresh(self):
        state, message = core.check_game(self.game.text().strip(), self.settings.get('update'))
        colour = 'ready' if state == 'ready' else 'warn' if state == 'warn' else 'setup' if state == 'setup' \
            else 'missing'
        self.game_status.setText(message)
        self.game_status.setProperty('state', colour)
        self.game_status.style().unpolish(self.game_status)
        self.game_status.style().polish(self.game_status)
        self.play_button.setEnabled(state in ('ready', 'warn') and self.process is None)
        self.setup_button.setEnabled(self.process is None)
        if not self._loading and self.game.text().strip() != self._stored:
            self._save()
        return state

    def _browse(self):
        if self.process:
            return
        start = self.game.text().strip() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, core.t('Game folder (holding eboot.bin)'), start)
        if not folder:
            return
        root = core.game_setup.find_root(folder)
        path = str(root) if root and root.parent != root else folder
        self.game.setText(path)
        if path != self.settings.get('game'):
            self.settings['update'] = ''
        self._save()
        self._refresh()

    def _game_changed(self):
        if self.process:
            return
        self._save()
        self._refresh()

    def _setting_changed(self, key):
        if self._loading:
            return
        self._save()
        if key == 'language' and self._language_differs():
            if self.process:
                self._set_status(core.t('The launcher changes language the next time it starts.'), 'busy')
            else:
                self._restart()

    def _language_differs(self):
        language = self.settings.get('language', 'auto')
        if language not in {code for _name, code in core.LANGUAGES}:
            language = 'auto'
        game_language = core.windows_language() if language == 'auto' else language
        return core.i18n.LOCALES.get(game_language, 'en') != core.i18n.locale

    def _restart(self):
        try:
            subprocess.Popen(self._launcher_command(), cwd=core.ROOT, close_fds=True, env=core.host_environment())
        except OSError:
            self._set_status(core.t('The launcher changes language the next time it starts.'), 'busy')
            return
        QTimer.singleShot(200, self.close)

    @staticmethod
    def _launcher_command():
        if core.APPIMAGE:
            return [str(core.APPIMAGE)]
        return [sys.executable] if FROZEN else [sys.executable, str(Path(__file__).resolve())]

    def _online_changed(self, _checked):
        if self._loading:
            return
        self._save()
        self.friends.account_changed()

    def _presence_changed(self, _checked):
        if not self._loading:
            self._save()

    def _account_changed(self):
        if self._loading:
            return
        self._save()
        self.friends.account_changed()

    def _save_signin(self, value):
        self.settings['lbppc_signin'] = value
        core.save_settings(self.settings)

    def _show_signin(self):
        """The Online box as the sign-in stands: signed in through lbppc.com, the name is that account's and the
        name and password fields aren't used."""
        signed_in = self.auth.signed_in
        for entry in (self.online_name, self.password):
            entry.setEnabled(not signed_in)
        self.create_button.setEnabled(not signed_in)
        self.signin_button.setEnabled(not signed_in)
        self.signout_button.setEnabled(signed_in)
        if signed_in:
            self.online_name.setText(self.auth.username or '')
            self.password.setText('')
            self._set_account_status(
                core.t('Signed in through lbppc.com as {name}.', name=self.auth.username or '?'), '#9ccc65')

    def _sign_in_browser(self):
        """Sign in through lbppc.com in the browser, off the window's thread (it waits for the browser)."""
        self.signin_button.setEnabled(False)
        self.create_button.setEnabled(False)
        self._set_account_status(core.t('Finish signing in in your browser...'))
        result = {}

        def work():
            try:
                result['tokens'] = core.lbppc_signin.browser_sign_in(webbrowser.open)
            except core.lbppc_signin.SignInError as error:
                result['error'] = str(error)
            except Exception as error:  # noqa: BLE001 - the buttons must come back whatever happens
                result['error'] = core.t('Signing in failed: {error}', error=error or type(error).__name__)

        def finished():
            if thread.is_alive():
                QTimer.singleShot(250, finished)
                return
            if 'tokens' in result:
                self.auth.signed_in_with(result['tokens'])
                self.settings['online_name'] = self.auth.username or ''
                self.settings['password'] = ''
                if not self.online.isChecked():
                    self.online.setChecked(True)
                self._show_signin()
                self._save()
                self.friends.account_changed(force=True)
            else:
                self._show_signin()
                self._set_account_status(result.get('error') or core.t('Signing in failed.'), '#ff7b6b')

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        QTimer.singleShot(250, finished)

    def _sign_out_browser(self):
        if not self.auth.signed_in:
            return
        threading.Thread(target=self.auth.sign_out, daemon=True).start()
        self.settings['lbppc_signin'] = None
        self.online_name.setText('')
        self._show_signin()
        self._save()
        self._set_account_status(core.t('Signed out.'))
        self.friends.account_changed(force=True)

    def _create_account(self):
        self._save()
        values = self.settings
        try:
            core.online_accounts.check_name_and_password(values['online_name'], values['password'])
            servers = core.online_accounts.server_endpoints(values['server'])
        except core.online_accounts.AccountError as error:
            self._set_account_status(str(error), '#ff7b6b')
            return
        self._set_account_status(core.t('Creating the account...'))
        result = {}

        def work():
            try:
                core.online_accounts.create_account(servers['shadnet'], servers['refresh'], values['online_name'],
                                                    values['password'])
                result['ok'] = core.t('Account {name} created: press Play.', name=values['online_name'])
            except core.online_accounts.AccountError as error:
                result['error'] = str(error)

        def finished():
            if thread.is_alive():
                QTimer.singleShot(200, finished)
                return
            ok = 'ok' in result
            self._set_account_status(result.get('ok') or result.get('error'), '#9ccc65' if ok else '#ff7b6b')
            if ok and not self.online.isChecked():
                self.online.setChecked(True)
            if ok:
                self.friends.account_changed(force=True)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        QTimer.singleShot(200, finished)

    def _set_account_status(self, text, colour='#aab2c4'):
        self.account_status.setText(text)
        self.account_status.setStyleSheet(f'color: {colour};')

    def _toggle_log(self, shown):
        self.log.setVisible(shown)

    def _show_log(self):
        if not self.show_log.isChecked():
            self.show_log.setChecked(True)

    def _open_log(self):
        folder = core.DATA / 'out' / 'logs'
        folder.mkdir(parents=True, exist_ok=True)
        try:
            core.open_path(folder)
        except OSError as error:
            self._set_status(core.t('Can\'t open the log folder: {error}', error=error), 'error')

    def _open_log_file(self):
        try:
            core.open_path(core.LOG)
        except OSError as error:
            self._set_status(core.t('Can\'t open the log file: {error}', error=error), 'error')

    def _open_saves(self):
        try:
            core.open_path(core.USER)
        except OSError as error:
            self._set_status(core.t('Can\'t open the saves folder: {error}', error=error), 'error')

    def _make_report(self):
        try:
            path = core.make_report(self.friends.friends)
        except OSError as error:
            self._set_status(core.t('The report could not be made: {error}', error=error), 'error')
            return
        self._set_status(core.t('Report saved: {file}', file=path.name), 'ready')
        try:
            core.open_path(path.parent)
        except OSError:
            pass

    def _make_shortcut(self):
        try:
            if os.name == 'nt':
                self._make_windows_shortcut()
            else:
                self._make_linux_shortcut()
        except (OSError, subprocess.SubprocessError) as error:
            self._set_status(core.t('The shortcut could not be made: {error}', error=error), 'error')
            return
        self._set_status(core.t('Desktop shortcut made: it starts the game without the launcher'), 'ready')

    def _make_windows_shortcut(self):
        if FROZEN:
            target, arguments = Path(sys.executable), '--play'
        else:
            exe = Path(sys.executable)
            target = exe.with_name('pythonw.exe') if exe.with_name('pythonw.exe').is_file() else exe
            arguments = f'"{Path(__file__).resolve()}" --play'
        icon = core.ASSETS / 'lbppc.ico'
        script = ('$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LBPPC_LNK);'
                  '$s.TargetPath=$env:LBPPC_TARGET;$s.Arguments=$env:LBPPC_ARGS;$s.WorkingDirectory=$env:LBPPC_DIR;'
                  'if($env:LBPPC_ICON){$s.IconLocation=$env:LBPPC_ICON};$s.Description=\"LBPPC\";$s.Save()')
        desktop = subprocess.run(['powershell', '-NoProfile', '-Command', '[Environment]::GetFolderPath("Desktop")'],
                                 capture_output=True, text=True, timeout=20,
                                 creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        link = Path(desktop) / 'LBPPC (direct).lnk'
        env = dict(os.environ, LBPPC_LNK=str(link), LBPPC_TARGET=str(target), LBPPC_ARGS=arguments,
                   LBPPC_DIR=str(core.ROOT), LBPPC_ICON=str(icon) if icon.is_file() else '')
        done = subprocess.run(['powershell', '-NoProfile', '-Command', script], env=env, capture_output=True,
                              text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        if done.returncode != 0 or not link.is_file():
            raise OSError(done.stderr.strip() or 'PowerShell could not save it')

    def _make_linux_shortcut(self):
        if core.APPIMAGE:
            command = [str(core.APPIMAGE), '--play']
        else:
            command = [sys.executable, '--play'] if FROZEN else [sys.executable, str(Path(__file__).resolve()), '--play']

        def quoted(word):
            return '"' + re.sub(r'(["`$\\])', r'\\\1', word) + '"'
        icon = core.ASSETS / 'logo.png'
        entry = ('[Desktop Entry]\nType=Application\nName=LBPPC (direct)\n'
                 'Comment=LittleBigPlanet 3 on PC, straight into the game\n'
                 f'Exec={" ".join(quoted(word) for word in command)}\n'
                 'Icon=lbppc\nTerminal=false\nCategories=Game;\n')
        data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
        if icon.is_file():
            icons = data_home / 'icons' / 'hicolor' / '256x256' / 'apps'
            icons.mkdir(parents=True, exist_ok=True)
            icons.joinpath('lbppc.png').write_bytes(icon.read_bytes())
        places = [data_home / 'applications']
        desktop = subprocess.run(['xdg-user-dir', 'DESKTOP'], capture_output=True, text=True, timeout=10,
                                 env=core.host_environment()).stdout.strip() if core.shutil_which('xdg-user-dir') else ''
        desktop = Path(desktop) if desktop else Path.home() / 'Desktop'
        if desktop.is_dir() and desktop != Path.home():
            places.append(desktop)
        for place in places:
            place.mkdir(parents=True, exist_ok=True)
            link = place / 'lbppc-direct.desktop'
            link.write_text(entry, encoding='utf-8')
            link.chmod(0o755)

    def _open_setup(self):
        if self.process:
            self._set_status(core.t('Stop the game before setting up its files'), 'warn')
            return
        self._save()
        dialog = SetupDialog(self, self.settings)
        if dialog.exec() == QDialog.Accepted:
            self.settings['game'] = dialog.game_root
            self.settings['update'] = dialog.update_root
            self.game.setText(dialog.game_root)
            self._save()
            self._refresh()

    def _start_presence(self):
        app_id = self.settings.get('discord_app_id') or core.DISCORD_APP_ID
        if not (app_id and self.settings.get('discord_presence', True)):
            return
        presence = core.discord_presence.Presence(app_id)

        def work():
            if presence.connect():
                online = bool(self.settings.get('online'))
                presence.set('LittleBigPlanet 3', core.t('Online on LBPPC') if online else core.t('Playing offline'),
                             button=(core.t('Get LBPPC'), 'https://lbppc.com'))
                self.presence = presence
        threading.Thread(target=work, daemon=True).start()

    def _stop_presence(self):
        presence, self.presence = self.presence, None
        if presence:
            threading.Thread(target=lambda: (presence.clear(), presence.close()), daemon=True).start()

    def _play(self):
        if self.process:
            return
        if self._refresh() not in ('ready', 'warn'):
            self._show_log()
            return
        if self.friends.busy:
            self._set_status(core.t('Starting...'), 'busy')
            QTimer.singleShot(150, self._play)
            return
        self._save()
        warnings = []
        try:
            env, self.scrub = core.game_environment(self.settings, self.friends.friends, warn=warnings.append,
                                                    auth=self.auth)
        except (core.online_accounts.AccountError, core.lbppc_signin.SignInError) as error:
            if not self.auth.signed_in:
                self._show_signin()
            self._set_status(core.t('Online: {error}', error=error), 'error')
            return
        except (OSError, ValueError) as error:
            self._set_status(core.t('Could not start: {error}', error=error), 'error')
            return
        dlss_files = [name if name != 'lbppc_dlss.dll' or (core.BIN / name).is_file() else 'bbport_dlss.dll'
                      for name in core.DLSS_FILES]
        if self.settings['frame_gen'] == 'dlss' and not all((core.BIN / name).is_file() for name in dlss_files):
            warnings.append(core.t('Launcher: the DLSS files are missing next to the game; frame generation uses '
                                   'FSR 3.1.'))
        self.lines = queue.Queue()
        self.log.clear()
        for line in warnings:
            self.log.appendPlainText(line)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        command = [sys.executable, '--script'] if FROZEN else [sys.executable]
        update = self.settings.get('update')
        try:
            self.process = subprocess.Popen(
                command + [str(core.ROOT / 'run.py'), self.settings['game'],
                           *(['--update', update] if update else [])], cwd=core.ROOT, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags,
                start_new_session=os.name != 'nt')
        except OSError as error:
            self.process = None
            self._set_status(core.t('Could not start: {error}', error=error), 'error')
            return
        threading.Thread(target=self._read_output, args=(self.process,), daemon=True).start()
        self.stopping = False
        self.play_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._set_status(core.t('Starting...'), 'busy')
        self.friends.game_running(True)
        self._start_presence()

    def _read_output(self, process):
        for raw in process.stdout:
            self.lines.put(self.scrub(raw.decode('utf-8', 'replace').rstrip('\r\n')))
        self.lines.put((process.wait(),))

    def _pump(self):
        shown = []
        try:
            while len(shown) < 400:
                item = self.lines.get_nowait()
                if isinstance(item, tuple):
                    self._finished(item[0])
                    break
                shown.append(item)
                progress = core.SHADER_PROGRESS.match(item)
                if progress:
                    done, total = int(progress.group(2)), max(int(progress.group(3)), 1)
                    percent = done * 50 // total + (50 if progress.group(1) == 'building' else 0)
                    self._set_status(core.t('Preparing shaders ({percent}%)', percent=min(percent, 100)), 'busy')
                elif 'Frame rate' in item or 'Pipeline cache: preload took' in item:
                    self._set_status(core.t('Running'), 'ready')
        except queue.Empty:
            pass
        if shown:
            self.log.appendPlainText('\n'.join(shown))
            bar = self.log.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _finished(self, code):
        stopped, self.stopping = self.stopping, False
        self.process = None
        self.stop_button.setEnabled(False)
        self.friends.game_running(False)
        self._stop_presence()
        if stopped:
            self._set_status(core.t('Stopped'), 'busy')
        elif code == 0:
            self._set_status(core.t('Closed'), 'busy')
        else:
            self._set_status(core.t('Ended with an error: see the log'), 'error')
            self._show_log()
        self._refresh()
        if self.direct and (stopped or code == 0):
            self.close()

    def _stop(self):
        if self.process and not self.stopping:
            self.stopping = True
            self.stop_button.setEnabled(False)
            self._set_status(core.t('Stopping...'), 'busy')
            threading.Thread(target=core.end_process, args=(self.process,), daemon=True).start()

    def _direct_play(self):
        if self._refresh() in ('ready', 'warn'):
            self._play()

    def _check_update(self):
        try:
            self.update_found = core.updater.check(core.VERSION) or False
        except core.updater.UpdateError:
            self.update_found = False

    def _offer_update(self):
        if self.direct:
            self.update_timer.stop()  # --play: the update is offered the next time the launcher is opened
            return
        if self.update_found is None:
            return
        self.update_timer.stop()
        if self.update_found and self.process is None and not self.update_shown:
            self.update_shown = True
            UpdateDialog(self, self.update_found).exec()

    def _after_update(self):
        if FROZEN:
            threading.Thread(target=core.updater.clean_up, args=(core.DATA,), daemon=True).start()
        if os.environ.pop('LBPPC_UPDATED', None):
            self._set_status(core.t('Updated to LBPPC {version}', version=core.VERSION), 'ready')
        failed = os.environ.pop('LBPPC_UPDATE_FAILED', None)
        if failed:
            self._set_status(core.t('The update failed; still on {version}', version=core.VERSION), 'error')
            self.log.appendPlainText(core.t('Launcher: the update failed: {error}', error=failed))

    def closeEvent(self, event):
        self._stop_presence()
        if self.process:
            core.end_process(self.process)
        try:
            self._save()
        except Exception:  # noqa: BLE001 - closing must not fail
            pass
        event.accept()


def main():
    # Native Wayland first, then X11 (XWayland) on a desktop without it.
    if sys.platform.startswith('linux') and os.environ.get('WAYLAND_DISPLAY') and not os.environ.get('QT_QPA_PLATFORM'):
        os.environ['QT_QPA_PLATFORM'] = 'wayland;xcb'
    app = QApplication(sys.argv[:1])
    app.setApplicationName('LBPPC')
    app.setApplicationDisplayName('LBPPC')
    app.setDesktopFileName('lbppc')
    app.setStyle('Fusion')
    settings = core.load_settings()
    language = settings.get('language', 'auto')
    core.i18n.set_language(core.windows_language() if language == 'auto' else language)
    icon = core.ASSETS / 'logo.png'
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    font = app.font()
    font.setPointSizeF(10.5)
    app.setFont(font)
    app.setStyleSheet(STYLE)
    window = LauncherWindow(settings, direct='--play' in sys.argv[1:])
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
