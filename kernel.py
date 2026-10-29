"""kernel - Windows 11 debloat & optimize tool. Build with build.bat."""
import ctypes
import html
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time

from PySide6.QtCore import (
    Property, QEasingCurve, QEvent, QObject, QParallelAnimationGroup, QPoint, QPointF,
    QPropertyAnimation, QRect, QRectF, Qt, QTimer, QUrl, Signal, QSize,
)
from PySide6.QtGui import (
    QBrush, QColor, QDesktopServices, QFont, QFontDatabase, QGradient, QFontMetrics, QIcon, QImage, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QGraphicsBlurEffect, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QComboBox, QGridLayout, QGraphicsPixmapItem, QGraphicsScene, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSlider, QStackedWidget, QSizePolicy, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

import apps
import display_controls
import specs
import storage
from jobs import ACTIVE, Job, JobManager
from tweaks import TASKS, ONE_SHOT, IRREVERSIBLE, is_active, revert_tweak

BG, BORDER, TEXT, MUTED = "#000000", "#1a1a1a", "#ffffff", "#7a7a7a"
ROW_ACTION_BUTTON_WIDTH = 96
CATEGORY_COLORS = {"Apps": "#5b9cff", "Games": "#b57bff", "Documents & files": "#4fd08a",
                   "System": "#ffb454", "Other": "#8a8a8a"}

QSS = f"""
* {{ font-family: 'Segoe UI Variable Text', 'Segoe UI'; color: {TEXT}; }}
QWidget#window, QWidget#content {{ background: {BG}; }}
QScrollArea {{ border: none; background: {BG}; }}
QLabel {{ background: transparent; }}
#muted {{ color: {MUTED}; font-size: 14px; }}
#h1 {{ font-size: 28px; font-weight: 500; }}
#title {{ font-size: 30px; font-weight: 500; padding-left: 13px; }}
#section {{ color: {MUTED}; font-size: 14px; padding-left: 13px; }}
#sidebar {{ background: {BG}; border-right: 1px solid {BORDER}; }}
#footer {{ background: {BG}; border-top: 1px solid {BORDER}; }}
QLineEdit#search {{ background: #101010; border: 1px solid #222222; border-radius: 12px;
                   padding: 13px 16px; font-size: 16px; selection-background-color: #444444; }}
QLineEdit#search:focus {{ border-color: #4a4a4a; }}
#row {{ background: transparent; border: none; border-radius: 14px; }}
QPlainTextEdit {{ background: transparent; border: 1px solid {BORDER}; border-radius: 12px; padding: 12px;
                 font-family: 'Cascadia Mono', Consolas; font-size: 14px; color: #bdbdbd; }}
#metric {{ background: #080808; border: 1px solid {BORDER}; border-radius: 12px; }}
#metricValue {{ font-size: 22px; font-weight: 500; }}
#attention {{ background: #100e08; border: 1px solid #55421c; border-radius: 14px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #262626; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #363636; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QComboBox {{ background: #0f0f0f; border: 1px solid #222222; border-radius: 11px; padding: 10px 40px 10px 18px;
            font-size: 15px; min-width: 120px; }}
QComboBox:hover {{ background: #1a1a1a; }}
QComboBox:on {{ border-color: #4a4a4a; }}
QComboBox::drop-down {{ border: none; width: 34px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{ background: #101010; border: 1px solid #2a2a2a; border-radius: 10px; padding: 4px;
                              outline: none; selection-background-color: #242424; }}
QComboBox QAbstractItemView::item {{ min-height: 34px; padding-left: 12px; border-radius: 8px; }}
QComboBox QAbstractItemView::item:selected {{ background: #242424; }}
"""

TWEAKS, APPS, GAMING, STORAGE, SPECS, ACTIVITY, SETTINGS = range(7)
GLYPH = {"storage": "\uEDA2", "tweaks": "\uE9E9", "apps": "\uF0E2", "gaming": "\uE7FC", "specs": "\uE770", "downloads": "\uE896", "activity": "\uE9D9",
         "settings": "\uE713"}


def glyph_icon(ch, size=20, on_color=TEXT):
    """Segoe Fluent Icons glyph as a QIcon: muted normally, bright when checked or hovered."""
    icon = QIcon()
    variants = ((QIcon.Normal, QIcon.Off, TEXT), (QIcon.Normal, QIcon.On, on_color),
                (QIcon.Active, QIcon.Off, TEXT), (QIcon.Active, QIcon.On, on_color))
    for mode, state, c in variants:
        pm = QPixmap(size * 2, size * 2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.TextAntialiasing)
        f = QFont("Segoe Fluent Icons")
        f.setPixelSize(size * 2 - 8)
        p.setFont(f)
        p.setPen(QColor(c))
        p.drawText(pm.rect(), Qt.AlignCenter, ch)
        p.end()
        icon.addPixmap(pm, mode, state)
    return icon


def total_fraction(total, free):
    return (total - free) / total if total else 0.0


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    args = "" if getattr(sys, "frozen", False) else f'"{os.path.abspath(__file__)}"'
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, None, 1)


class Config:
    def __init__(self):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        self.dir = os.path.join(base, "kernel")
        self.path = os.path.join(self.dir, "settings.json")
        self.toggles = {}
        self.applied = {}
        self.smooth_scroll = True
        self.cpu_temp_mode = "average"
        self.nvidia_gamma = 100
        self.nvidia_contrast = 50
        self.win_size = None
        self.download_dir = os.path.join(os.path.expanduser("~"), "Downloads", "kernel")
        self.load(self.path)

    def load(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return False
        if isinstance(d.get("toggles"), dict):
            self.toggles = {k: bool(v) for k, v in d["toggles"].items()}
        else:
            self.toggles = {}
        if isinstance(d.get("applied"), dict):
            self.applied = {k: bool(v) for k, v in d["applied"].items()}
        else:
            self.applied = {}
        if isinstance(d.get("download_dir"), str) and d["download_dir"]:
            self.download_dir = d["download_dir"]
        if isinstance(d.get("smooth_scroll"), bool):
            self.smooth_scroll = d["smooth_scroll"]
        if d.get("cpu_temp_mode") in ("average", "per_core"):
            self.cpu_temp_mode = d["cpu_temp_mode"]
        if type(d.get("nvidia_gamma")) is int and 40 <= d["nvidia_gamma"] <= 280:
            self.nvidia_gamma = d["nvidia_gamma"]
        if type(d.get("nvidia_contrast")) is int and 0 <= d["nvidia_contrast"] <= 100:
            self.nvidia_contrast = d["nvidia_contrast"]
        ws = d.get("window")
        if isinstance(ws, list) and len(ws) == 2 and all(isinstance(x, int) for x in ws):
            self.win_size = ws
        return True

    def save(self, path=None):
        path = path or self.path
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"toggles": self.toggles, "applied": self.applied,
                           "download_dir": self.download_dir,
                           "smooth_scroll": self.smooth_scroll,
                           "cpu_temp_mode": self.cpu_temp_mode,
                           "nvidia_gamma": self.nvidia_gamma,
                           "nvidia_contrast": self.nvidia_contrast,
                           "window": self.win_size}, f, indent=2)
            return True
        except OSError:
            return False


# ---------- small widgets ----------

def lerp(a, b, t):
    return a + (b - a) * t


class AnimButton(QPushButton):
    """Base for custom-painted buttons: animated hover and a small press 'squish'."""

    def __init__(self, text=""):
        super().__init__(text)
        self._hover = 0.0
        self._press = 0.0
        self._cursor_pos = QPointF()
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.a_hover = QPropertyAnimation(self, b"hover", self)
        self.a_press = QPropertyAnimation(self, b"press", self)
        self.a_cursor = QPropertyAnimation(self, b"cursorPosition", self)
        self.a_cursor.setDuration(100)
        self.a_cursor.setEasingCurve(QEasingCurve.OutCubic)
        self.pressed.connect(lambda: self.go(self.a_press, 1.0, 120, QEasingCurve.OutCubic))
        self.released.connect(lambda: self.go(self.a_press, 0.0, 280, QEasingCurve.OutCubic))

    def go(self, anim, end, ms, curve=QEasingCurve.OutCubic):
        anim.stop()
        anim.setDuration(ms)
        anim.setEasingCurve(curve)
        anim.setStartValue(self.property(bytes(anim.propertyName()).decode()))
        anim.setEndValue(end)
        anim.start()

    def get_hover(self):
        return self._hover

    def set_hover(self, v):
        self._hover = v
        self.update()

    def get_press(self):
        return self._press

    def set_press(self, v):
        self._press = v
        self.update()

    def get_cursor_position(self):
        return self._cursor_pos

    def set_cursor_position(self, pos):
        self._cursor_pos = pos
        self.update()

    hover = Property(float, get_hover, set_hover)
    press = Property(float, get_press, set_press)
    cursorPosition = Property(QPointF, get_cursor_position, set_cursor_position)

    def enterEvent(self, e):
        self.snap_cursor(e.position())
        self.go(self.a_hover, 1.0, 140)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.go(self.a_hover, 0.0, 200)
        super().leaveEvent(e)

    def mouseMoveEvent(self, e):
        self.go(self.a_cursor, QPointF(e.position()), 55, QEasingCurve.OutQuad)
        super().mouseMoveEvent(e)

    def snap_cursor(self, pos):
        self.a_cursor.stop()
        self.set_cursor_position(QPointF(pos))

    def squish(self, p, strength=0.035):
        c = QRectF(self.rect()).center()
        s = 1.0 - strength * max(self._press, 0.0)
        p.translate(c)
        p.scale(s, s)
        p.translate(-c)


class LogView(QPlainTextEdit):
    """Log box with Copy and Clear buttons floating in its bottom-left corner."""

    def __init__(self, clearable=True):
        super().__init__()
        self.copy_btn = Button("Copy")
        self.clear_btn = Button("Clear")
        for b in (self.copy_btn, self.clear_btn):
            b.setParent(self)
            b.setCursor(Qt.PointingHandCursor)
            b.show()
        self.clear_btn.setVisible(clearable)
        self.copy_label = "Copy"
        self.copy_btn.clicked.connect(self.copy_all)
        self.clear_btn.clicked.connect(self.clear)

    def flash_copied(self):
        self.copy_btn.setText("Copied")
        QTimer.singleShot(1200, lambda: self.copy_btn.setText(self.copy_label))

    def copy_all(self):
        QApplication.clipboard().setText(self.toPlainText())
        self.flash_copied()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        x = 12
        for b in (self.copy_btn, self.clear_btn):
            if b.isHidden():
                continue
            b.setFixedWidth(88)
            b.adjustSize()
            b.move(x, self.height() - b.height() - 12)
            b.raise_()
            x += b.width() + 8


class Button(AnimButton):
    KINDS = {
        "ghost": dict(base=(15, 15, 15), hov=(26, 26, 26), text=(240, 240, 240), size=15, weight=QFont.Normal,
                      px=22, py=11, r=11, dis=(10, 10, 10), dis_text=(90, 90, 90)),
        "accent": dict(base=(236, 90, 190), hov=(236, 90, 190), text=(255, 255, 255), size=16, weight=QFont.Medium,
                       px=36, py=15, r=16, dis=(30, 30, 30), dis_text=(100, 100, 100)),
    }
    KINDS["pill"] = KINDS["ghost"]

    def __init__(self, text, kind="ghost"):
        super().__init__(text)
        self.k = self.KINDS[kind]
        self.kind = kind
        self.backdrop = None
        f = QFont(self.font())
        f.setPixelSize(self.k["size"])
        f.setWeight(self.k["weight"])
        self.setFont(f)
        self.setIconSize(QSize(18, 18))
        self._enable = 1.0 if self.isEnabled() else 0.0
        self.a_enable = QPropertyAnimation(self, b"enableMix", self)

    def get_enable(self):
        return self._enable

    def set_enable(self, v):
        self._enable = v
        self.update()

    enableMix = Property(float, get_enable, set_enable)

    def changeEvent(self, e):
        if e.type() == QEvent.EnabledChange:
            self.go(self.a_enable, 1.0 if self.isEnabled() else 0.0, 380, QEasingCurve.InOutSine)
        super().changeEvent(e)

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self.text().strip()) + self.k["px"] * 2
        if self.kind == "accent":
            w = fm.horizontalAdvance(self.text().strip()) + 72
        if not self.icon().isNull():
            w += self.iconSize().width() + 8
        return QSize(w, fm.height() + self.k["py"] * 2)

    def accent_brush(self, r, dark):
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0.0, QColor(*(int(c * dark) for c in (240, 98, 190))))
        g.setColorAt(1.0, QColor(*(int(c * dark) for c in (176, 98, 240))))
        return QBrush(g)

    def paintAccent(self, p, on):
        dark = 1.0 - 0.08 * self._press
        t = self._enable
        mix = lambda a, b: tuple(lerp(x, y, t) for x, y in zip(a, b))
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.save()
        self.squish(p)
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        top = mix((37, 37, 41), (78, 61, 110))
        bottom = mix((29, 29, 33), (61, 48, 89))
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0.0, QColor(*(int(c * dark) for c in top)))
        g.setColorAt(1.0, QColor(*(int(c * dark) for c in bottom)))
        p.fillPath(path, QBrush(g))
        strength = self._hover * t
        if strength > 0.001:
            p.setClipPath(path)
            glow = QRadialGradient(self._cursor_pos, self.width() * 0.9)
            glow.setColorAt(0.0, QColor(214, 172, 255, int(105 * strength)))
            glow.setColorAt(0.4, QColor(170, 120, 240, int(46 * strength)))
            glow.setColorAt(1.0, QColor(140, 92, 220, 0))
            p.fillRect(self.rect(), QBrush(glow))
            p.setClipping(False)
        edge = QLinearGradient(r.topLeft(), r.bottomLeft())
        edge.setColorAt(0.0, QColor(255, 255, 255, int(lerp(18, 34 + 20 * self._hover, t))))
        edge.setColorAt(1.0, QColor(255, 255, 255, int(lerp(10, 16 + 12 * self._hover, t))))
        p.setPen(QPen(QBrush(edge), 1.2))
        p.drawPath(path)
        p.restore()
        p.setFont(self.font())
        p.setPen(QColor(*(int(c) for c in mix((128, 128, 134), (255, 255, 255)))))
        p.drawText(QRectF(self.rect()), Qt.AlignCenter, self.text().strip())

    def paintEvent(self, _):
        k = self.k
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        on = self.isEnabled()
        if self.kind == "accent":
            self.paintAccent(p, on)
            return
        lit = self.kind == "pill" and self.isChecked()
        if not on:
            base, text = k["dis"], k["dis_text"]
        elif lit:
            base, text = (52, 52, 52), (255, 255, 255)
        else:
            base = tuple(lerp(a, b, self._hover) for a, b in zip(k["base"], k["hov"]))
            text = k["text"]
        dark = 1.0 - 0.08 * self._press
        base = tuple(max(0, int(c * dark)) for c in base)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, k["r"], k["r"])
        p.save()
        self.squish(p)
        if on and self.kind == "accent":
            p.fillPath(path, self.accent_brush(r, dark))
        else:
            if self.kind == "pill" and self.backdrop and not lit and on:
                pm, src = self.backdrop
                p.setClipPath(path)
                p.fillPath(path, QColor(*(int(c) for c in base)))
                p.drawPixmap(r, pm, src)
                p.fillPath(path, QColor(*(int(c) for c in base), 110))
                p.setClipping(False)
            else:
                p.fillPath(path, QColor(*(int(c) for c in base)))
            edge = QPainterPath()
            edge.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), k["r"], k["r"])
            p.setPen(QPen(QColor(255, 255, 255, int(22 + 14 * self._hover)), 1))
            p.drawPath(edge)
        p.restore()
        p.setPen(QColor(*text))
        p.setFont(self.font())
        label = self.text().strip()
        fm = QFontMetrics(self.font())
        tw = fm.horizontalAdvance(label)
        icon = self.icon()
        if icon.isNull():
            p.drawText(QRectF(self.rect()), Qt.AlignCenter, label)
            return
        iw = self.iconSize().width() + 8 if not icon.isNull() else 0
        x = (self.width() - tw - iw) / 2
        if not icon.isNull():
            mode = QIcon.Normal
            pm = icon.pixmap(self.iconSize(), mode, QIcon.On if lit else QIcon.Off)
            p.drawPixmap(QRectF(x, (self.height() - self.iconSize().height()) / 2,
                                self.iconSize().width(), self.iconSize().height()), pm, QRectF(pm.rect()))
            x += iw
        p.drawText(QRectF(x, 0, tw + 2, self.height()), Qt.AlignVCenter | Qt.AlignLeft, label)


class NavButton(AnimButton):
    def __init__(self, text, glyph):
        super().__init__(text)
        self.glyph = glyph
        self._sel = 0.0
        self.setCheckable(True)
        self.setFixedHeight(44)
        self.a_sel = QPropertyAnimation(self, b"sel", self)
        self.toggled.connect(lambda c: self.go(self.a_sel, 1.0 if c else 0.0, 220))
        f = QFont(self.font())
        f.setPixelSize(16)
        f.setWeight(QFont.Normal)
        self.setFont(f)

    def get_sel(self):
        return self._sel

    def set_sel(self, v):
        self._sel = v
        self.update()

    sel = Property(float, get_sel, set_sel)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        s, h = self._sel, self._hover
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        p.fillPath(path, QColor(255, 255, 255, int(8 * h * (1 - s) + 20 * s)))
        t = max(s, h * 0.7)
        c = QColor(int(lerp(122, 255, t)), int(lerp(122, 255, t)), int(lerp(122, 255, t)))
        p.setPen(c)
        gf = QFont("Segoe Fluent Icons")
        gf.setPixelSize(20)
        p.setFont(gf)
        p.drawText(QRectF(16, 0, 24, self.height()), Qt.AlignCenter, self.glyph)
        p.setFont(self.font())
        p.drawText(QRectF(52, 0, self.width() - 60, self.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   self.text().strip())


class SmoothScroller(QObject):
    """Eased wheel scrolling for any scroll area. Disabled globally via SmoothScroller.on."""
    on = True

    def __init__(self, area):
        super().__init__(area)
        self.bar = area.verticalScrollBar()
        self.pos = self.target = 0.0
        self.last = 0.0
        self.area = area
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        area.viewport().installEventFilter(self)

    def eventFilter(self, _, e):
        if (e.type() == QEvent.Wheel and SmoothScroller.on and e.angleDelta().y() != 0
                and not e.modifiers() & Qt.ControlModifier):
            if not self.timer.isActive():
                self.pos = self.target = float(self.bar.value())
                self.last = time.monotonic()
                hz = self.area.screen().refreshRate() if self.area.screen() else 60.0
                self.timer.setInterval(max(4, int(1000 / max(hz, 30.0)) - 1))
            self.target = max(self.bar.minimum(), min(self.bar.maximum(), self.target - e.angleDelta().y() * 0.85))
            self.timer.start()
            e.accept()
            return True
        return False

    def tick(self):
        now = time.monotonic()
        dt = min(now - self.last, 0.05)
        self.last = now
        if abs(self.bar.value() - round(self.pos)) > 2:  # scrollbar was dragged meanwhile
            self.timer.stop()
            return
        d = self.target - self.pos
        if abs(d) < 0.5:
            self.pos = self.target
            self.timer.stop()
        else:
            self.pos += d * (1 - math.exp(-dt / 0.055))
        self.bar.setValue(round(self.pos))


class AppIcon(QWidget):
    def __init__(self, name, size=32):
        super().__init__()
        self.name, self.img = name, None
        self.setFixedSize(size, size)

    def set_image(self, img):
        self.img = QPixmap.fromImage(img)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 9, 9)
        p.fillPath(path, QColor("#141414"))
        if self.img:
            p.setClipPath(path)
            p.drawPixmap(r.adjusted(5, 5, -5, -5), self.img, QRectF(self.img.rect()))
        else:
            p.setPen(QColor("#8a8a8a"))
            f = p.font()
            f.setWeight(QFont.Medium)
            f.setPixelSize(14)
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, self.name[:1].upper())


class Bar(QWidget):
    """Progress bar. Segment changes morph smoothly and set_scanning() fades in a moving shimmer."""

    def __init__(self):
        super().__init__()
        self.value = -1.0
        self.segments = []
        self._from = {}
        self._to = {}
        self._order = []
        self._morph = 1.0
        self._scan = 0.0
        self.scanning = False
        self.setFixedHeight(5)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.morph_anim = QPropertyAnimation(self, b"morph", self)
        self.morph_anim.setDuration(600)
        self.morph_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.scan_anim = QPropertyAnimation(self, b"scan", self)
        self.scan_anim.setDuration(450)
        self.scan_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.scan_anim.finished.connect(self.sync_timer)

    def get_morph(self):
        return self._morph

    def set_morph(self, v):
        self._morph = v
        self.update()

    morph = Property(float, get_morph, set_morph)

    def get_scan(self):
        return self._scan

    def set_scan(self, v):
        self._scan = v
        self.update()

    scan = Property(float, get_scan, set_scan)

    def sync_timer(self):
        if (self.scanning or self._scan > 0.001 or self.value < 0) and not self.segments_only():
            if not self.timer.isActive():
                self.timer.start(16)
        else:
            self.timer.stop()

    def segments_only(self):
        return self.value >= 0 and not self.scanning and self._scan <= 0.001

    def set_value(self, v):
        self.value = v
        self.sync_timer()
        self.update()

    def current(self):
        keys = list(dict.fromkeys(self._order))
        return {k: self._from.get(k, 0.0) + (self._to.get(k, 0.0) - self._from.get(k, 0.0)) * self._morph
                for k in keys}

    def set_segments(self, segments):
        """segments: [(fraction of the bar, color hex)] drawn left to right; morphs from the old look."""
        start = self.current()
        if not start and self.value >= 0:
            start = {"#ffffff": min(self.value, 1.0)}
        self._to = {color: fraction for fraction, color in segments}
        self._from = start
        self._order = [color for _, color in segments] + [k for k in start if k not in self._to]
        self.segments = segments
        self.morph_anim.stop()
        self._morph = 0.0
        self.morph_anim.setStartValue(0.0)
        self.morph_anim.setEndValue(1.0)
        self.morph_anim.start()
        self.sync_timer()

    def set_scanning(self, on):
        self.scanning = on
        self.scan_anim.stop()
        self.scan_anim.setStartValue(self._scan)
        self.scan_anim.setEndValue(1.0 if on else 0.0)
        self.scan_anim.start()
        self.sync_timer()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, w, h), h / 2.0, h / 2.0)
        p.fillPath(track, QColor("#1f1f1f"))
        p.setClipPath(track)
        p.setPen(Qt.NoPen)
        if self._order:
            x = 0.0
            for color, fraction in self.current().items():
                seg = w * fraction
                if seg > 0.5:
                    p.setBrush(QColor(color))
                    p.drawRect(QRectF(x, 0, seg - 2 if seg > 4 else seg, h))
                x += seg
        elif self.value >= 0:
            p.setBrush(QColor("#ffffff"))
            p.drawRect(QRectF(0, 0, w * min(self.value, 1.0), h))
        else:
            p.setBrush(QColor("#ffffff"))
            seg = w * 0.3
            x = (w + seg) * ((time.monotonic() * 0.8) % 1.0) - seg
            p.drawRect(QRectF(x, 0, seg, h))
        if self._scan > 0.001:
            band = w * 0.35
            x = (w + band) * ((time.monotonic() * 0.7) % 1.0) - band
            g = QLinearGradient(x, 0, x + band, 0)
            peak = int(120 * self._scan)
            g.setColorAt(0.0, QColor(255, 255, 255, 0))
            g.setColorAt(0.5, QColor(255, 255, 255, peak))
            g.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(g))
            p.drawRect(QRectF(0, 0, w, h))

class Toggle(QWidget):
    toggled = Signal(bool)

    def __init__(self):
        super().__init__()
        self.on = False
        self._pos = 0.0
        self.setFixedSize(46, 26)
        self.anim = QPropertyAnimation(self, b"progress", self)
        self.anim.setDuration(240)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)

    def get_pos(self):
        return self._pos

    def set_pos(self, v):
        self._pos = v
        self.update()

    progress = Property(float, get_pos, set_pos)

    def set(self, on):
        self.on = on
        self.anim.stop()
        self.anim.setStartValue(self._pos)
        self.anim.setEndValue(1.0 if on else 0.0)
        self.anim.start()
        self.toggled.emit(on)

    def set_quiet(self, on):
        self.on = on
        self._pos = 1.0 if on else 0.0
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.isEnabled():
            self.set(not self.on)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        t = self._pos
        track = QColor(int(52 + 203 * t), int(52 + 203 * t), int(56 + 199 * t))
        knob = QColor(int(142 - 142 * t), int(142 - 142 * t), int(147 - 147 * t))
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 46, 26), 13, 13)
        p.setPen(QColor(105, 105, 110) if t < 0.5 else QColor(70, 70, 74))
        f = QFont(self.font())
        f.setPixelSize(10)
        f.setWeight(QFont.Bold)
        p.setFont(f)
        p.drawText(QRectF(6, 0, 12, 26), Qt.AlignCenter, "O")
        p.drawText(QRectF(28, 0, 12, 26), Qt.AlignCenter, "I")
        p.setBrush(knob)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(3 + 20 * t, 3, 20, 20))


# ---------- rows ----------

class HoverFrame(QFrame):
    """Row background that fades in on hover."""
    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._h = 0.0
        self.a_h = QPropertyAnimation(self, b"hl", self)
        self.a_h.setEasingCurve(QEasingCurve.OutCubic)

    def get_hl(self):
        return self._h

    def set_hl(self, v):
        self._h = v
        self.update()

    hl = Property(float, get_hl, set_hl)

    def fade(self, end, ms):
        self.a_h.stop()
        self.a_h.setDuration(ms)
        self.a_h.setStartValue(self._h)
        self.a_h.setEndValue(end)
        self.a_h.start()

    def enterEvent(self, e):
        self.fade(1.0, 160)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.fade(0.0, 240)
        super().leaveEvent(e)

    def paintEvent(self, e):
        if self._h > 0.001:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            v = int(15 * self._h)
            p.setBrush(QColor(v, v, v))
            p.drawRoundedRect(self.rect(), 14, 14)
            p.end()
        super().paintEvent(e)


class SpecRow(HoverFrame):
    """Spec line: clicking anywhere on the row copies its whole value."""

    def __init__(self, label, value, button=None, copyable=True):
        super().__init__()
        self.setObjectName("row")
        self.copyable = copyable
        if copyable:
            self.setCursor(Qt.PointingHandCursor)
        self.copy_text = value
        self.flash_id = 0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(14)
        self.name = QLabel(label)
        self.name.setObjectName("muted")
        self.name.setFixedWidth(150)
        self.value = QLabel(value)
        self.value.setWordWrap(True)
        self.value.setStyleSheet("font-size: 16px;")
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        self.hint.setFixedWidth(92)
        self.hint.setAlignment(Qt.AlignRight | Qt.AlignTop)
        for w in (self.name, self.value, self.hint):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self.name, 0, Qt.AlignTop)
        lay.addWidget(self.value, 1)
        if button is not None:
            lay.addWidget(button, 0, Qt.AlignTop)
        lay.addWidget(self.hint, 0, Qt.AlignTop)

    def set_value(self, text, copy_text=None):
        self.value.setText(text)
        self.copy_text = text if copy_text is None else copy_text

    def set_attention(self, on):
        self.setObjectName("attention" if on else "row")
        self.style().unpolish(self)
        self.style().polish(self)

    def enterEvent(self, e):
        if self.copyable and self.hint.text() != "Copied":
            self.hint.setText("Click to copy")
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self.hint.text() != "Copied":
            self.hint.setText("")
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if not self.copyable or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        QApplication.clipboard().setText(self.copy_text)
        self.flash_id += 1
        flash_id = self.flash_id
        self.hint.setText("Copied")
        self.hint.setStyleSheet("color: #7ee0a0;")
        self.fade(1.0, 60)

        def restore():
            if self.flash_id == flash_id and isValid(self):
                self.hint.setStyleSheet("")
                self.hint.setText("Click to copy" if self.underMouse() else "")
        QTimer.singleShot(1200, restore)


class Row(HoverFrame):
    def __init__(self, title, subtitle="", icon=None, right_margin=16):
        super().__init__()
        self.setObjectName("row")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12 if icon else 16, right_margin, 12 if icon else 16)
        lay.setSpacing(14)
        if icon:
            lay.addWidget(icon)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = title
        t = QLabel(title)
        t.setStyleSheet("font-size: 17px;")
        self.sub = QLabel(subtitle)
        self.sub.setObjectName("muted")
        if not subtitle:
            self.sub.hide()
        text.addWidget(t)
        text.addWidget(self.sub)
        self.text_col = text
        lay.addLayout(text, 1)
        self.right = QHBoxLayout()
        self.right.setSpacing(8)
        self.right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addLayout(self.right)


class MetricCard(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("metric")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)
        self.title = QLabel(title)
        self.title.setObjectName("muted")
        self.value = QLabel("Unavailable")
        self.value.setObjectName("metricValue")
        layout.addWidget(self.title)
        layout.addWidget(self.value)

    def set_value(self, text):
        self.value.setText(text)

    def set_alert(self, alert):
        self.value.setStyleSheet("color: #ff6268;" if alert else "")

    def set_temperature(self, temperature):
        self.value.setStyleSheet("color: #ff6268;" if temperature is not None and temperature >= 80 else "")


class TaskRow(Row):
    def __init__(self, title, subtitle, on_apply=None, on_revert=None, reversible=True):
        super().__init__(title, subtitle)
        self.reversible = reversible
        self.sub.setWordWrap(True)
        self.setCursor(Qt.ArrowCursor)
        self.apply = None
        self.revert = None
        if on_apply:
            self.apply = Button("Apply")
            self.apply.setCursor(Qt.PointingHandCursor)
            self.apply.setFixedWidth(ROW_ACTION_BUTTON_WIDTH)
            self.apply.clicked.connect(on_apply)
            self.right.addWidget(self.apply)
        if on_revert:
            self.revert = Button("Revert")
            self.revert.setCursor(Qt.PointingHandCursor)
            self.revert.setFixedWidth(ROW_ACTION_BUTTON_WIDTH)
            self.revert.clicked.connect(on_revert)
            self.right.addWidget(self.revert)
        self.toggle = Toggle()
        self.right.addWidget(self.toggle)

    def set_active(self, active, selected=None):
        if self.apply is None:
            return
        self.apply.setText("Applied" if active else "Apply")
        self.apply.setEnabled(not active)
        self.revert.setEnabled(active and self.reversible)
        if active:
            self.toggle.set_quiet(True)
        elif selected is not None:
            self.toggle.set_quiet(selected)
        self.toggle.setEnabled(not active)


class RunRow(Row):
    """Single-use action: one click runs it."""

    def __init__(self, title, subtitle, on_run, on_revert, active=False):
        super().__init__(title, subtitle)
        self.b = Button("Run")
        self.b.setCursor(Qt.PointingHandCursor)
        self.b.setFixedWidth(ROW_ACTION_BUTTON_WIDTH)
        self.b.clicked.connect(on_run)
        self.right.addWidget(self.b)
        self.revert = Button("Revert")
        self.revert.setCursor(Qt.PointingHandCursor)
        self.revert.setFixedWidth(ROW_ACTION_BUTTON_WIDTH)
        self.revert.clicked.connect(on_revert)
        self.right.addWidget(self.revert)
        spacer = QWidget()
        spacer.setFixedSize(46, 26)
        self.right.addWidget(spacer)
        self.set_active(active)

    def set_active(self, active):
        self.b.setText("Applied" if active else "Run")
        self.b.setEnabled(not active)
        self.revert.setEnabled(active)


class ActionRow(Row):
    """actions: (label, done label, kind, fn(job) -> bool). A running action's button turns into Cancel."""

    def __init__(self, title, subtitle, actions, kernel, key, domain=None):
        super().__init__(title, subtitle, kernel.make_icon(key, domain, title))
        self.actions, self.kernel, self.key, self.domain = actions, kernel, key, domain
        self.jobs = [None] * len(actions)
        self.source = None
        self.buttons = []
        for i, a in enumerate(actions):
            b = Button(a[0])
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(116)
            b.clicked.connect(lambda _=False, i=i: self.click(i))
            self.right.addWidget(b)
            self.buttons.append(b)

    def set_info(self, version, size, url=""):
        self.sub.setText(f"v{version.lstrip('vV')}" + (f"  ·  {apps.human(size)}" if size else ""))
        self.sub.show()
        if url:
            if self.source is None:
                self.source = QLabel()
                self.source.setObjectName("muted")
                self.source.setTextFormat(Qt.RichText)
                self.source.setOpenExternalLinks(True)
                self.source.setTextInteractionFlags(Qt.TextBrowserInteraction)
                self.text_col.addWidget(self.source)
            shown = html.escape(url if len(url) <= 90 else url[:87] + "...")
            self.source.setText(f'Direct Source: <a href="{html.escape(url, quote=True)}" style="color:#b48cf0;text-decoration:none;">{shown}</a>')

    def click(self, i):
        job = self.jobs[i]
        if job and job.active:
            self.kernel.jobs.cancel(job)
            return
        _, _, kind, fn = self.actions[i]
        job = Job(self.title, kind, self.key, fn, domain=self.domain)
        job.changed.connect(self.refresh)
        self.jobs[i] = job
        self.kernel.jobs.submit(job, parallel=(kind == "download"))
        self.refresh()

    def refresh(self):
        for (label, done, _, _), b, job in zip(self.actions, self.buttons, self.jobs):
            if job is None:
                b.setText(label)
            elif job.active:
                b.setText("Cancel")
            else:
                b.setText({"done": done, "cancelled": label}.get(job.state, "Retry"))


class JobRow(HoverFrame):
    def __init__(self, job, kernel):
        super().__init__()
        self.setObjectName("row")
        self.job, self.kernel = job, kernel
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(14)
        lay.addWidget(kernel.make_icon(job.key or job.name, job.domain, job.name))
        col = QVBoxLayout()
        col.setSpacing(5)
        name = QLabel(job.name)
        name.setStyleSheet("font-size: 17px;")
        self.bar = Bar()
        self.status = QLabel()
        self.status.setObjectName("muted")
        col.addWidget(name)
        col.addWidget(self.bar)
        col.addWidget(self.status)
        lay.addLayout(col, 1)
        self.btn = Button("", "ghost")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setMinimumWidth(84)
        self.btn.clicked.connect(self.click)
        lay.addWidget(self.btn)
        job.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        j = self.job
        kind = j.kind.capitalize()
        if j.active:
            self.bar.setVisible(True)
            self.bar.set_value(j.progress if j.state == "running" else -1)
            self.status.setText(f"{kind} · {j.message}")
            self.btn.setText("Cancel")
            self.btn.setVisible(True)
            return
        self.bar.setVisible(False)
        self.bar.set_value(1.0)
        when = time.strftime("%b %d, %I:%M %p", time.localtime(j.finished_at)) if j.finished_at else ""
        self.status.setText(" · ".join(x for x in (kind, j.message, when) if x))
        can_show = bool(j.path) and os.path.exists(j.path)
        self.btn.setText("Show")
        self.btn.setVisible(can_show)

    def click(self):
        if self.job.active:
            self.kernel.jobs.cancel(self.job)
        elif self.job.path and os.path.exists(self.job.path):
            target = self.job.path
            if os.path.isdir(target):
                os.startfile(target)
            else:
                subprocess.Popen(f'explorer /select,"{target}"')


class Page(QScrollArea):
    def __init__(self, title):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("content")
        self.setWidget(content)
        SmoothScroller(self)
        outer = QHBoxLayout(content)
        outer.setContentsMargins(20, 0, 20, 0)
        col = QWidget()
        col.setMaximumWidth(1060)
        outer.addWidget(col, 1)
        outer.addStretch(0)
        self.col = QVBoxLayout(col)
        self.col.setContentsMargins(0, 28, 0, 36)
        self.col.setSpacing(0)
        t = QLabel(title)
        t.setObjectName("title")
        self.col.addWidget(t)

    def section(self, text):
        self.col.addSpacing(26)
        s = QLabel(text)
        s.setObjectName("section")
        self.col.addWidget(s)
        self.col.addSpacing(8)
        return s

    def add(self, w):
        self.col.addWidget(w)
        self.col.addSpacing(2)


class Overlay(QWidget):
    """Invisible click-catcher over the window; the app underneath stays untouched."""
    closed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.panel = None

    def mousePressEvent(self, e):
        if self.panel is None or not self.panel.geometry().contains(e.position().toPoint()):
            self.closed.emit()


class Panel(QFrame):
    """Frosted-glass panel: paints a blurred copy of what is behind it, tinted dark."""

    def __init__(self, parent):
        super().__init__(parent)
        self.bg = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 18, 18)
        p.setClipPath(path)
        if self.bg:
            k = self.bg.width() / max(self.window().width(), 1)
            g = self.geometry()
            p.drawPixmap(QRectF(self.rect()), self.bg, QRectF(g.x() * k, g.y() * k, g.width() * k, g.height() * k))
        p.fillPath(path, QColor(12, 12, 12, 150))
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.drawPath(path)


class ApplicationsHost(QWidget):
    def __init__(self, page, edit):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(page)
        self.dock = SearchDock(page, edit)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.dock.place()


class SearchDock(QWidget):
    """Search field pinned over the bottom of a scrolling page, frosted over whatever scrolls behind it."""
    H, GAP = 60, 22

    def __init__(self, page, edit):
        super().__init__(page.parentWidget())
        self.page = page
        self.edit = edit
        self._focus = 0.0
        self._base_rect = QRect()
        self.backdrop = QPixmap()
        self.backdrop_src = QRectF()
        self.backdrop_k = 1.0
        self.focus_anim = QPropertyAnimation(self, b"focusProgress", self)
        self.focus_anim.setDuration(360)
        self.focus_anim.setEasingCurve(
            QEasingCurve.fromCubicBezier(QPointF(0.22, 0.1), QPointF(0.25, 1.0)))
        edit.setParent(self)
        edit.setFrame(False)
        edit.setStyleSheet("QLineEdit { background: transparent; border: none; padding: 0 22px; "
                           "font-size: 18px; selection-background-color: #444444; }")
        self.tick = QTimer(self)
        self.tick.setInterval(250)
        self.tick.timeout.connect(self.refresh)
        self.tick.start()
        page.verticalScrollBar().valueChanged.connect(self.capture_backdrop)
        app = QApplication.instance()
        if app:
            app.focusChanged.connect(self.focus_changed)
        self.edit.installEventFilter(self)

    def focus_changed(self, old, new):
        if old is self.edit or new is self.edit:
            self.animate_focus(new is self.edit)

    def eventFilter(self, obj, event):
        if obj is self.edit and event.type() == QEvent.MouseButtonPress:
            self.edit.setFocus(Qt.MouseFocusReason)
            self.animate_focus(True)
        return False

    def animate_focus(self, focused):
        self.focus_anim.stop()
        self.focus_anim.setStartValue(round(self._focus * 100))
        self.focus_anim.setEndValue(100 if focused else 0)
        self.focus_anim.start()

    def get_focus_progress(self):
        return round(self._focus * 100)

    def set_focus_progress(self, value):
        self._focus = value / 100.0
        self.update_geometry()
        self.update()

    focusProgress = Property(int, get_focus_progress, set_focus_progress)

    def update_geometry(self):
        if self._base_rect.isNull():
            return
        scale = 1.0 + 0.02 * self._focus
        width = round(self._base_rect.width() * scale)
        height = round(self._base_rect.height() * scale)
        center = self._base_rect.center()
        self.setGeometry(QRect(center.x() - width // 2, center.y() - height // 2, width, height))
        self.edit.setGeometry(self.rect())

    def refresh(self):
        if self.isVisible() and self._focus in (0.0, 1.0):
            self.capture_backdrop()

    def capture_backdrop(self):
        if not self.isVisible() or self.width() <= 0:
            return
        m = 24
        g = self._base_rect
        if g.isNull():
            return
        area = QRect(self.page.mapTo(self.parentWidget(), QPoint(0, 0)), self.page.size())
        region = g.adjusted(-m, -m, m, m) & area
        if region.isEmpty():
            return
        shot = self.page.grab(QRect(self.page.mapFrom(self.parentWidget(), region.topLeft()), region.size()))
        if shot.isNull():
            return
        shot.setDevicePixelRatio(1.0)
        pm = blurred(shot, radius=10)
        k = pm.width() / region.width()
        off = g.topLeft() - region.topLeft()
        self.backdrop = pm
        self.backdrop_k = k
        self.backdrop_src = QRectF(off.x() * k, off.y() * k, g.width() * k, g.height() * k)
        self.update()

    def place(self):
        host = self.parentWidget()
        w = max(host.width() - 40, 0)
        self._base_rect = QRect(20, host.height() - self.H - self.GAP, w, self.H)
        self.update_geometry()
        self.update()
        self.raise_()
        QTimer.singleShot(0, self.capture_backdrop)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        p.setClipPath(path)
        p.fillPath(path, QColor(14, 14, 14, 235))
        if not self.backdrop.isNull():
            dx = (self.width() - self._base_rect.width()) / 2 * self.backdrop_k
            dy = (self.height() - self._base_rect.height()) / 2 * self.backdrop_k
            p.drawPixmap(QRectF(self.rect()), self.backdrop, self.backdrop_src.adjusted(-dx, -dy, dx, dy))
            p.fillPath(path, QColor(14, 14, 14, 110))
        p.setClipping(False)
        a = round(32 + 20 * self._focus)
        width = 1.0 + 0.1 * self._focus
        p.setPen(QPen(QColor(255, 255, 255, a), width))
        p.drawPath(path)


def blurred(pm, radius=18):
    """Blur a window screenshot (downscaled first, so it stays fast on big monitors)."""
    pm.setDevicePixelRatio(1.0)
    small = pm.scaled(max(pm.width() // 2, 1), max(pm.height() // 2, 1), Qt.IgnoreAspectRatio,
                      Qt.SmoothTransformation)
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(small)
    fx = QGraphicsBlurEffect()
    fx.setBlurRadius(radius)
    fx.setBlurHints(QGraphicsBlurEffect.QualityHint)
    item.setGraphicsEffect(fx)
    scene.addItem(item)
    out = QImage(small.size(), QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    p = QPainter(out)
    scene.render(p, QRectF(out.rect()), QRectF(small.rect()))
    p.end()
    return QPixmap.fromImage(out)


# ---------- main window ----------

class FluentCombo(QComboBox):
    def __init__(self):
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(190)
        popup = self.view().window()
        popup.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        f = QFont("Segoe Fluent Icons")
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor(MUTED))
        p.drawText(self.rect().adjusted(0, 0, -14, 0), Qt.AlignRight | Qt.AlignVCenter, "\uE70D")


class SideGrip(QWidget):
    """Thin drag handle that resizes the sidebar."""
    MIN, MAX = 150, 420

    def __init__(self, side):
        super().__init__()
        self.side = side
        self.setFixedWidth(5)
        self.setCursor(Qt.SizeHorCursor)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            x = self.side.mapFromGlobal(e.globalPosition().toPoint()).x()
            self.side.setFixedWidth(max(self.MIN, min(self.MAX, x)))


class Kernel(QWidget):
    logged = Signal(str)
    found = Signal(list)
    icon_ready = Signal(str, QImage)
    specs_ready = Signal(list, str)
    updates_ready = Signal(object)
    drives_ready = Signal(list)
    storage_ready = Signal(str, dict)
    scan_finished = Signal()
    monitor_sample = Signal(dict)
    monitor_error = Signal(str)
    monitor_finished = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.cfg = Config()
        self.prefetch = self.scan_source = None
        self.start_storage_prefetch()
        SmoothScroller.on = self.cfg.smooth_scroll
        self.jobs = JobManager(os.path.join(self.cfg.dir, "downloads.json"))
        self.icons, self.icon_widgets, self.icon_pending = {}, {}, set()
        self.icon_q = queue.Queue()
        self.icon_done = queue.Queue()
        self.info_q, self.info_done, self.info_rows = queue.Queue(), queue.Queue(), {}
        for _ in range(4):
            threading.Thread(target=self.info_loop, daemon=True).start()
        self.icon_timer = QTimer(self)
        self.icon_timer.timeout.connect(self.drain_icons)
        self.icon_timer.start(100)
        self.catalog_domain = {c[2]: c[3] for c in apps.CATALOG}
        for _ in range(6):
            threading.Thread(target=self.icon_loop, daemon=True).start()
        self.anim = None
        self.tweak_job = None

        self.setObjectName("window")
        self.setWindowTitle("kernel")
        avail = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1180, avail.width() - 80), min(820, avail.height() - 80))
        if self.cfg.win_size:
            self.resize(min(max(self.cfg.win_size[0], 640), avail.width()), min(max(self.cfg.win_size[1], 480), avail.height()))
        self.setMinimumSize(640, 480)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(210)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 24, 14, 14)
        sl.setSpacing(4)
        logo = QLabel("kernel")
        logo.setObjectName("h1")
        sl.addWidget(logo)
        sl.addSpacing(20)
        outer.addWidget(side)
        outer.addWidget(SideGrip(side))

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        outer.addWidget(right, 1)
        self.stack = QStackedWidget()
        rv.addWidget(self.stack, 1)

        self.rows = []
        self.action_rows = []
        self.stack.addWidget(self.build_tweaks())
        self.stack.addWidget(self.build_apps())
        self.stack.addWidget(self.build_gaming())
        self.stack.addWidget(self.build_storage())
        self.stack.addWidget(self.build_specs())
        self.stack.addWidget(self.build_activity())
        self.stack.addWidget(self.build_settings())

        self.navs = []
        group = QButtonGroup(self)
        for text, idx, g in (("Tweaks", TWEAKS, "tweaks"), ("Performance", GAMING, "gaming"),
                             ("Applications", APPS, "apps"), ("Storage", STORAGE, "storage"),
                             ("Specifications", SPECS, "specs"), ("Activity", ACTIVITY, "activity")):
            sl.addWidget(self.nav(text, idx, g, group))
        sl.addStretch()
        sl.addWidget(self.nav("Settings", SETTINGS, "settings", group))
        self.navs.sort(key=lambda b: b[0])
        self.navs[0][1].setChecked(True)

        self.footer = QFrame()
        self.footer.setObjectName("footer")
        self.footer.setFixedHeight(68)
        fl = QHBoxLayout(self.footer)
        fl.setContentsMargins(36, 0, 36, 0)
        self.status = QLabel("Nothing selected")
        self.status.setObjectName("muted")
        fl.addWidget(self.status)
        fl.addStretch()
        self.btn = Button("Apply", "accent")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self.start)
        fl.addWidget(self.btn)
        rv.addWidget(self.footer)

        self.build_overlay()

        self.logged.connect(self.log.appendPlainText)
        self.jobs.logged.connect(self.log.appendPlainText)
        self.jobs.added.connect(self.add_job_row)

        self.applied_states = {}
        self.profile_pending = False
        for r, key, _, fn in self.rows:
            actual = is_active(fn)
            active = self.cfg.applied.get(key, actual)
            if key in IRREVERSIBLE and actual:
                active = True
            self.applied_states[key] = active
            r.set_active(active, self.cfg.toggles.get(key, False))
            r.toggle.toggled.connect(self.on_toggle)
            self.profile_pending |= active != actual
        for job in self.jobs.load():
            self.add_job_row(job)
        self.update_status()
        self.update_pill()
        if not is_admin():
            self.logged.emit("Not running as administrator - admin tasks will fail.")

    # ---- pinned download manager ----

    def build_overlay(self):
        self.overlay = Overlay(self)
        self.overlay.closed.connect(self.collapse_downloads)
        panel = Panel(self.overlay)
        self.overlay.panel = panel
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(18, 16, 18, 18)
        pl.setSpacing(10)
        head = QHBoxLayout()
        h = QLabel("Downloads")
        h.setStyleSheet("font-size: 20px; font-weight: 500;")
        head.addWidget(h)
        head.addStretch()
        clear = Button("Clear finished")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self.clear_finished)
        head.addWidget(clear)
        pl.addLayout(head)
        self.dl_hint = QLabel("No downloads yet. Anything you download or install shows up here.")
        self.dl_hint.setObjectName("muted")
        self.dl_hint.setWordWrap(True)
        pl.addWidget(self.dl_hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("dlscroll")
        scroll.setStyleSheet("#dlscroll { background: transparent; border: none; }")
        scroll.viewport().setObjectName("dlvp")
        scroll.viewport().setStyleSheet("#dlvp { background: transparent; }")
        inner = QWidget()
        inner.setObjectName("dlinner")
        inner.setStyleSheet("#dlinner { background: transparent; }")
        self.dl_list = QVBoxLayout(inner)
        self.dl_list.setContentsMargins(0, 0, 0, 0)
        self.dl_list.setSpacing(2)
        self.dl_list.addStretch()
        scroll.setWidget(inner)
        SmoothScroller(scroll)
        pl.addWidget(scroll, 1)
        self.job_rows = []
        self.overlay.hide()

        self.pill = Button("Downloads", "pill")
        self.pill.setParent(self)
        self.pill.setCheckable(True)
        self.pill.setIcon(glyph_icon(GLYPH["downloads"], 18, TEXT))
        self.pill.setCheckable(True)
        self.pill.clicked.connect(self.toggle_downloads)
        self.pill_tick = QTimer(self)
        self.pill_tick.setInterval(250)
        self.pill_tick.timeout.connect(self.refresh_pill_backdrop)
        self.pill_tick.start()
        for i in range(self.stack.count()):
            w = self.stack.widget(i)
            for sa in ([w] if isinstance(w, QScrollArea) else w.findChildren(QScrollArea)):
                sa.verticalScrollBar().valueChanged.connect(self.refresh_pill_backdrop)
        self.fade = None
        self.place_overlay()

    def refresh_pill_backdrop(self):
        if (not self.pill.isVisible() or not self.isActiveWindow() or self.overlay.isVisible()
                or self.fade or self.pill.isDown()):
            return
        g = self.pill.geometry()
        cur = self.stack.currentWidget()
        page = cur if isinstance(cur, QScrollArea) else cur.findChild(QScrollArea)
        if page is None:
            self.pill.backdrop = None
            return
        m = 24
        area = QRect(page.mapTo(self, QPoint(0, 0)), page.size())
        region = g.adjusted(-m, -m, m, m) & area
        if region.isEmpty():
            return
        shot = page.grab(QRect(page.mapFrom(self, region.topLeft()), region.size()))
        if shot.isNull():
            return
        shot.setDevicePixelRatio(1.0)
        pm = blurred(shot, radius=10)
        k = pm.width() / region.width()
        off = g.topLeft() - region.topLeft()
        self.pill.backdrop = (pm, QRectF(off.x() * k, off.y() * k, g.width() * k, g.height() * k))
        self.pill.update()

    def place_overlay(self):
        w, h = self.width(), self.height()
        self.overlay.setGeometry(0, 0, w, h)
        pw = min(520, w - 48)
        ph = min(600, h - 110)
        self.overlay.panel.setGeometry(w - 24 - pw, 76, pw, ph)
        self.pill.adjustSize()
        self.pill.move(w - 24 - self.pill.width(), 20)
        self.pill.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "overlay"):
            self.place_overlay()

    def toggle_downloads(self):
        if self.pill.isChecked():
            self.expand_downloads()
        else:
            self.collapse_downloads()

    def expand_downloads(self):
        closing = bool(self.fade) and not self.fade_show
        if self.overlay.isVisible() and not closing:
            return
        start = 0.0
        if closing:
            start = self.overlay.graphicsEffect().opacity() if self.overlay.graphicsEffect() else 1.0
        else:
            self.pill.hide()
            shot = self.grab()
            self.pill.show()
            self.overlay.panel.bg = blurred(shot)
        self.place_overlay()
        self.pill.setChecked(True)
        self.run_fade(start, 1.0, show=True)
        self.overlay.raise_()
        self.pill.raise_()

    def collapse_downloads(self):
        self.pill.setChecked(False)
        if self.overlay.isVisible():
            self.run_fade(1.0, 0.0, show=False)

    def run_fade(self, a, b, show):
        if self.fade:
            self.fade.stop()
        fx = QGraphicsOpacityEffect(self.overlay)
        fx.setOpacity(a)
        self.overlay.setGraphicsEffect(fx)
        self.overlay.show()
        anim = QPropertyAnimation(fx, b"opacity", self)
        anim.setDuration(180)
        anim.setStartValue(a)
        anim.setEndValue(b)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(self.fade_done)
        self.fade, self.fade_show = anim, show
        anim.start()

    def fade_done(self):
        if not self.fade_show:
            self.overlay.hide()
        self.overlay.setGraphicsEffect(None)
        self.fade = None

    def update_pill(self):
        active = [j for j in self.jobs.jobs if j.active and not getattr(j, "hidden", False)]
        if active:
            self.pill.setText(f"  Downloads · {len(active)}")
        else:
            self.pill.setText("  Downloads")
        self.place_overlay()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.overlay.isVisible():
            self.collapse_downloads()
        else:
            super().keyPressEvent(e)

    # ---- navigation + page animation ----

    def nav(self, text, index, glyph, group):
        b = NavButton(text, GLYPH[glyph])
        b.clicked.connect(lambda: self.goto(index))
        group.addButton(b)
        self.navs.append((index, b))
        return b

    def goto(self, index):
        if self.overlay.isVisible():
            self.collapse_downloads()
        for i, b in self.navs:
            b.setChecked(i == index)
        if index == self.stack.currentIndex():
            return
        self.finish_anim()
        self.stack.setCurrentIndex(index)
        if index == GAMING:
            self.refresh_display_status()
        if index == SPECS:
            self.start_monitor()
        self.footer.setVisible(index in (TWEAKS, GAMING))
        page = self.stack.currentWidget()
        fx = QGraphicsOpacityEffect(page)
        fx.setOpacity(0.0)
        page.setGraphicsEffect(fx)
        fade = QPropertyAnimation(fx, b"opacity")
        fade.setDuration(220)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        slide = QPropertyAnimation(page, b"pos")
        slide.setDuration(260)
        slide.setStartValue(QPoint(0, 14))
        slide.setEndValue(QPoint(0, 0))
        slide.setEasingCurve(QEasingCurve.OutCubic)
        group = QParallelAnimationGroup(self)
        group.addAnimation(fade)
        group.addAnimation(slide)
        group.finished.connect(self.finish_anim)
        self.anim, self.anim_page = group, page
        group.start()

    def finish_anim(self):
        if self.anim is None:
            return
        group, page = self.anim, self.anim_page
        self.anim = None
        group.stop()
        if isValid(page):
            page.setGraphicsEffect(None)
            page.move(0, 0)

    # ---- icons ----

    def make_icon(self, key, domain, name):
        domain = domain or self.catalog_domain.get(key)
        w = AppIcon(name)
        self.icon_widgets.setdefault(key, []).append(w)
        if key in self.icons:
            w.set_image(self.icons[key])
        elif key not in self.icon_pending:
            self.icon_pending.add(key)
            self.icon_q.put((key, domain))
        return w

    def icon_loop(self):
        while True:
            key, domain = self.icon_q.get()
            img = QImage()
            for attempt in range(4):
                data = apps.icon_bytes(key, domain, google_only=attempt % 2 == 1)
                ok = bool(data) and img.loadFromData(data)
                if ok and not img.isNull():
                    break
                img = QImage()
                time.sleep(1.5)
            if not img.isNull():
                self.icon_done.put((key, img))

    def drain_icons(self):
        while True:
            try:
                key, img = self.icon_done.get_nowait()
            except queue.Empty:
                break
            self.on_icon(key, img)
        while True:
            try:
                key, version, size, url = self.info_done.get_nowait()
            except queue.Empty:
                return
            row = self.info_rows.get(key)
            if row is not None and isValid(row) and version:
                row.set_info(version, size, url)

    def info_loop(self):
        while True:
            key = self.info_q.get()
            try:
                version, size, url = apps.info(key)
            except Exception:
                continue
            self.info_done.put((key, version, size, url))

    def on_icon(self, key, img):
        self.icons[key] = img
        for w in self.icon_widgets.get(key, []):
            if isValid(w):
                w.set_image(img)

    # ---- pages ----

    def build_gaming(self):
        self.nvidia_control = None
        self.nvidia_error = None
        try:
            self.nvidia_control = display_controls.NVIDIAControl()
        except display_controls.NVIDIAUnavailable as ex:
            self.nvidia_error = str(ex)
        page = self.build_tweaks("Performance", ("Performance",))
        self.add_display_controls(page)
        page.col.addStretch()
        return page

    def add_display_controls(self, page):
        page.section("Display")
        self.refresh_row = Row(
            "Highest refresh rate",
            "Checking the primary NVIDIA display's highest rate at its current resolution...")
        self.refresh_row.setObjectName("row")
        self.highest_refresh_button = Button("Check")
        self.highest_refresh_button.clicked.connect(self.apply_highest_refresh)
        self.refresh_row.right.addWidget(self.highest_refresh_button)
        page.add(self.refresh_row)
        self.display_status = QLabel(
            "Settings are applied through the NVIDIA driver; the NVIDIA App is not required."
            if self.nvidia_control else
            f"NVIDIA display controls are unavailable: {self.nvidia_error}")
        self.display_status.setObjectName("muted")
        self.display_status.setWordWrap(True)
        self.display_status.setContentsMargins(16, 0, 16, 0)
        page.col.addWidget(self.display_status)

        self.gamma_slider = QSlider(Qt.Horizontal)
        self.gamma_slider.setRange(40, 280)
        self.gamma_slider.setValue(self.cfg.nvidia_gamma)
        self.gamma_slider.setFixedWidth(220)
        self.gamma_value = QLabel(f"{self.cfg.nvidia_gamma / 100:.2f}")
        self.gamma_slider.valueChanged.connect(self.queue_gamma_contrast)
        gamma = Row("Gamma", "Adjust the NVIDIA driver gamma correction")
        gamma.right.addWidget(self.gamma_value)
        gamma.right.addWidget(self.gamma_slider)
        page.add(gamma)

        self.contrast_slider = QSlider(Qt.Horizontal)
        self.contrast_slider.setRange(0, 100)
        self.contrast_slider.setValue(self.cfg.nvidia_contrast)
        self.contrast_slider.setFixedWidth(220)
        self.contrast_value = QLabel(f"{self.cfg.nvidia_contrast}%")
        self.contrast_slider.valueChanged.connect(self.queue_gamma_contrast)
        contrast = Row("Contrast", "Adjust contrast through the NVIDIA driver")
        contrast.right.addWidget(self.contrast_value)
        contrast.right.addWidget(self.contrast_slider)
        page.add(contrast)

        self.gamma_contrast_timer = QTimer(self)
        self.gamma_contrast_timer.setSingleShot(True)
        self.gamma_contrast_timer.timeout.connect(self.apply_gamma_contrast)
        self.gamma_slider.sliderReleased.connect(lambda: self.gamma_contrast_timer.start(0))
        self.contrast_slider.sliderReleased.connect(lambda: self.gamma_contrast_timer.start(0))

        self.vibrance_slider = QSlider(Qt.Horizontal)
        self.vibrance_slider.setFixedWidth(220)
        self.vibrance_value = QLabel("Unavailable")
        self.vibrance_range = None
        vibrance = Row("Digital vibrance", "Adjust NVIDIA digital vibrance")
        vibrance.right.addWidget(self.vibrance_value)
        vibrance.right.addWidget(self.vibrance_slider)
        page.add(vibrance)
        self.vibrance_slider.valueChanged.connect(self.queue_vibrance)
        self.vibrance_timer = QTimer(self)
        self.vibrance_timer.setSingleShot(True)
        self.vibrance_timer.timeout.connect(self.apply_vibrance)
        self.vibrance_slider.sliderReleased.connect(lambda: self.vibrance_timer.start(0))

        if self.nvidia_control is None:
            for slider in (self.gamma_slider, self.contrast_slider, self.vibrance_slider):
                slider.setEnabled(False)
            self.gamma_value.setText("Unavailable")
            self.contrast_value.setText("Unavailable")
        else:
            if self.nvidia_control.supports_gamma_contrast:
                if self.cfg.nvidia_gamma != 100 or self.cfg.nvidia_contrast != 50:
                    self.apply_gamma_contrast()
            else:
                self.gamma_slider.setEnabled(False)
                self.contrast_slider.setEnabled(False)
                self.gamma_value.setText("Unavailable")
                self.contrast_value.setText("Unavailable")
                self.display_status.setText(
                    f"{self.display_status.text()} NVIDIA gamma-correction control is unavailable.")

            if not self.nvidia_control.supports_vibrance:
                self.vibrance_slider.setEnabled(False)
                self.display_status.setText(
                    f"{self.display_status.text()} NVIDIA digital-vibrance control is unavailable.")
            else:
                try:
                    minimum, maximum, current = self.nvidia_control.vibrance_info()
                    self.vibrance_range = (minimum, maximum)
                    self.vibrance_slider.setRange(minimum, maximum)
                    self.vibrance_slider.setValue(current)
                    self.vibrance_value.setText(self.vibrance_text(current))
                except display_controls.UnsupportedDisplay as ex:
                    self.vibrance_slider.setEnabled(False)
                    self.display_status.setText(f"{self.display_status.text()} {ex}")

        self.refresh_display_status()

    def refresh_display_status(self):
        if self.nvidia_control is None:
            self.refresh_row.sub.setText("Unavailable without an active NVIDIA display output.")
            self.highest_refresh_button.setText("Unavailable")
            self.highest_refresh_button.setEnabled(False)
            return
        try:
            info = display_controls.refresh_info()
        except display_controls.DisplayError as ex:
            self.refresh_row.sub.setText(str(ex))
            self.highest_refresh_button.setText("Unavailable")
            self.highest_refresh_button.setEnabled(False)
            self.refresh_row.setObjectName("row")
            self.refresh_row.style().unpolish(self.refresh_row)
            self.refresh_row.style().polish(self.refresh_row)
            return
        if info["device"].casefold() != self.nvidia_control.display_name.casefold():
            self.refresh_row.sub.setText(
                "The primary display is no longer connected to the active NVIDIA output.")
            self.highest_refresh_button.setText("Unavailable")
            self.highest_refresh_button.setEnabled(False)
            self.refresh_row.setObjectName("row")
            self.refresh_row.style().unpolish(self.refresh_row)
            self.refresh_row.style().polish(self.refresh_row)
            return
        current, highest = info["current"], info["highest"]
        if highest > current:
            self.refresh_row.sub.setText(
                f"Highest refresh rate not applied · Current {current} Hz · Available {highest} Hz "
                f"at {info['width']}×{info['height']}.")
            self.highest_refresh_button.setText(f"Apply {highest} Hz")
            self.highest_refresh_button.setEnabled(True)
            self.refresh_row.setObjectName("attention")
        else:
            self.refresh_row.sub.setText(
                f"Highest available rate is active · {current} Hz at {info['width']}×{info['height']}.")
            self.highest_refresh_button.setText("Applied")
            self.highest_refresh_button.setEnabled(False)
            self.refresh_row.setObjectName("row")
        self.refresh_row.style().unpolish(self.refresh_row)
        self.refresh_row.style().polish(self.refresh_row)

    def apply_highest_refresh(self):
        if self.nvidia_control is None:
            return
        try:
            display_controls.apply_highest_refresh(self.nvidia_control.display_name)
        except (display_controls.DisplayError, OSError) as ex:
            self.display_status.setText(f"Could not apply the highest refresh rate: {ex}")
            self.logged.emit(f"Display refresh-rate change failed: {ex}")
            return
        self.refresh_display_status()
        self.logged.emit("Applied the highest refresh rate available for the primary display.")

    def queue_gamma_contrast(self, value):
        if self.sender() is self.gamma_slider:
            self.gamma_value.setText(f"{value / 100:.2f}")
        else:
            self.contrast_value.setText(f"{value}%")
        self.gamma_contrast_timer.start(350)

    def apply_gamma_contrast(self):
        if self.nvidia_control is None:
            return
        try:
            self.nvidia_control.set_gamma_contrast(
                self.gamma_slider.value() / 100, self.contrast_slider.value())
        except display_controls.DisplayError as ex:
            self.gamma_slider.setEnabled(False)
            self.contrast_slider.setEnabled(False)
            self.display_status.setText(f"NVIDIA gamma or contrast adjustment failed: {ex}")
            self.logged.emit(f"NVIDIA display adjustment failed: {ex}")
            return
        self.cfg.nvidia_gamma = self.gamma_slider.value()
        self.cfg.nvidia_contrast = self.contrast_slider.value()
        self.cfg.save()

    @staticmethod
    def vibrance_text(value):
        return f"{value}%" if 0 <= value <= 100 else str(value)

    def queue_vibrance(self, value):
        self.vibrance_value.setText(self.vibrance_text(value))
        self.vibrance_timer.start(350)

    def apply_vibrance(self):
        if self.nvidia_control is None or self.vibrance_range is None:
            return
        try:
            self.nvidia_control.set_vibrance(self.vibrance_slider.value())
        except display_controls.DisplayError as ex:
            self.vibrance_slider.setEnabled(False)
            self.display_status.setText(f"NVIDIA digital-vibrance adjustment failed: {ex}")
            self.logged.emit(f"NVIDIA digital-vibrance adjustment failed: {ex}")

    def build_tweaks(self, title="Tweaks", SECTION_ORDER=("Appearance", "System"), prefix=None):
        page = Page(title)
        if prefix:
            prefix(page)
        CATEGORY_MAP = {
            "Privacy": "System",
            "Apps": "System",
            "Input": "System",
            "Security": "System",
        }
        INLINE = {"Appearance", "Performance", "System"}
        visible = [(CATEGORY_MAP.get(cat, cat), name, desc, fn)
                   for cat, name, desc, fn in TASKS]
        toggles = [t for t in visible if t[3].__name__ not in ONE_SHOT]
        run_row = self.run_row
        for cat in SECTION_ORDER:
            page.section(cat)
            for _, name, desc, fn in (t for t in toggles if t[0] == cat):
                r = TaskRow(
                    name, desc,
                    lambda _=False, n=name, f=fn: self.run_tasks([(n, f)]),
                    lambda _=False, n=name, f=fn: self.run_reverts([(n, f)]),
                    reversible=fn.__name__ not in IRREVERSIBLE)
                r.set_active(is_active(fn))
                self.rows.append((r, fn.__name__, name, fn))
                page.add(r)
            action_names = ONE_SHOT if cat in INLINE else set()
            if action_names:
                for _, name, desc, fn in (t for t in visible if t[0] == cat and t[3].__name__ in action_names):
                    page.add(run_row(name, fn))
        if title != "Performance":
            page.col.addStretch()
        return page

    def run_row(self, name, fn):
        row = RunRow(name, next(t[2] for t in TASKS if t[3] is fn),
                     lambda _=False, n=name, f=fn: self.run_tasks([(n, f)]),
                     lambda _=False, n=name, f=fn: self.run_reverts([(n, f)]),
                     is_active(fn))
        self.action_rows.append((row, fn))
        return row

    def build_storage(self):
        page = Page("Storage")
        header = QHBoxLayout()
        page.col.addSpacing(26)
        label = QLabel("Drives")
        label.setObjectName("section")
        header.addWidget(label)
        header.addStretch()
        self.rescan_btn = Button("Rescan")
        self.rescan_btn.setFixedWidth(ROW_ACTION_BUTTON_WIDTH)
        self.rescan_btn.setEnabled(False)
        self.rescan_btn.clicked.connect(self.refresh_drives)
        header.addWidget(self.rescan_btn)
        page.col.addLayout(header)
        page.col.addSpacing(8)
        self.drive_box = QVBoxLayout()
        self.drive_box.setSpacing(2)
        page.col.addLayout(self.drive_box)

        page.section("Biggest apps and games")
        self.apps_box = QVBoxLayout()
        self.apps_box.setSpacing(2)
        page.col.addLayout(self.apps_box)

        page.section("Clean up")
        for cat, name, desc, fn in TASKS:
            if cat == "Storage":
                page.add(self.run_row(name, fn))
        page.col.addStretch()
        self.storage_apps = {}
        self.apps_expanded = False
        self.drive_rows = {}
        self.storage_ready.connect(self.show_usage)
        self.scan_finished.connect(lambda: self.rescan_btn.setEnabled(True))
        self.drives_ready.connect(self.show_drives)
        self.refresh_drives()
        return page

    def refresh_drives(self):
        self.rescan_btn.setEnabled(False)
        pre, self.prefetch = self.prefetch, None

        def work():
            self.drives_ready.emit(pre["drives"].result() if pre else storage.drives())
        threading.Thread(target=work, daemon=True).start()
        self.scan_source = pre

    def start_storage_prefetch(self):
        from concurrent.futures import Future
        pre = {"drives": Future(), "installed": Future(), "res": {}, "done": threading.Event()}

        def run():
            try:
                drives = storage.drives()
            except Exception:
                drives = []
            pre["drives"].set_result(drives)
            try:
                installed = storage.installed_apps()
            except Exception:
                installed = []
            for d in drives:
                try:
                    pre["res"][d[0]] = storage.analyze(d[0], installed)
                except Exception:
                    pre["res"][d[0]] = {"root": [], "user": [], "categories": [], "apps": []}
            pre["done"].set()
        threading.Thread(target=run, daemon=True).start()
        self.prefetch = pre

    def show_drives(self, items):
        roots = [i[0] for i in items]
        if roots != list(self.drive_rows):
            while self.drive_box.count():
                w = self.drive_box.takeAt(0).widget()
                if w:
                    w.deleteLater()
            self.drive_rows = {}
            self.storage_apps = {}
            for root, label, free, total in items:
                row = Row(f"{label} ({root[:2]})", "")
                legend = QLabel("")
                legend.setTextFormat(Qt.RichText)
                legend.setWordWrap(True)
                legend.setStyleSheet("font-size: 13px;")
                row.text_col.addWidget(legend)
                bar = Bar()
                bar.setFixedHeight(10)
                bar.setMinimumWidth(260)
                row.right.addWidget(bar)
                self.drive_box.addWidget(row)
                self.drive_rows[root] = {"row": row, "bar": bar, "legend": legend, "legend_html": "",
                                         "total": total, "free": free}
        for root, label, free, total in items:
            info = self.drive_rows[root]
            info.update(total=total, free=free)
            info["row"].sub.setText(f"{apps.human(total - free)} used · {apps.human(free)} free of {apps.human(total)}")
            info["row"].sub.show()
            if not info["legend_html"]:
                info["bar"].set_value(total_fraction(total, free))
                info["legend"].setText('<span style="color:#8a8a8a">Scanning...</span>')
            info["bar"].set_scanning(True)
        self.render_apps()

        pre, self.scan_source = self.scan_source, None

        def work():
            if pre:
                pre["done"].wait()
                for root in roots:
                    self.storage_ready.emit(root, pre["res"].get(
                        root, {"root": [], "user": [], "categories": [], "apps": []}))
                self.scan_finished.emit()
                return
            try:
                installed = storage.installed_apps()
            except Exception:
                installed = []
            for root in roots:
                try:
                    res = storage.analyze(root, installed)
                except Exception:
                    res = {"root": [], "user": [], "categories": [], "apps": []}
                self.storage_ready.emit(root, res)
            self.scan_finished.emit()
        threading.Thread(target=work, daemon=True).start()

    def show_drive_breakdown(self, root, categories):
        info = self.drive_rows.get(root)
        if not info:
            return
        info["bar"].set_scanning(False)
        total = info["total"] or 1
        if not categories:
            info["legend"].setText("")
            return
        info["bar"].set_segments([(size / total, CATEGORY_COLORS[name]) for name, size in categories])
        parts = [f'<span style="color:{CATEGORY_COLORS[name]}">●</span>&nbsp;{name}&nbsp;{apps.human(size)}'
                 for name, size in categories]
        parts.append(f'<span style="color:#3a3a3a">●</span>&nbsp;Free&nbsp;{apps.human(info["free"])}')
        info["legend_html"] = "&nbsp;&nbsp;&nbsp;".join(parts)
        info["legend"].setText(info["legend_html"])
    def uninstall_app(self, app, btn):
        if app["uri"]:
            os.startfile(app["uri"])
            return
        if btn.text() != "Confirm":
            btn.setText("Confirm")

            def reset():
                if isValid(btn) and btn.text() == "Confirm":
                    btn.setText("Uninstall")
            QTimer.singleShot(3000, reset)
            return
        btn.setText("Uninstall")
        subprocess.Popen(app["command"], shell=True)

    def show_usage(self, root, res):
        self.show_drive_breakdown(root, res.get("categories", []))
        self.storage_apps[root] = res.get("apps", [])
        self.render_apps()

    def render_apps(self):
        while self.apps_box.count():
            item = self.apps_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        everything = sorted((a for items in self.storage_apps.values() for a in items),
                            key=lambda a: a["size"], reverse=True)
        if not everything:
            hint = QLabel("Scanning your drives..." if any(
                self.drive_rows[r]["bar"].scanning for r in self.drive_rows)
                else "No large apps or games found.")
            hint.setObjectName("muted")
            hint.setContentsMargins(16, 0, 16, 0)
            self.apps_box.addWidget(hint)
            return
        shown = everything if self.apps_expanded else everything[:8]
        top = everything[0]["size"] or 1
        for app in shown:
            kind = "Store app" if app["uwp"] else ("Game" if app["game"] else "App")
            details = [kind] + ([app["publisher"]] if app["publisher"] else []) + (
                [app["location"]] if app["location"] else [])
            row = Row(app["name"], " · ".join(details))
            row.sub.setWordWrap(True)
            bar = Bar()
            bar.set_value(app["size"] / top)
            bar.setFixedWidth(120)
            sz = QLabel(apps.human(app["size"]))
            sz.setStyleSheet("font-size: 15px;")
            sz.setFixedWidth(70)
            sz.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            u = Button("Uninstall")
            u.setCursor(Qt.PointingHandCursor)
            u.setFixedWidth(ROW_ACTION_BUTTON_WIDTH)
            u.clicked.connect(lambda _=False, a=app, b=u: self.uninstall_app(a, b))
            row.right.addWidget(bar)
            row.right.addWidget(sz)
            row.right.addWidget(u)
            self.apps_box.addWidget(row)
        if len(everything) > 8:
            more = Button("Show fewer" if self.apps_expanded else f"Show all {len(everything)}")
            more.setFixedWidth(ROW_ACTION_BUTTON_WIDTH + 24)
            more.clicked.connect(self.toggle_apps)
            holder = QHBoxLayout()
            holder.setContentsMargins(16, 8, 0, 0)
            holder.addStretch()
            holder.addWidget(more)
            wrap = QWidget()
            wrap.setLayout(holder)
            self.apps_box.addWidget(wrap)

    def toggle_apps(self):
        self.apps_expanded = not self.apps_expanded
        self.render_apps()

    def show_path(self, path, is_dir):
        if is_dir:
            os.startfile(path)
        else:
            subprocess.Popen(f'explorer /select,"{path}"')

    def app_actions(self, pkg_id):
        def dl(job):
            return apps.download(pkg_id, self.cfg.download_dir, job)
        return [("Download", "Download", "download", dl),
                ("Install", "Installed", "install", lambda job: apps.install(pkg_id, job))]

    def build_apps(self):
        page = Page("Applications")
        page.col.setContentsMargins(0, 28, 0, 120)
        page.col.addSpacing(16)
        self.search_box = QLineEdit()
        self.search_box.setObjectName("search")
        self.search_box.setPlaceholderText("Search apps")
        self.search_box.textChanged.connect(self.filter_apps)
        self.app_groups = []
        self.no_match = QLabel("No matching apps.")
        self.no_match.setObjectName("section")
        self.no_match.hide()

        runtime = ActionRow(
            "C++ Redistributables", "Install missing and repair existing 2005 - 2022 (x86 + x64)",
            [("Install / repair", "Repaired", "install", apps.vcredists)], self,
            "kernel.vcredist", "microsoft.com")
        rbox = QWidget()
        rl = QVBoxLayout(rbox)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(2)
        rl.addSpacing(26)
        rs = QLabel("Runtimes")
        rs.setObjectName("section")
        rl.addWidget(rs)
        rl.addSpacing(6)
        rl.addWidget(runtime)
        page.col.addWidget(rbox)
        self.app_groups.append((rbox, [("c++ redistributables visual runtime vcredist", runtime)]))
        for cat in dict.fromkeys(c[0] for c in apps.CATALOG):
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(2)
            bl.addSpacing(26)
            s = QLabel(cat)
            s.setObjectName("section")
            bl.addWidget(s)
            bl.addSpacing(6)
            entries = []
            for _, name, pkg, domain in (c for c in apps.CATALOG if c[0] == cat):
                row = ActionRow(name, "", self.app_actions(pkg), self, pkg, domain)
                self.info_rows[pkg] = row
                self.info_q.put(pkg)
                bl.addWidget(row)
                entries.append((f"{cat} {name} {pkg}".lower(), row))
            page.col.addWidget(box)
            self.app_groups.append((box, entries))
        page.col.addWidget(self.no_match)
        page.col.addStretch()
        host = ApplicationsHost(page, self.search_box)
        return host

    def filter_apps(self, text):
        q = text.strip().lower()
        shown = 0
        for box, entries in self.app_groups:
            vis = 0
            for hay, row in entries:
                m = all(w in hay for w in q.split())
                row.setVisible(m)
                vis += m
            box.setVisible(vis > 0)
            shown += vis
        self.no_match.setVisible(bool(q) and shown == 0)

    def build_specs(self):
        page = Page("Specifications")
        self.add_monitor(page)

        page.col.addSpacing(26)
        header = QHBoxLayout()
        label = QLabel("Hardware")
        label.setObjectName("section")
        header.addWidget(label)
        header.addStretch()
        self.copy_specs_btn = Button("Copy all")
        self.copy_specs_btn.setFixedWidth(96)
        self.copy_specs_btn.setEnabled(False)
        self.copy_specs_btn.clicked.connect(self.copy_all_specs)
        header.addWidget(self.copy_specs_btn)
        page.col.addLayout(header)
        page.col.addSpacing(8)

        self.spec_rows = []
        self.spec_text = ""
        self.spec_placeholder = QLabel("Detecting your hardware...")
        self.spec_placeholder.setObjectName("muted")
        self.spec_placeholder.setContentsMargins(16, 0, 16, 0)
        page.col.addWidget(self.spec_placeholder)
        self.spec_box = QVBoxLayout()
        self.spec_box.setSpacing(2)
        page.col.addLayout(self.spec_box)
        self.update_row = None
        page.col.addStretch()
        self.specs_ready.connect(self.show_specs)
        self.updates_ready.connect(self.show_updates)

        def work():
            try:
                pairs, windows = specs.collect()
            except Exception as ex:
                self.specs_ready.emit([("Error", f"Could not detect specs ({ex})")], "")
                return
            self.specs_ready.emit(pairs, windows)
            self.updates_ready.emit(specs.check_updates())
        threading.Thread(target=work, daemon=True).start()
        return page

    def show_specs(self, pairs, windows):
        self.spec_placeholder.hide()
        lines = []
        for key, value in pairs:
            row = SpecRow(key, value)
            self.spec_box.addWidget(row)
            lines.append(f"{key}: " + value.replace("\n", "\n    "))
        if windows:
            button = Button("Windows Update")
            button.setFixedWidth(140)
            button.clicked.connect(lambda: os.startfile("ms-settings:windowsupdate"))
            self.spec_box.addWidget(SpecRow("Windows version", windows))
            self.update_row = SpecRow("Windows Update", "Checking for updates…", button, copyable=False)
            self.spec_box.addWidget(self.update_row)
            lines.append(f"Windows version: {windows}")
        self.spec_text = "\n".join(lines)
        self.copy_specs_btn.setEnabled(bool(lines))

    def show_updates(self, result):
        row = self.update_row
        if row is None:
            return
        if result is None:
            status = "Could not check for updates · open Windows Update to check manually"
        elif result[0] == 0:
            status = "Windows is up to date"
        else:
            count, titles = result
            noun = "update" if count == 1 else "updates"
            status = f"{count} {noun} available · {titles[0]}" if titles else f"{count} {noun} available"
            if count > 1:
                status += f" and {count - 1} more"
        row.set_value(status)
        row.set_attention(result is not None and result[0] > 0)

    def copy_all_specs(self):
        QApplication.clipboard().setText(self.spec_text)
        self.copy_specs_btn.setText("Copied")
        QTimer.singleShot(1200, lambda: self.copy_specs_btn.setText("Copy all"))

    def build_activity(self):
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(20, 28, 20, 28)
        t = QLabel("Activity")
        t.setObjectName("title")
        pl.addWidget(t)
        pl.addSpacing(16)
        self.log = LogView()
        self.log.setReadOnly(True)
        SmoothScroller(self.log)
        pl.addWidget(self.log, 1)
        return page

    def add_monitor(self, page):
        page.section("Live hardware metrics")
        self.monitor_status = QLabel("Starting hardware sensors…")
        self.monitor_status.setObjectName("muted")
        self.monitor_status.setWordWrap(True)
        self.monitor_status.setContentsMargins(16, 0, 16, 8)
        page.col.addWidget(self.monitor_status)

        self.monitor_cards = {}
        groups = (
            ("CPU", (
                ("cpu_package", "CPU package temperature"),
                ("cpu_hottest", "Hottest core"),
                ("cpu_temperature", "Core average"),
                ("cpu_clock", "CPU clock (avg / max)"),
                ("cpu_power", "CPU package power"),
                ("cpu_voltage", "CPU core voltage"),
            )),
            ("GPU", (
                ("gpu_temperature", "GPU temperature"),
                ("gpu_hotspot", "GPU hot spot"),
                ("gpu_memory_junction", "VRAM junction temperature"),
                ("gpu_clock", "GPU clock"),
                ("gpu_memory", "GPU memory clock"),
                ("gpu_load", "GPU load"),
                ("gpu_vram", "VRAM usage"),
                ("gpu_power", "GPU board power"),
                ("gpu_voltage", "GPU core voltage"),
                ("gpu_fan", "GPU fan speed"),
            )),
            ("Memory", (
                ("ram", "Memory usage"),
                ("ram_available", "Available memory"),
                ("ram_committed", "Committed memory"),
                ("ram_cached", "Cached memory"),
            )),
            ("System", (
                ("cpu_utilization", "CPU utilization"),
                ("processes", "Processes"),
                ("threads", "Threads"),
                ("handles", "Handles"),
                ("uptime", "Uptime"),
            )),
        )
        for group, items in groups:
            label = page.section(group)
            grid = QGridLayout()
            grid.setContentsMargins(13, 0, 0, 0)
            grid.setSpacing(10)
            for column in range(3):
                grid.setColumnStretch(column, 1)
            for index, (key, title) in enumerate(items):
                card = MetricCard(title)
                self.monitor_cards[key] = card
                grid.addWidget(card, index // 3, index % 3)
            page.col.addLayout(grid)
            if group == "CPU":
                self.core_temperature_section = page.section("CPU core temperatures")
                self.core_temperature_hint = QLabel("Per-core temperatures are hidden in average mode.")
                self.core_temperature_hint.setObjectName("muted")
                self.core_temperature_hint.setContentsMargins(16, 0, 16, 8)
                page.col.addWidget(self.core_temperature_hint)
                self.core_temperature_box = QWidget()
                self.core_temperature_grid = QGridLayout(self.core_temperature_box)
                self.core_temperature_grid.setContentsMargins(13, 0, 0, 0)
                self.core_temperature_grid.setSpacing(10)
                for column in range(3):
                    self.core_temperature_grid.setColumnStretch(column, 1)
                page.col.addWidget(self.core_temperature_box)

        per_core = self.cfg.cpu_temp_mode == "per_core"
        self.core_temperature_section.setVisible(per_core)
        self.core_temperature_hint.setVisible(per_core)
        self.core_temperature_box.setVisible(per_core)
        self.monitor_cards["cpu_temperature"].setVisible(not per_core)

        self.core_temperature_cards = []
        self.monitor_process = None
        self.monitor_thread = None
        self.monitor_lock = threading.Lock()
        self.monitor_started = False
        self.monitor_stop = threading.Event()
        self.monitor_sample.connect(self.update_monitor)
        self.monitor_error.connect(self.show_monitor_error)
        self.monitor_finished.connect(self.monitor_helper_finished)

    def monitor_helper_path(self):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(base, "monitor-helper", "kernel-monitor.exe"),
            os.path.join(os.environ.get("TEMP", ""), "kernel-monitor-build", "kernel-monitor.exe"),
        ]
        return next((path for path in candidates if os.path.isfile(path)), None)

    def start_monitor(self):
        if self.monitor_started:
            return
        path = self.monitor_helper_path()
        if not path:
            self.monitor_status.setText(
                "Hardware sensor helper is unavailable. Rebuild the app to include the monitor.")
            return
        self.monitor_started = True
        self.monitor_stop.clear()
        self.monitor_status.setText("Starting hardware sensors…")
        self.monitor_thread = threading.Thread(
            target=self.run_monitor_helper, args=(path,), name="kernel-monitor", daemon=True)
        self.monitor_thread.start()

    def run_monitor_helper(self, path):
        process = None
        exit_code = -1
        details = ""
        try:
            process = subprocess.Popen(
                [path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            with self.monitor_lock:
                self.monitor_process = process
            for raw in process.stdout:
                if self.monitor_stop.is_set():
                    break
                if not raw.strip():
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as ex:
                    self.monitor_error.emit(f"Sensor helper returned invalid data: {ex}")
                    continue
                if not isinstance(data, dict):
                    self.monitor_error.emit(
                        "Sensor helper returned an unexpected data format.")
                    continue
                self.monitor_sample.emit(data)
            if self.monitor_stop.is_set() and process.poll() is None:
                process.stdin.close()
            try:
                exit_code = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                exit_code = process.wait(timeout=2)
            details = process.stderr.read().strip()
        except OSError as ex:
            details = str(ex)
            self.monitor_error.emit(f"Could not start the hardware sensor helper: {ex}")
        finally:
            if process is not None:
                with self.monitor_lock:
                    self.monitor_process = None
                if process.stdout:
                    process.stdout.close()
                if process.stderr:
                    process.stderr.close()
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
            self.monitor_finished.emit(exit_code, details)

    def update_monitor(self, data):
        self.latest_monitor_data = data
        cards = self.monitor_cards

        def number(key):
            value = data.get(key)
            return value if isinstance(value, (int, float)) and math.isfinite(value) else None

        def show(card, key, fmt):
            value = number(key)
            cards[card].set_value("Unavailable" if value is None else fmt.format(value))

        show("cpu_utilization", "cpuLoadPercent", "{:.0f}%")
        show("cpu_power", "cpuPowerW", "{:.1f} W")
        show("cpu_voltage", "cpuVoltageV", "{:.3f} V")
        show("gpu_memory", "gpuMemoryClockMHz", "{:.0f} MHz")
        show("gpu_clock", "gpuClockMHz", "{:.0f} MHz")
        show("gpu_load", "gpuLoadPercent", "{:.0f}%")
        show("gpu_power", "gpuPowerW", "{:.1f} W")
        show("gpu_voltage", "gpuVoltageV", "{:.3f} V")
        show("gpu_fan", "gpuFanRpm", "{:.0f} RPM")
        clock, clock_max = number("cpuClockMHz"), number("cpuClockMaxMHz")
        if clock is None:
            cards["cpu_clock"].set_value("Unavailable")
        elif clock_max is not None:
            cards["cpu_clock"].set_value(f"{clock:.0f} / {clock_max:.0f} MHz")
        else:
            cards["cpu_clock"].set_value(f"{clock:.0f} MHz")
        self.set_temperature_card(cards["cpu_package"], number("cpuPackageTemperature"))
        self.set_temperature_card(cards["cpu_hottest"], number("cpuHottestCoreTemperature"))
        self.set_temperature_card(cards["gpu_temperature"], number("gpuTemperature"))
        self.set_temperature_card(cards["gpu_hotspot"], number("gpuHotSpotTemperature"))
        self.set_temperature_card(cards["gpu_memory_junction"], number("gpuMemoryJunctionTemperature"))

        vram_used, vram_total = number("gpuMemoryUsedMB"), number("gpuMemoryTotalMB")
        if vram_used is not None and vram_total:
            percent = vram_used * 100 / vram_total
            cards["gpu_vram"].set_value(
                f"{vram_used / 1024:.1f} / {vram_total / 1024:.1f} GB ({percent:.0f}%)")
            cards["gpu_vram"].set_alert(percent >= 90)
        else:
            cards["gpu_vram"].set_value("Unavailable")
            cards["gpu_vram"].set_alert(False)
        cards["ram"].set_value("Unavailable")
        cards["ram"].set_alert(False)
        used, total = number("ramUsedBytes"), number("ramTotalBytes")
        gb = 1024 ** 3
        if used is not None and total:
            percent = used * 100 / total
            cards["ram"].set_value(f"{used / gb:.1f} / {total / gb:.1f} GB ({percent:.0f}%)")
            cards["ram"].set_alert(percent >= 90)
        available, cached = number("ramAvailableBytes"), number("cachedBytes")
        cards["ram_available"].set_value(
            "Unavailable" if available is None else f"{available / gb:.1f} GB")
        cards["ram_cached"].set_value("Unavailable" if cached is None else f"{cached / gb:.1f} GB")
        commit, limit = number("commitTotalBytes"), number("commitLimitBytes")
        if commit is not None and limit:
            percent = commit * 100 / limit
            cards["ram_committed"].set_value(f"{commit / gb:.1f} / {limit / gb:.1f} GB ({percent:.0f}%)")
            cards["ram_committed"].set_alert(percent >= 90)
        else:
            cards["ram_committed"].set_value("Unavailable")
            cards["ram_committed"].set_alert(False)
        for card, key in (("processes", "processCount"), ("threads", "threadCount"),
                          ("handles", "handleCount")):
            count = number(key)
            cards[card].set_value("Unavailable" if count is None else f"{count:,.0f}")
        uptime = number("uptimeSeconds")
        if uptime is None:
            cards["uptime"].set_value("Unavailable")
        else:
            days, rest = divmod(int(uptime), 86400)
            hours, rest = divmod(rest, 3600)
            minutes, seconds = divmod(rest, 60)
            cards["uptime"].set_value(
                f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m {seconds}s")

        temperatures = data.get("cpuCoreTemperatures")
        if not isinstance(temperatures, list):
            temperatures = []
        temperatures = [value for value in temperatures
                        if isinstance(value, (int, float)) and math.isfinite(value)]
        average_card = self.monitor_cards["cpu_temperature"]
        if self.cfg.cpu_temp_mode == "average":
            average_card.show()
            self.core_temperature_section.hide()
            self.core_temperature_hint.hide()
            self.core_temperature_box.hide()
            if temperatures:
                self.set_temperature_card(average_card, sum(temperatures) / len(temperatures))
            else:
                self.set_temperature_card(average_card, None)
        else:
            average_card.hide()
            self.core_temperature_section.show()
            self.core_temperature_box.show()
            self.core_temperature_hint.setText(
                "Each available CPU core temperature is shown separately."
                if temperatures else "CPU core temperature sensors are unavailable.")
            self.core_temperature_hint.show()
            while len(self.core_temperature_cards) < len(temperatures):
                index = len(self.core_temperature_cards)
                card = MetricCard(f"Core {index + 1}")
                self.core_temperature_grid.addWidget(card, index // 3, index % 3)
                self.core_temperature_cards.append(card)
            for index, card in enumerate(self.core_temperature_cards):
                visible = index < len(temperatures)
                card.setVisible(visible)
                if visible:
                    self.set_temperature_card(card, temperatures[index])
        errors = data.get("hardwareErrors")
        if isinstance(errors, list) and errors:
            details = "; ".join(str(error) for error in errors[:3])
            self.monitor_status.setText(f"Live · some sensors unavailable: {details}")
        elif data.get("nvidiaGpuDetected") is False:
            self.monitor_status.setText(
                "NVIDIA GPU sensors not detected · CPU, memory, and process tracking remain active.")
        else:
            status = f"Live · updated {time.strftime('%H:%M:%S')}"
            if data.get("cpuClockMHz") is None:
                status += " · CPU clock unavailable"
            if data.get("cpuTemperatureAvailable") is False:
                if data.get("cpuDetected") is False:
                    status += " · CPU hardware not detected"
                elif data.get("isElevated") is False:
                    status += " · run as administrator for CPU sensor access"
                elif not self.pawnio_installed():
                    status += " · CPU temperature needs the free PawnIO driver (pawnio.eu), which is not installed"
                elif isinstance(data.get("cpuTemperatureSensorCount"), (int, float)) \
                        and data["cpuTemperatureSensorCount"] > 0:
                    status += " · CPU temperature sensors detected but returning no readings"
                else:
                    status += " · CPU temperature sensor unavailable"
            self.monitor_status.setText(status)

    @staticmethod
    def pawnio_installed():
        try:
            import winreg
            winreg.CloseKey(winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\PawnIO"))
            return True
        except OSError:
            return False

    @staticmethod
    def set_temperature_card(card, temperature):
        if temperature is None:
            card.set_value("Unavailable")
            card.set_temperature(None)
        elif temperature >= 80:
            card.set_value(f"{temperature:.0f} °C · HOT")
            card.set_temperature(temperature)
        else:
            card.set_value(f"{temperature:.0f} °C")
            card.set_temperature(temperature)

    def show_monitor_error(self, message):
        self.monitor_status.setText(message)

    def monitor_helper_finished(self, exit_code, details):
        self.monitor_started = False
        if exit_code == 0:
            return
        message = f"Hardware sensor helper stopped (exit code {exit_code})."
        if details:
            message = f"{message} {details}"
        self.monitor_status.setText(message)

    def build_settings(self):
        page = Page("Settings")
        page.section("Interface")
        smooth = TaskRow("Smooth scrolling", "Eased, inertial mouse-wheel scrolling")
        smooth.toggle.set_quiet(self.cfg.smooth_scroll)
        smooth.toggle.toggled.connect(self.set_smooth)
        self.smooth_toggle = smooth.toggle
        page.add(smooth)
        temp = Row(
            "CPU temperature tracking",
            "Show the average of available CPU core sensors or each core separately")
        self.cpu_temp_combo = FluentCombo()
        self.cpu_temp_combo.addItem("Average of cores", "average")
        self.cpu_temp_combo.addItem("Per core", "per_core")
        self.cpu_temp_combo.setCurrentIndex(self.cpu_temp_combo.findData(self.cfg.cpu_temp_mode))
        self.cpu_temp_combo.currentIndexChanged.connect(lambda: self.set_cpu_temp_mode())
        temp.right.addWidget(self.cpu_temp_combo)
        page.add(temp)
        page.section("Downloads")
        self.dl_row = Row("Download location", self.cfg.download_dir)
        for text, fn in (("Change", self.pick_dir), ("Open", self.open_dir)):
            b = Button(text)
            b.clicked.connect(fn)
            self.dl_row.right.addWidget(b)
        page.add(self.dl_row)
        page.section("Profile")
        prof = Row("Settings file", "Applied tweak states and download location are saved automatically")
        for text, fn in (("Export", self.export_settings), ("Import", self.import_settings)):
            b = Button(text)
            b.clicked.connect(fn)
            prof.right.addWidget(b)
        page.add(prof)
        page.col.addStretch()
        return page

    def set_cpu_temp_mode(self):
        self.cfg.cpu_temp_mode = self.cpu_temp_combo.currentData()
        self.cfg.save()
        if hasattr(self, "latest_monitor_data"):
            self.update_monitor(self.latest_monitor_data)

    def sync_nvidia_controls(self, apply=False):
        self.gamma_contrast_timer.stop()
        for slider, value in ((self.gamma_slider, self.cfg.nvidia_gamma),
                              (self.contrast_slider, self.cfg.nvidia_contrast)):
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)
        self.gamma_value.setText(f"{self.cfg.nvidia_gamma / 100:.2f}")
        self.contrast_value.setText(f"{self.cfg.nvidia_contrast}%")
        if (apply and self.nvidia_control is not None
                and self.nvidia_control.supports_gamma_contrast):
            self.apply_gamma_contrast()

    def set_smooth(self, on):
        SmoothScroller.on = on
        self.cfg.smooth_scroll = on
        self.cfg.save()

    # ---- download manager ----

    def add_job_row(self, job):
        row = JobRow(job, self)
        self.job_rows.insert(0, row)
        self.dl_list.insertWidget(0, row)
        self.dl_hint.setVisible(False)
        job.changed.connect(self.update_pill)
        self.update_pill()

    def clear_finished(self):
        for row in list(self.job_rows):
            if not row.job.active:
                self.job_rows.remove(row)
                if row.job in self.jobs.jobs:
                    self.jobs.jobs.remove(row.job)
                row.deleteLater()
        self.jobs.save()
        self.dl_hint.setVisible(not self.job_rows)

    # ---- settings ----

    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Download location", self.cfg.download_dir)
        if d:
            self.cfg.download_dir = os.path.normpath(d)
            self.dl_row.sub.setText(self.cfg.download_dir)
            self.cfg.save()

    def open_dir(self):
        os.makedirs(self.cfg.download_dir, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.cfg.download_dir))

    def export_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export settings", "kernel-settings.json", "JSON (*.json)")
        if path:
            self.save_toggle_states()
            ok = self.cfg.save(path)
            self.logged.emit(f"Settings exported to {path}" if ok else "Export failed.")

    def import_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import settings", "", "JSON (*.json)")
        if not path:
            return
        if not self.cfg.load(path):
            self.logged.emit("Import failed: not a valid settings file.")
            return
        self.applied_states = {}
        self.profile_pending = False
        for r, key, _, fn in self.rows:
            actual = is_active(fn)
            active = self.cfg.applied.get(key, actual)
            if key in IRREVERSIBLE and actual:
                active = True
            self.applied_states[key] = active
            r.set_active(active, self.cfg.toggles.get(key, False))
            self.profile_pending |= active != actual
        for row, fn in self.action_rows:
            row.set_active(is_active(fn))
        self.dl_row.sub.setText(self.cfg.download_dir)
        self.smooth_toggle.set_quiet(self.cfg.smooth_scroll)
        self.cpu_temp_combo.setCurrentIndex(self.cpu_temp_combo.findData(self.cfg.cpu_temp_mode))
        self.sync_nvidia_controls(apply=True)
        SmoothScroller.on = self.cfg.smooth_scroll
        self.update_status()
        self.save_toggle_states()
        self.cfg.save()
        self.logged.emit(f"Settings imported from {path}")

    def save_toggle_states(self):
        self.cfg.toggles = {key: r.toggle.on for r, key, _, _ in self.rows}
        self.cfg.applied = dict(self.applied_states)

    def update_status(self):
        n = sum(r.toggle.on and r.apply.isEnabled() for r, *_ in self.rows)
        if self.profile_pending:
            self.status.setText(f"{n} selected; imported profile ready to apply" if n
                                else "Imported profile ready to apply")
        else:
            self.status.setText(f"{n} selected" if n else "Nothing selected")
        self.btn.setEnabled(bool(n or self.profile_pending))

    def on_toggle(self, *_):
        self.update_status()
        self.save_toggle_states()
        if not (self.isMaximized() or self.isFullScreen()):
            self.cfg.win_size = [self.width(), self.height()]
        self.cfg.save()

    def closeEvent(self, e):
        self.save_toggle_states()
        if not (self.isMaximized() or self.isFullScreen()):
            self.cfg.win_size = [self.width(), self.height()]
        self.cfg.save()
        if getattr(self, "monitor_started", False):
            self.monitor_stop.set()
            with self.monitor_lock:
                process = self.monitor_process
            if process is not None and process.poll() is None:
                try:
                    process.stdin.close()
                except (AttributeError, OSError):
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.terminate()
            if self.monitor_thread is not None:
                self.monitor_thread.join(timeout=2)
        for j in self.jobs.jobs:
            j.request_cancel()
        super().closeEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        try:  # native dark title bar
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), 20, ctypes.byref(ctypes.c_int(1)), 4)
        except Exception:
            pass

    # ---- tweaks ----

    def irreversible_names(self, operations):
        return [name for name, fn, target in operations if target and fn.__name__ in IRREVERSIBLE]

    def start(self):
        targets = dict(self.applied_states) if self.profile_pending else {}
        for row, key, _, _ in self.rows:
            if row.toggle.on and row.apply.isEnabled():
                targets[key] = True
        operations = []
        for row, key, name, fn in self.rows:
            target = targets.get(key)
            if target is None or is_active(fn) == target:
                continue
            if not target and key in IRREVERSIBLE:
                continue
            operations.append((name, fn, target))
        if not operations:
            self.profile_pending = False
            self.update_status()
            return
        text = ("This will apply the selected tweaks and sync imported applied states. "
                "Some changes affect Windows system and security settings. Continue?")
        risky = self.irreversible_names(operations)
        if risky:
            text = ("These changes CANNOT be undone: " + ", ".join(risky) + ".\n\n" + text)
        answer = QMessageBox.question(
            self, "Apply selected changes", text,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.run_operations(operations)

    def run_tasks(self, sel):
        operations = [(name, fn, True) for name, fn in sel]
        risky = self.irreversible_names(operations)
        if risky and QMessageBox.warning(
                self, "This cannot be undone",
                f"{', '.join(risky)} cannot be reverted once applied. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.run_operations(operations)

    def run_reverts(self, sel):
        self.run_operations([(name, fn, False) for name, fn in sel])

    def run_operations(self, operations):
        if not operations or (self.tweak_job and self.tweak_job.active):
            return
        self.tweak_operation_fns = {fn for _, fn, _ in operations}
        self.btn.setEnabled(False)
        self.goto(ACTIVITY)

        def work(job):
            for name, fn, target in operations:
                job.log(f"== {'Apply ' if target else 'Revert '}{name}")
                try:
                    if is_active(fn) == target:
                        job.log(f"Skipped {name}: already {'active' if target else 'inactive'}.")
                    elif not target:
                        revert_tweak(fn, job.log)
                    else:
                        fn(job.log)
                except Exception as e:
                    tb = e.__traceback__
                    while tb.tb_next:
                        tb = tb.tb_next
                    job.log(f"FAILED: {e} (at {os.path.basename(tb.tb_frame.f_code.co_filename)}:"
                            f"{tb.tb_lineno} in {tb.tb_frame.f_code.co_name})")
            job.log("Done.")
            return True
        self.tweak_job = Job("Tweaks", "task", fn=work, hidden=True)
        self.tweak_job.changed.connect(self.tweaks_changed)
        self.jobs.submit(self.tweak_job)

    def tweaks_changed(self):
        if self.tweak_job and not self.tweak_job.active:
            for row, key, _, fn in self.rows:
                active = is_active(fn)
                previous = self.applied_states.get(key, False)
                if fn in self.tweak_operation_fns and active != previous:
                    self.applied_states[key] = active
                selected = False if previous and not self.applied_states[key] else None
                row.set_active(self.applied_states[key], selected)
            self.profile_pending = any(
                self.applied_states[key] != is_active(fn)
                for _, key, _, fn in self.rows)
            for row, fn in self.action_rows:
                row.set_active(is_active(fn))
            self.update_status()
            self.save_toggle_states()
            self.cfg.save()


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "windows:fontengine=freetype")
    if not is_admin():
        relaunch_as_admin()
        sys.exit()
    # own AppUserModelID so the taskbar shows kernel's icon instead of python's
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("kernel.app")
    app = QApplication(sys.argv)
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    app.setWindowIcon(QIcon(os.path.join(base, "icon.ico")))
    family = "Segoe UI Variable Text"
    fdir = os.path.join(base, "fonts")
    if os.path.isdir(fdir):
        for fn in sorted(os.listdir(fdir)):
            fid = QFontDatabase.addApplicationFont(os.path.join(fdir, fn))
            if fid >= 0 and QFontDatabase.applicationFontFamilies(fid):
                family = QFontDatabase.applicationFontFamilies(fid)[0]
    QSS = QSS.replace("'Segoe UI Variable Text'", f"'{family}', 'Segoe UI Variable Text'")
    base_font = QFont(family, 11)
    base_font.setStyleStrategy(QFont.PreferAntialias | QFont.NoSubpixelAntialias)
    base_font.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(base_font)
    app.setStyleSheet(QSS)
    w = Kernel()
    w.show()
    sys.exit(app.exec())
