"""kernel - Windows 11 debloat & optimize tool. Build with build-installer.bat or build-portable.bat."""
import ctypes
from ctypes import wintypes
import html
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from PySide6.QtCore import (
    Property, QEasingCurve, QEvent, QObject, QParallelAnimationGroup, QPoint, QPointF, QVariantAnimation,
    QPropertyAnimation, QRect, QRectF, Qt, QTimer, QUrl, Signal, QSize,
)
from PySide6.QtGui import (
    QBrush, QColor, QCursor, QDesktopServices, QFont, QFontDatabase, QGradient, QFontMetrics, QFontMetricsF, QIcon, QTransform, QImage, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QRegion,
)
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QButtonGroup, QFileDialog, QFrame, QGraphicsBlurEffect, QGraphicsDropShadowEffect, QGraphicsEffect,
    QGraphicsOpacityEffect,
    QComboBox, QGridLayout, QGraphicsPixmapItem, QGraphicsScene, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSlider, QStackedWidget, QStyle, QStyledItemDelegate, QSizePolicy,
    QVBoxLayout, QWidget,
)
from shiboken6 import isValid

import apps
import display_controls
import specs
import storage
from version import VERSION, UPDATE_REPO
from jobs import ACTIVE, Job, JobManager
from tweaks import TASKS, ONE_SHOT, IRREVERSIBLE, is_active, revert_tweak

BG, BORDER, TEXT, MUTED = "#000000", "#1a1a1a", "#ffffff", "#7a7a7a"
ROW_ACTION_BUTTON_WIDTH = 96
CUSTOM_TITLEBAR = True  # False = back to the normal Windows title bar
TITLEBAR_H, CAPTION_W, TITLEBAR_SOFT, SOFT_HOLD, SIDE_FADE = 40, 46 * 3, 56, 4, 56
TINT = 130  # darkness (0-255) of the title bar blur
BAND = 2  # rows blended per step when composing the progressive blur
CACHE_MARGIN = 120  # rows of page content blurred above/below the title bar zone, so small scrolls need no re-blur
ACCENTS = (("White", "#ffffff"), ("Blue", "#5b9cff"), ("Violet", "#a78bfa"), ("Pink", "#ff7ab8"),
           ("Green", "#4fd08a"), ("Amber", "#ffb454"), ("Red", "#ff6b6b"))
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
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #1f1f1f; border-radius: 3px; min-height: 40px; margin: 0 3px; }}
QScrollBar::handle:vertical:hover {{ background: #3a3a3a; }}
QScrollBar::handle:vertical:pressed {{ background: #4d4d4d; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QComboBox {{ background: transparent; border: none; padding: 10px 4px 10px 18px;
            font-size: 15px; min-width: 120px; }}
QComboBox::drop-down {{ border: none; width: 34px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{ background: transparent; border: none; padding: 6px; outline: none; }}
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
        self.applied = {}
        self.smooth_scroll = True
        self.cpu_temp_mode = "average"
        self.caption_style = "windows"
        self.accent = ACCENTS[0][1]
        self.nvidia_displays = {}
        self.legacy_display = None
        self.win_size = None
        self.download_dir = os.path.join(os.path.expanduser("~"), "Downloads", "kernel")
        self.load(self.path)

    def load(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return False
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
        if d.get("caption_style") in ("windows", "mac"):
            self.caption_style = d["caption_style"]
        if isinstance(d.get("accent"), str) and d["accent"].lower() in [c for _, c in ACCENTS]:
            self.accent = d["accent"].lower()
        gamma_ok = lambda g: type(g) is int and 40 <= g <= 280
        contrast_ok = lambda c: type(c) is int and 0 <= c <= 100
        if gamma_ok(d.get("nvidia_gamma")) and contrast_ok(d.get("nvidia_contrast")):
            self.legacy_display = (d["nvidia_gamma"], d["nvidia_contrast"])
        if isinstance(d.get("nvidia_displays"), dict):
            self.nvidia_displays = {
                k: {"gamma": v["gamma"], "contrast": v["contrast"]}
                for k, v in d["nvidia_displays"].items()
                if isinstance(k, str) and isinstance(v, dict)
                and gamma_ok(v.get("gamma")) and contrast_ok(v.get("contrast"))}
        ws = d.get("window")
        if isinstance(ws, list) and len(ws) == 2 and all(isinstance(x, int) for x in ws):
            self.win_size = ws
        return True

    def save(self, path=None):
        path = path or self.path
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"applied": self.applied,
                           "download_dir": self.download_dir,
                           "smooth_scroll": self.smooth_scroll,
                           "cpu_temp_mode": self.cpu_temp_mode,
                           "accent": self.accent,
                           "caption_style": self.caption_style,
                           "nvidia_displays": self.nvidia_displays,
                           "window": self.win_size}, f, indent=2)
            return True
        except OSError:
            return False


# ---------- small widgets ----------

def lerp(a, b, t):
    return a + (b - a) * t


def draw_spotlight(p, path, rect, center, strength, radius, alpha):
    """Soft white glow around `center`, clipped to `path`; `strength` (0..1) is the animated hover amount.
    Buttons, the closed dropdown and the dropdown rows all use this so they share one highlight look."""
    a = alpha * strength
    if a < 0.05:
        return
    glow = QRadialGradient(center, radius)
    for stop, k in ((0.0, 1.0), (0.3, 0.55), (0.65, 0.16), (1.0, 0.0)):
        c = QColor(255, 255, 255)
        c.setAlphaF(a * k / 255.0)
        glow.setColorAt(stop, c)
    p.save()
    p.setClipPath(path)
    p.fillRect(rect, QBrush(glow))
    p.restore()


BUTTON_GLOW, DROPDOWN_GLOW = 17, 14  # peak glow alpha; the dropdown stays a step dimmer than buttons


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
        self.go(self.a_hover, 1.0, 420, QEasingCurve.InOutSine)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.go(self.a_hover, 0.0, 520, QEasingCurve.InOutSine)
        super().leaveEvent(e)

    def mouseMoveEvent(self, e):
        self.go(self.a_cursor, QPointF(e.position()), 240, QEasingCurve.OutCubic)
        super().mouseMoveEvent(e)

    def snap_cursor(self, pos):
        self.a_cursor.stop()
        self.set_cursor_position(QPointF(pos))

    def spotlight(self, p, path, radius, alpha):
        draw_spotlight(p, path, self.rect(), self._cursor_pos, self._hover, radius, alpha)

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
        "ghost": dict(base=(11, 11, 11), text=(240, 240, 240), size=15, weight=QFont.Normal,
                      px=22, py=11, r=11, dis=(8, 8, 8), dis_text=(90, 90, 90)),
        "accent": dict(base=(236, 90, 190), text=(255, 255, 255), size=16, weight=QFont.Medium,
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
            base = k["base"]
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
            if on and not lit:
                inner = QPainterPath()  # inside the 1px border, so the stroke itself never changes
                inner.addRoundedRect(r.adjusted(1, 1, -1, -1), k["r"] - 1, k["r"] - 1)
                self.spotlight(p, inner, 95, BUTTON_GLOW)
            edge = QPainterPath()
            edge.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), k["r"], k["r"])
            p.setPen(QPen(QColor(255, 255, 255, 22), 1))
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
        self.spotlight(p, path, 150, BUTTON_GLOW * 0.85 * (1 - s))
        edge = h * 0.3 * (1 - s)  # faint hover border; the selected highlight is drawn by the sidebar
        if edge > 0.01:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, int(30 * edge)), 1))
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
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

class WindowState:
    """Set while the window is being dragged or resized: heavy background work (blur grabs, typing
    animation) pauses so the move stays smooth and the UI doesn't twitch."""
    moving = False  # a drag or resize is in progress
    dragging = False  # ...and it is a plain title-bar drag (a move, not a resize)


class WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND), ("x", ctypes.c_int),
                ("y", ctypes.c_int), ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]


class HoverFrame(QFrame):
    """Row background that fades in on hover. Only one frame is lit at a time, and a watcher clears the
    highlight if the pointer left without the leave event arriving (the downloads panel does that)."""
    HOVER_LEVEL = 15  # brightness (0-255) of the hovered background
    active = None
    watch = None

    @staticmethod
    def check():
        a = HoverFrame.active
        if a is None or not isValid(a):
            HoverFrame.active = None
            HoverFrame.watch.stop()
            return
        if not a.isVisible() or not a.rect().contains(a.mapFromGlobal(QCursor.pos())):
            a.fade(0.0, 240)
            HoverFrame.active = None
            HoverFrame.watch.stop()

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
        prev = HoverFrame.active
        if prev is not None and prev is not self and isValid(prev):
            prev.fade(0.0, 200)  # hand the highlight over: never two lit rows
        HoverFrame.active = self
        if HoverFrame.watch is None:
            HoverFrame.watch = QTimer()
            HoverFrame.watch.setInterval(120)
            HoverFrame.watch.timeout.connect(HoverFrame.check)
        if not HoverFrame.watch.isActive():
            HoverFrame.watch.start()
        self.fade(1.0, 160)
        super().enterEvent(e)

    def leaveEvent(self, e):
        if HoverFrame.active is self:
            HoverFrame.active = None
        self.fade(0.0, 240)
        super().leaveEvent(e)

    def paintEvent(self, e):
        if self._h > 0.001:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            # translucent white, so the fade-out blends smoothly into whatever is behind the row and
            # never passes through a gray that is darker than the panel (that looked like a lingering box)
            p.setBrush(QColor(255, 255, 255, int(self.HOVER_LEVEL * self._h)))
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
        lay.addWidget(self.value, 1, Qt.AlignTop)
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


def version_tuple(v):
    parts = []
    for p in str(v).lstrip("vV").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


# Fixed gap between a row's text and its buttons; the text column takes whatever width is left,
# so the gap stays the same at every window size.
ROW_TEXT_GAP = 48


class Row(HoverFrame):
    def __init__(self, title, subtitle="", icon=None, right_margin=16):
        super().__init__()
        self.setObjectName("row")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12 if icon else 16, right_margin, 12 if icon else 16)
        lay.setSpacing(14)
        if icon:
            lay.addWidget(icon)
        text_box = QWidget()
        text = QVBoxLayout(text_box)
        text.setContentsMargins(0, 0, ROW_TEXT_GAP, 0)
        text.setSpacing(2)
        self.title = title
        t = QLabel(title)
        t.setStyleSheet("font-size: 17px;")
        t.setWordWrap(True)
        self.sub = QLabel(subtitle)
        self.sub.setObjectName("muted")
        self.sub.setWordWrap(True)
        if not subtitle:
            self.sub.hide()
        text.addWidget(t)
        text.addWidget(self.sub)
        self.text_col = text
        lay.addWidget(text_box, 1)
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
        if self.apply is not None and self.revert is None:  # nothing to undo: keep Revert in its place, disabled
            self.revert = Button("Revert")
            self.revert.setFixedWidth(ROW_ACTION_BUTTON_WIDTH)
            self.revert.setEnabled(False)
            self.reversible = False
            self.right.addWidget(self.revert)
        self.toggle = None
        if on_apply is None:  # a row without an Apply button (Settings) is controlled by its switch
            self.toggle = Toggle()
            self.right.addWidget(self.toggle)

    def set_active(self, active):
        if self.apply is None:
            return
        self.apply.setText("Applied" if active else "Apply")
        self.apply.setEnabled(not active)
        self.revert.setEnabled(active and self.reversible)


class RunRow(Row):
    """Single-use action: one click runs it."""

    def __init__(self, title, subtitle, on_run, active=False):
        super().__init__(title, subtitle)
        self.sub.setWordWrap(True)
        self.b = Button("Run")
        self.b.setCursor(Qt.PointingHandCursor)
        self.b.setFixedWidth(ROW_ACTION_BUTTON_WIDTH)
        self.b.clicked.connect(on_run)
        self.right.addWidget(self.b)  # Run sits where Apply normally does
        self.revert = Button("Revert")  # single-use actions can't be undone: Revert stays in its place, disabled
        self.revert.setFixedWidth(ROW_ACTION_BUTTON_WIDTH)
        self.revert.setEnabled(False)
        self.right.addWidget(self.revert)
        self.set_active(active)

    def set_active(self, active):
        self.b.setText("Applied" if active else "Run")
        self.b.setEnabled(not active)


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


class ElidedLabel(QLabel):
    """Single-line label that truncates with an ellipsis (full text in the tooltip) instead of widening its parent."""

    def __init__(self, text=""):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, _):
        p = QPainter(self)
        r = self.contentsRect()
        p.setPen(self.palette().windowText().color())
        p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter,
                   self.fontMetrics().elidedText(self.text(), Qt.ElideRight, r.width()))


class JobRow(HoverFrame):
    HOVER_LEVEL = 18  # alpha of the white hover wash; a bit lighter than other rows

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
        name = ElidedLabel(job.name)
        name.setStyleSheet("font-size: 17px;")
        self.bar = Bar()
        self.status = ElidedLabel()
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


class ColorSwatches(QWidget):
    """Row of round colour swatches; hover grows a swatch, the selection ring fades between them."""
    changed = Signal(str)
    D, GAP, H = 22, 12, 34

    def __init__(self, colors, current):
        super().__init__()
        self.colors = [c for _, c in colors]
        self.names = [n for n, _ in colors]
        self.current = self.colors.index(current) if current in self.colors else 0
        n = len(self.colors)
        self.hv = [0.0] * n
        self.sv = [1.0 if i == self.current else 0.0 for i in range(n)]
        self.hover = -1
        self.setMouseTracking(True)
        self.setFixedSize(n * self.D + (n - 1) * self.GAP + 8, self.H)
        self.tick = QTimer(self)
        self.tick.setInterval(16)
        self.tick.timeout.connect(self.step)

    def centre(self, i):
        return QPointF(4 + self.D / 2 + i * (self.D + self.GAP), self.H / 2)

    def step(self):
        moving = False
        for i in range(len(self.colors)):
            for arr, goal in ((self.hv, 1.0 if i == self.hover else 0.0), (self.sv, 1.0 if i == self.current else 0.0)):
                d = goal - arr[i]
                if abs(d) > 0.01:
                    arr[i] += d * 0.28
                    moving = True
                else:
                    arr[i] = goal
        if not moving:
            self.tick.stop()
        self.update()

    def at(self, pos):
        for i in range(len(self.colors)):
            c = self.centre(i)
            if (pos.x() - c.x()) ** 2 + (pos.y() - c.y()) ** 2 <= (self.D / 2 + self.GAP / 2) ** 2:
                return i
        return -1

    def mouseMoveEvent(self, e):
        i = self.at(e.position())
        if i != self.hover:
            self.hover = i
            self.setCursor(Qt.PointingHandCursor if i >= 0 else Qt.ArrowCursor)
            self.setToolTip(self.names[i] if i >= 0 else "")
            self.tick.start()

    def leaveEvent(self, e):
        self.hover = -1
        self.tick.start()

    def mousePressEvent(self, e):
        i = self.at(e.position())
        if e.button() == Qt.LeftButton and i >= 0 and i != self.current:
            self.current = i
            self.tick.start()
            self.changed.emit(self.colors[i])

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for i, hexcolor in enumerate(self.colors):
            c = self.centre(i)
            r = self.D / 2 - 2 + 1.5 * self.hv[i] - 1.5 * self.sv[i]
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(hexcolor))
            p.drawEllipse(c, r, r)
            if self.sv[i] > 0.01:
                ring = QColor(hexcolor)
                ring.setAlphaF(self.sv[i])
                p.setPen(QPen(ring, 1.6))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(c, self.D / 2 + 1.5, self.D / 2 + 1.5)


class IOSScrollBar(QWidget):
    """iOS-style scrollbar: a floating pill over the content that appears while scrolling or hovering
    and fades out when idle. Drag the pill, or click the strip to jump."""
    def __init__(self, area, top=6, bottom=6, pill=(5, 7), strip=14):
        super().__init__(area)
        self.PILL, self.PILL_HOVER, self.STRIP = pill[0], pill[1], strip
        self.area, self.bar = area, area.verticalScrollBar()
        self.top, self.bottom = top, bottom
        self._op = 0.0
        self.hover = False
        self.drag = None  # (mouse y, scroll value) while dragging
        self.setMouseTracking(True)
        self.setCursor(Qt.ArrowCursor)
        self.fade = QVariantAnimation(self)
        self.fade.setEasingCurve(QEasingCurve.OutCubic)
        self.fade.valueChanged.connect(self._set_op)
        self._g = 0.0  # 0 = resting size, 1 = hovered/dragged size
        self.grow = QVariantAnimation(self)
        self.grow.setEasingCurve(QEasingCurve.OutCubic)
        self.grow.valueChanged.connect(self._set_g)
        self.idle = QTimer(self)
        self.idle.setSingleShot(True)
        self.idle.setInterval(900)
        self.idle.timeout.connect(self.dim)
        self.bar.valueChanged.connect(self.poke)
        self.bar.rangeChanged.connect(self.place)
        area.installEventFilter(self)
        self.place()

    def eventFilter(self, obj, e):
        if obj is self.area:
            if e.type() == QEvent.Resize:
                self.place()
            elif e.type() == QEvent.Show:  # flash the pill when the app or a page opens
                QTimer.singleShot(0, self.poke)
        return False

    def place(self, *_):
        a = self.area
        self.setGeometry(a.width() - self.STRIP, self.top, self.STRIP, max(0, a.height() - self.top - self.bottom))
        was = self.isVisible()
        self.setVisible(self.bar.maximum() > self.bar.minimum())
        self.raise_()
        self.update()
        if self.isVisible() and not was:  # content just became scrollable
            self.poke()

    def _set_op(self, v):
        self._op = float(v)
        self.update()

    def _set_g(self, v):
        self._g = float(v)
        self.update()

    def _sync_grow(self):
        target = 1.0 if (self.hover or self.drag) else 0.0
        if self.grow.state() == QVariantAnimation.Running and self.grow.endValue() == target:
            return
        self.grow.stop()
        self.grow.setStartValue(self._g)
        self.grow.setEndValue(target)
        self.grow.setDuration(180)
        self.grow.start()

    def _fade_to(self, target, ms):
        self.fade.stop()
        self.fade.setStartValue(self._op)
        self.fade.setEndValue(target)
        self.fade.setDuration(ms)
        self.fade.start()

    def poke(self, *_):
        """Show the pill and restart the idle countdown."""
        if not self.isVisible():
            return
        if self._op < 1.0 and not (self.fade.state() == QVariantAnimation.Running and self.fade.endValue() == 1.0):
            self._fade_to(1.0, 120)
        self.idle.start()

    def dim(self):
        if self.hover or self.drag:
            return
        self._fade_to(0.0, 450)

    def handle(self):
        h = self.height()
        rng = self.bar.maximum() - self.bar.minimum()
        total = rng + self.bar.pageStep()
        size = max(44.0, h * self.bar.pageStep() / total) if total > 0 else h
        y = (h - size) * (self.bar.value() - self.bar.minimum()) / rng if rng > 0 else 0.0
        return y, size

    def paintEvent(self, _):
        if self._op <= 0.01:
            return
        y, size = self.handle()
        g = self._g
        w = self.PILL + (self.PILL_HOVER - self.PILL) * g
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self._op)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, int(70 + 50 * g)))
        p.drawRoundedRect(QRectF(self.width() - w - 3, y, w, size), w / 2, w / 2)

    def enterEvent(self, e):
        self.hover = True
        self.idle.stop()
        self._fade_to(1.0, 120)
        self._sync_grow()

    def leaveEvent(self, e):
        self.hover = False
        self.idle.start()
        self._sync_grow()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        y, size = self.handle()
        if not (y <= e.position().y() <= y + size):  # click on the strip: centre the pill there
            self.scroll_to(e.position().y() - size / 2, size)
            y, size = self.handle()
        self.drag = (e.position().y(), self.bar.value())
        self._sync_grow()

    def mouseMoveEvent(self, e):
        if not self.drag:
            return
        y0, v0 = self.drag
        _, size = self.handle()
        track = self.height() - size
        rng = self.bar.maximum() - self.bar.minimum()
        if track > 0:
            self.bar.setValue(int(v0 + (e.position().y() - y0) * rng / track))

    def mouseReleaseEvent(self, e):
        self.drag = None
        self.idle.start()
        self._sync_grow()

    def scroll_to(self, y, size):
        track = self.height() - size
        rng = self.bar.maximum() - self.bar.minimum()
        if track > 0:
            self.bar.setValue(int(self.bar.minimum() + max(0.0, min(1.0, y / track)) * rng))

    def wheelEvent(self, e):
        QApplication.sendEvent(self.area.viewport(), e)  # keep the eased wheel scrolling over the strip


class Page(QScrollArea):
    def __init__(self, title):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("content")
        self.setWidget(content)
        SmoothScroller(self)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # the floating pill replaces it
        self.ios_bar = IOSScrollBar(self, top=TITLEBAR_H + 6 if CUSTOM_TITLEBAR else 6)
        outer = QHBoxLayout(content)
        outer.setContentsMargins(20, 0, 20, 0)
        col = QWidget()
        outer.addWidget(col, 1)
        self.col = QVBoxLayout(col)
        self.col.setContentsMargins(0, 48 + (TITLEBAR_H - 16 if CUSTOM_TITLEBAR else 0), 0, 36)
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


class FlatLabel(QLabel):
    """Muted wrapped text drawn as vector outlines. Normal text rendering changes weight with whatever is behind
    the text (the frosted panel is translucent, so parts of one line looked bolder than others); outlines give
    every glyph the same weight and gamma, on any background."""

    def __init__(self, text, size=14, color=MUTED):
        super().__init__(text)
        self._color = QColor(color)
        font = self.font()
        font.setPixelSize(size)
        font.setWeight(QFont.Medium)
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def wrapped(self, width):
        fm = QFontMetricsF(self.font())
        lines, cur = [], ""
        for word in self.text().split(" "):
            trial = (cur + " " + word).strip()
            if cur and fm.horizontalAdvance(trial) > width:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        if cur:
            lines.append(cur)
        return lines

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        m = self.contentsMargins()
        n = len(self.wrapped(max(1, width - m.left() - m.right())))
        return int(n * QFontMetricsF(self.font()).lineSpacing() + 0.999) + m.top() + m.bottom()

    def sizeHint(self):
        m = self.contentsMargins()
        fm = QFontMetricsF(self.font())
        return QSize(int(fm.horizontalAdvance(self.text())) + m.left() + m.right(), self.heightForWidth(10 ** 6))

    def minimumSizeHint(self):
        return QSize(10, self.heightForWidth(10 ** 6))

    def paintEvent(self, _):
        m = self.contentsMargins()
        fm = QFontMetricsF(self.font())
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self._color, 0.35)  # a hairline stroke on top of the fill makes the text a touch bolder
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(self._color)
        for i, line in enumerate(self.wrapped(max(1, self.width() - m.left() - m.right()))):
            path = QPainterPath()
            path.addText(QPointF(m.left(), m.top() + fm.ascent() + i * fm.lineSpacing()), self.font(), line)
            p.drawPath(path)


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
        # Source mode replaces whatever is underneath, so a partial repaint (a hovered row) can never
        # stack another tinted layer on top of the old one and leave dark boxes behind.
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillPath(path, QColor(0, 0, 0, 255))
        if self.bg:
            k = self.bg.width() / max(self.window().width(), 1)
            g = self.geometry()
            p.drawPixmap(QRectF(self.rect()), self.bg, QRectF(g.x() * k, g.y() * k, g.width() * k, g.height() * k))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
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


class ClearButton(QAbstractButton):
    """Small X that wipes a search field; the glyph brightens on hover."""
    SIZE = 28

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("Clear")
        self._hover = 0.0
        self.anim = QPropertyAnimation(self, b"hover", self)
        self.anim.setEasingCurve(QEasingCurve.InOutSine)

    def get_hover(self):
        return self._hover

    def set_hover(self, v):
        self._hover = v
        self.update()

    hover = Property(float, get_hover, set_hover)

    def glide(self, end, ms):
        self.anim.stop()
        self.anim.setDuration(ms)
        self.anim.setStartValue(self._hover)
        self.anim.setEndValue(end)
        self.anim.start()

    def enterEvent(self, e):
        self.glide(1.0, 160)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.glide(0.0, 220)
        super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QRectF(self.rect()).center()
        v = round(lerp(120, 235, self._hover))
        p.setPen(QPen(QColor(v, v, v), 1.6, Qt.SolidLine, Qt.RoundCap))
        d = 4.5
        p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
        p.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))


class SearchDock(QWidget):
    """Search field pinned over the bottom of a scrolling page, frosted over whatever scrolls behind it."""
    H, GAP = 60, 37
    MAX_GROW = 28  # px of extra width when focused
    GROW_H = 0.05  # extra height at full scale (fraction)
    HOVER_SCALE = 0.65  # how much of the full grow a hover reaches
    WIDTH = 700  # the dock is a fixed 700px wide and stays centred (shrinks only on narrow windows)
    SUGGESTIONS = ("OBS Studio", "Discord", "Spotify", "Steam", "VLC", "Visual Studio Code", "7-Zip",
                   "Firefox", "Notepad++", "Audacity", "Python")

    def __init__(self, page, edit):
        super().__init__(page.parentWidget())
        self.page = page
        self.edit = edit
        self._focus = 0.0
        self._base_rect = QRect()
        self.backdrop = QPixmap()
        self.backdrop_src = QRectF()
        self.backdrop_k = 1.0
        self.focus_anim = QVariantAnimation(self)  # focus scale: one smooth ease, no bounce
        self.focus_anim.setDuration(380)
        curve = QEasingCurve(QEasingCurve.BezierSpline)
        curve.addCubicBezierSegment(QPointF(0.32, 0.0), QPointF(0.15, 1.0), QPointF(1.0, 1.0))
        self.focus_anim.setEasingCurve(curve)
        self.focus_anim.valueChanged.connect(self._set_focus)
        self._hover, self._hvel, self._htarget, self._hlast, self._hfresh = 0.0, 0.0, 0.0, 0.0, False
        self._hgoal = 0.0  # the smoothed hover decision the spring follows
        self.hover_timer = QTimer(self)  # hover scale chases its target (no bounce), keeping its momentum
        self.hover_timer.setTimerType(Qt.PreciseTimer)
        self.hover_timer.timeout.connect(self.hover_step)
        edit.setParent(self)
        edit.setFrame(False)
        edit.setStyleSheet("QLineEdit { background: transparent; border: none; padding: 0 54px 0 22px; "
                           "font-size: 18px; selection-background-color: #444444; }")
        self.clear_btn = ClearButton(self)
        self.clear_btn.clicked.connect(self.clear_search)
        self.clear_btn.setVisible(bool(edit.text()))
        edit.textChanged.connect(lambda text: self.clear_btn.setVisible(bool(text)))
        self.tick = QTimer(self)
        self.tick.setInterval(250)
        self.tick.timeout.connect(self.refresh)
        self.tick.start()
        page.verticalScrollBar().valueChanged.connect(self.capture_backdrop)
        app = QApplication.instance()
        if app:
            app.focusChanged.connect(self.focus_changed)
        self.edit.installEventFilter(self)
        self.typing_i, self.typing_n, self.typing_mode = 0, 0, "type"
        self.ph_x = 30.0
        self.ph_text, self.ph_alpha = "", 1.0  # the animated placeholder is drawn by the dock, not the QLineEdit
        edit.setPlaceholderText("")
        self.ph_anim = QVariantAnimation(self)
        self.ph_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.ph_anim.valueChanged.connect(self._set_ph_alpha)
        edit.textChanged.connect(lambda _t: self.fade_placeholder(self.edit.hasFocus()))
        self.typer = QTimer(self)
        self.typer.setSingleShot(True)
        self.typer.timeout.connect(self.type_step)
        self.typer.start(900)

    def _set_ph_alpha(self, v):
        self.ph_alpha = float(v)
        self.update()

    def fade_placeholder(self, focused):
        """Placeholder stays (and keeps typing) while the field is empty, even when focused; it fades out
        once the user types something and back in when the field is emptied."""
        target = 0.0 if self.edit.text() else 1.0
        if abs(self.ph_alpha - target) < 0.01 and self.ph_anim.state() != QVariantAnimation.Running:
            return
        self.ph_anim.stop()
        self.ph_anim.setStartValue(self.ph_alpha)
        self.ph_anim.setEndValue(target)
        self.ph_anim.setDuration(70 if target == 0.0 else 320)  # vanish almost instantly when typing starts
        self.ph_anim.start()

    def type_step(self):
        """Placeholder typewriter: types an app name, holds, deletes it slowly, moves to the next."""
        word = self.SUGGESTIONS[self.typing_i % len(self.SUGGESTIONS)]
        delay = 600
        if not self.edit.text() and self.isVisible() and not WindowState.moving:
            if self.typing_mode == "type":
                self.typing_n += 1
                delay = 70 + (self.typing_n * 37) % 45  # uneven keystrokes feel human
                if self.typing_n >= len(word):
                    self.typing_mode, delay = "hold", 1500
            elif self.typing_mode == "hold":
                self.typing_mode, delay = "delete", 200
            else:
                self.typing_n -= 1
                delay = 55  # slow delete
                if self.typing_n <= 0:
                    self.typing_n, self.typing_mode, delay = 0, "type", 450
                    self.typing_i += 1
            self.ph_text = word[:self.typing_n]
            self.update()
        self.typer.start(delay)

    def focus_changed(self, old, new):
        if old is self.edit or new is self.edit:
            self.animate_focus(new is self.edit)
            self.fade_placeholder(new is self.edit)

    def eventFilter(self, obj, event):
        if obj is self.edit and event.type() == QEvent.MouseButtonPress:
            self.edit.setFocus(Qt.MouseFocusReason)
            self.animate_focus(True)
        return False

    def clear_search(self):
        self.edit.clear()
        self.edit.setFocus(Qt.MouseFocusReason)

    def _set_focus(self, v):
        self._focus = float(v)
        self.update_geometry()
        self.update()

    def animate_hover(self, hovered):
        """Enter/leave events only wake the follower; it reads the cursor itself (see hover_step)."""
        if not self.hover_timer.isActive():
            hz = self.screen().refreshRate() if self.screen() else 60.0  # tick at the display's rate
            self.hover_timer.setInterval(max(4, int(1000 / max(hz, 30.0)) - 1))
            self._hfresh = True
            self.hover_timer.start()

    def hover_step(self):
        """Hover is decided by where the cursor is (with a 10px margin, so the dock growing under the cursor
        can never flicker enter/leave), the decision is low-pass filtered, and the size follows the filtered value
        on a critically damped spring. Hovering in and out a lot therefore just bends one smooth curve."""
        now = time.monotonic()
        if self._hfresh:  # first tick only starts the clock
            self._hfresh, self._hlast = False, now
            return
        dt = min(now - self._hlast, 1 / 30)
        self._hlast = now
        inside = self.isVisible() and self.rect().adjusted(-10, -10, 10, 10).contains(
            self.mapFromGlobal(QCursor.pos()))
        self._htarget = 1.0 if inside else 0.0
        self._hgoal += (self._htarget - self._hgoal) * (1 - math.exp(-dt / 0.06))
        before = self._hover
        steps = int(dt / 0.004) + 1
        h = dt / steps
        w = 22.0  # spring stiffness
        for _ in range(steps):
            self._hvel += (-w * w * (self._hover - self._hgoal) - 2 * w * self._hvel) * h  # damping ratio 1
            self._hover += self._hvel * h
        if (abs(self._hover - self._hgoal) < 0.0005 and abs(self._hvel) < 0.01
                and abs(self._hgoal - self._htarget) < 0.0005):
            self._hover, self._hgoal, self._hvel = self._htarget, self._htarget, 0.0
            if not inside:
                self.hover_timer.stop()  # at rest and the cursor is away: nothing to watch
        if self._hover != before:
            self.update_geometry()
            self.update()

    def enterEvent(self, e):
        self.animate_hover(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.animate_hover(False)
        super().leaveEvent(e)

    def animate_focus(self, focused):
        """Focus no longer changes the size (only hover does); kept so the focus hook still has a target."""

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
        # Grow by a capped number of pixels, not a percentage: 2% of a wide window is more than the dock's
        # side margin and the focused field would be clipped by the window edges.
        room = max(0, self.parentWidget().width() - self._base_rect.width() - 16)  # never wider than the window
        amount = self.HOVER_SCALE * self._hover  # only hovering scales the dock; focusing no longer does
        grow = min(0.04 * self._base_rect.width(), self.MAX_GROW, room) * amount
        width = int(round((self._base_rect.width() + grow) / 2.0)) * 2  # even sizes keep the centre on a whole
        height = int(round(self._base_rect.height() * (1.0 + self.GROW_H * amount) / 2.0)) * 2  # pixel: no 1px jitter
        center = self._base_rect.center()
        self.setGeometry(QRect(center.x() - width // 2, center.y() - height // 2, width, height))
        self.edit.setGeometry(self.rect())
        self.clear_btn.move(self.width() - self.clear_btn.width() - 14,
                            (self.height() - self.clear_btn.height()) // 2)
        self.clear_btn.raise_()

    def refresh(self):
        if WindowState.moving:
            return
        if self.isVisible() and self._focus in (0.0, 1.0) and self._hover in (0.0, 1.0):
            self.capture_backdrop()

    def capture_backdrop(self):
        if not self.isVisible() or self.width() <= 0:
            return
        m = 48
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
        pm = blurred(shot, radius=26)
        k = pm.width() / region.width()
        off = g.topLeft() - region.topLeft()
        self.backdrop = pm
        self.backdrop_k = k
        self.backdrop_src = QRectF(off.x() * k, off.y() * k, g.width() * k, g.height() * k)
        self.update()

    def place(self):
        host = self.parentWidget()
        w = max(min(self.WIDTH, host.width() - 40), 0)
        self._base_rect = QRect((host.width() - w) // 2, host.height() - self.H - self.GAP, w, self.H)
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
        if self.ph_text and self.ph_alpha > 0.01:
            p.setFont(self.edit.font())
            p.setPen(QColor(138, 138, 138, int(255 * self.ph_alpha)))
            if not self.edit.text():  # remember where the empty caret sits; typing moves the caret, not this
                cr = self.edit.cursorRect()
                if cr.height() > 0:
                    self.ph_x = cr.x() + 7
            x = self.ph_x
            # Centre the letters themselves: a line box also holds the descender gap, which makes text
            # centred by its line box look a little low. Put the middle of the cap height on the middle.
            baseline = self.height() / 2 + QFontMetricsF(self.edit.font()).capHeight() / 2
            p.drawText(QPointF(x, baseline), self.ph_text)
        p.setPen(QPen(QColor(255, 255, 255, 32), 1))
        p.drawPath(path)


def blurred(pm, radius=18):
    """Blur a window screenshot (downscaled first, so it stays fast on big monitors)."""
    pm.setDevicePixelRatio(1.0)
    small = pm.scaled(max(pm.width() // 4, 1), max(pm.height() // 4, 1), Qt.IgnoreAspectRatio,
                      Qt.SmoothTransformation)
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(small)
    fx = QGraphicsBlurEffect()
    fx.setBlurRadius(radius / 2)
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

class PopupBackdrop(QWidget):
    """Frosted-glass fill behind a dropdown list: a blurred copy of the window beneath it, tinted dark."""

    def __init__(self, container, combo):
        super().__init__(container)
        self.combo = combo
        self.blur = None
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        container.installEventFilter(self)
        self.setGeometry(container.rect())
        self.lower()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.setGeometry(obj.rect())
            self.lower()
        return False

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        p.setClipPath(path)
        p.fillPath(path, QColor(14, 14, 14))
        if self.blur is not None:
            win = self.combo.window()
            k = self.blur.width() / max(win.width(), 1)
            origin = win.mapFromGlobal(self.mapToGlobal(QPoint(0, 0)))
            p.drawPixmap(QRectF(self.rect()), self.blur,
                         QRectF(origin.x() * k, origin.y() * k, self.width() * k, self.height() * k))
            p.fillPath(path, QColor(14, 14, 14, 115))
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 34), 1))
        p.drawPath(path)


class ComboItemDelegate(QStyledItemDelegate):
    """Dropdown rows drawn by hand: exact row sizes, and the same soft cursor glow as the buttons,
    faded in and out per row. The current choice gets a flat fill that hovering never changes."""
    ROW, GAP = 36, 5
    SELECTED_FILL = 32
    FADE_IN_MS, FADE_OUT_MS = 220, 320

    def __init__(self, combo):
        super().__init__(combo)
        self.list_view = combo.view()
        self.strength = {}
        self.target = None
        self.cursor = QPointF()
        self.last = 0.0
        self.clock = QTimer(self)
        self.clock.setInterval(16)
        self.clock.timeout.connect(self.tick)
        self.list_view.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.MouseMove:
            self.cursor = QPointF(event.position())
            index = self.list_view.indexAt(event.position().toPoint())
            self.set_target(index.row() if index.isValid() else None)
            self.list_view.viewport().update()
        elif kind == QEvent.Leave:
            self.set_target(None)
        elif kind == QEvent.Hide:
            self.clock.stop()
            self.strength.clear()
            self.target = None
        return False

    def set_target(self, row):
        self.target = row
        if not self.clock.isActive():
            self.last = time.monotonic()
            self.clock.start()

    def tick(self):
        now = time.monotonic()
        dt = (now - self.last) * 1000
        self.last = now
        busy = False
        for row in set(self.strength) | ({self.target} - {None}):
            s = self.strength.get(row, 0.0)
            if row == self.target:
                s = min(1.0, s + dt / self.FADE_IN_MS)
                busy |= s < 1.0
            else:
                s = max(0.0, s - dt / self.FADE_OUT_MS)
                busy |= s > 0.0
            if s > 0.0:
                self.strength[row] = s
            else:
                self.strength.pop(row, None)
        self.list_view.viewport().update()
        if not busy:
            self.clock.stop()

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ROW + self.GAP)

    def paint(self, p, option, index):
        p.save()
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        rect = QRectF(option.rect).adjusted(0, self.GAP / 2, 0, -self.GAP / 2)
        path = QPainterPath()
        path.addRoundedRect(rect, 9, 9)
        if option.state & QStyle.State_Selected:
            p.fillPath(path, QColor(255, 255, 255, self.SELECTED_FILL))
        else:
            s = self.strength.get(index.row(), 0.0)
            if s > 0.0:
                eased = (1.0 - math.cos(math.pi * s)) / 2.0
                p.fillPath(path, QColor(255, 255, 255, int(7 * eased)))
                draw_spotlight(p, path, rect, self.cursor, eased, 105, DROPDOWN_GLOW)
        # Start from the application font, not the view's: a style-sheet font drops the app's rendering flags
        # (grayscale antialiasing, no hinting), which is why this text looked different from everything else.
        font = QFont(QApplication.font())
        font.setFamily(option.font.family())
        font.setWeight(option.font.weight())
        font.setPixelSize(15)
        font.setStyleStrategy(QFont.PreferAntialias | QFont.NoSubpixelAntialias)
        font.setHintingPreference(QFont.PreferNoHinting)
        p.setFont(font)
        p.setPen(QColor(TEXT))
        text_rect = rect.adjusted(14, 0, -14, 0)
        text = QFontMetrics(font).elidedText(str(index.data(Qt.DisplayRole) or ""), Qt.ElideRight,
                                             int(text_rect.width()))
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
        p.restore()


class ZoomEffect(QGraphicsEffect):
    """Draws its widget slightly scaled around the top (or bottom) centre, to ease a dropdown in and out."""

    def __init__(self):
        super().__init__()
        self.zoom = 1.0
        self.from_bottom = False

    def set_zoom(self, zoom):
        self.zoom = zoom
        self.update()

    def draw(self, painter):
        offset = QPoint()
        # Device pixels, drawn 1:1: the popup is rendered at the screen's real resolution and only resampled
        # while the zoom is actually animating, so text isn't softened by a scale-by-1.0 pass.
        pm = self.sourcePixmap(Qt.DeviceCoordinates, offset, QGraphicsEffect.NoPad)
        painter.save()
        painter.setWorldTransform(QTransform())
        if self.zoom != 1.0:
            painter.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.Antialiasing)
            anchor = QPointF(offset.x() + pm.width() / 2, offset.y() + (pm.height() if self.from_bottom else 0))
            painter.translate(anchor)
            painter.scale(self.zoom, self.zoom)
            painter.translate(-anchor)
        painter.drawPixmap(offset, pm)
        painter.restore()


class FluentCombo(QComboBox):
    FADE_IN_MS, FADE_OUT_MS = 320, 220
    ZOOM_FROM = 0.965

    def __init__(self):
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(190)
        self._hover = 0.0
        self._cursor_pos = QPointF()
        self._closing = False
        self._progress = 1.0
        self._fx = []
        self.zoom_from_bottom = False
        self.a_hover = QPropertyAnimation(self, b"hoverMix", self)
        self.a_cursor = QPropertyAnimation(self, b"cursorPosition", self)
        self._caret = 0.0  # 0 = arrow down (closed), 1 = flipped 180 degrees (open)
        self.a_caret = QVariantAnimation(self)
        self.a_caret.setEasingCurve(QEasingCurve.InOutCubic)
        self.a_caret.valueChanged.connect(self._set_caret)
        self._text_alpha = 1.0  # the shown choice fades in when the user picks a new one
        self.a_text = QVariantAnimation(self)
        self.a_text.setEasingCurve(QEasingCurve.OutCubic)
        self.a_text.valueChanged.connect(self._set_text_alpha)
        self.activated.connect(lambda _i: self.fade_in_text())
        self.setMouseTracking(True)
        self.installEventFilter(self)

        QApplication.setEffectEnabled(Qt.UI_AnimateCombo, False)  # Qt's own top-down reveal
        self.setItemDelegate(ComboItemDelegate(self))
        popup = self.view().window()
        popup.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground)
        popup.setStyleSheet("QComboBoxPrivateContainer { background: transparent; border: none; }")
        self.view().viewport().setAutoFillBackground(False)
        self._backdrop = PopupBackdrop(popup, self)
        self._fade = QPropertyAnimation(self, b"popupProgress", self)
        self._fade.finished.connect(self._fade_finished)

    def get_hover_mix(self):
        return self._hover

    def set_hover_mix(self, v):
        self._hover = v
        self.update()

    hoverMix = Property(float, get_hover_mix, set_hover_mix)

    def get_cursor_position(self):
        return self._cursor_pos

    def set_cursor_position(self, pos):
        self._cursor_pos = pos
        self.update()

    cursorPosition = Property(QPointF, get_cursor_position, set_cursor_position)

    def _set_text_alpha(self, v):
        self._text_alpha = float(v)
        self.update()

    def fade_in_text(self):
        self.a_text.stop()
        self.a_text.setStartValue(0.0)
        self.a_text.setEndValue(1.0)
        self.a_text.setDuration(260)
        self.a_text.start()

    def _set_caret(self, v):
        self._caret = float(v)
        self.update()

    def animate_caret(self, open_):
        self.a_caret.stop()
        self.a_caret.setStartValue(self._caret)
        self.a_caret.setEndValue(1.0 if open_ else 0.0)
        self.a_caret.setDuration(300 if open_ else 260)
        self.a_caret.start()

    def glide(self, anim, end, ms, curve):
        anim.stop()
        anim.setDuration(ms)
        anim.setEasingCurve(curve)
        anim.setStartValue(anim.targetObject().property(bytes(anim.propertyName()).decode()))
        anim.setEndValue(end)
        anim.start()

    def eventFilter(self, obj, event):
        if obj is self:
            kind = event.type()
            if kind == QEvent.Enter:
                self.a_cursor.stop()
                self.set_cursor_position(QPointF(event.position()))
                self.glide(self.a_hover, 1.0, 280, QEasingCurve.InOutSine)
            elif kind == QEvent.Leave:
                self.glide(self.a_hover, 0.0, 360, QEasingCurve.InOutSine)
            elif kind == QEvent.MouseMove:
                self.glide(self.a_cursor, QPointF(event.position()), 130, QEasingCurve.OutCubic)
        return super().eventFilter(obj, event)

    def showPopup(self):
        popup = self.view().window()
        self._closing = False
        self._backdrop.blur = blurred(self.window().grab(), radius=16)
        self._fade.stop()
        self.set_zoom_effects(True)
        self.set_progress(0.0)
        super().showPopup()
        self.zoom_from_bottom = popup.y() < self.mapToGlobal(QPoint(0, 0)).y()  # opened upwards
        popup.move(popup.x(), popup.y() + (-5 if self.zoom_from_bottom else 5))  # 5px more gap from the box
        self.animate_caret(True)
        for fx in self._fx:
            fx.from_bottom = self.zoom_from_bottom
        self.set_progress(0.0)
        self.fade_popup(1.0, self.FADE_IN_MS)

    def hidePopup(self):
        popup = self.view().window()
        if self._closing or not popup.isVisible():
            self._closing = False
            self._fade.stop()
            super().hidePopup()
            self.animate_caret(False)
            return
        self._closing = True
        self.animate_caret(False)
        if not self._fx:
            self.set_zoom_effects(True)
            for fx in self._fx:
                fx.from_bottom = self.zoom_from_bottom
        self.fade_popup(0.0, self.FADE_OUT_MS)

    def fade_popup(self, target, ms):
        self._fade.stop()
        self._fade.setDuration(ms)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.setStartValue(self._progress)
        self._fade.setEndValue(target)
        self._fade.start()

    def _fade_finished(self):
        if self._closing:
            self._closing = False
            super().hidePopup()
            self.set_zoom_effects(False)
        elif self._progress >= 1.0:
            self.set_zoom_effects(False)  # fully open: draw the list directly, crisp like all other text

    def set_zoom_effects(self, on):
        for widget in (self.view(), self._backdrop):
            widget.setGraphicsEffect(None)
        self._fx = []
        if on:
            for widget in (self.view(), self._backdrop):
                fx = ZoomEffect()
                widget.setGraphicsEffect(fx)
                self._fx.append(fx)

    def get_progress(self):
        return self._progress

    def set_progress(self, v):
        """0 = hidden and slightly shrunk, 1 = fully shown; drives the fade and the zoom together."""
        self._progress = v
        self.view().window().setWindowOpacity(v)
        zoom = self.ZOOM_FROM + (1.0 - self.ZOOM_FROM) * v
        for fx in self._fx:
            if isValid(fx):
                fx.set_zoom(zoom)

    popupProgress = Property(float, get_progress, set_progress)

    def paintEvent(self, e):
        # same material as the buttons: flat dark fill, constant 1px border, soft glow under the cursor
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 11, 11)
        p.fillPath(path, QColor(11, 11, 11))
        inner = QPainterPath()
        inner.addRoundedRect(r.adjusted(1, 1, -1, -1), 10, 10)
        draw_spotlight(p, inner, self.rect(), self._cursor_pos, self._hover, 70, DROPDOWN_GLOW)
        edge = QPainterPath()
        edge.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 11, 11)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.drawPath(edge)
        p.end()
        h = self._hover
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        # the chosen option, drawn here (not by Qt) so it can fade in; same font and rendering as the rest of the app
        tf = QFont(QApplication.font())
        tf.setFamily(self.font().family())
        tf.setWeight(self.font().weight())
        tf.setPixelSize(15)
        tf.setStyleStrategy(QFont.PreferAntialias | QFont.NoSubpixelAntialias)
        tf.setHintingPreference(QFont.PreferNoHinting)
        p.setFont(tf)
        p.setPen(QColor(TEXT))
        p.setOpacity(self._text_alpha)
        shown = QFontMetrics(tf).elidedText(self.currentText(), Qt.ElideRight, max(0, self.width() - 18 - 34))
        p.drawText(QRectF(18, 0, max(0, self.width() - 18 - 34), self.height()), Qt.AlignVCenter | Qt.AlignLeft, shown)
        p.setOpacity(1.0)
        f = QFont("Segoe Fluent Icons")
        f.setPixelSize(12)
        p.setFont(f)
        arrow_alpha = int(122 + (200 - 122) * h)
        p.setPen(QColor(arrow_alpha, arrow_alpha, arrow_alpha))
        box = self.rect().adjusted(0, 0, -14, 0)
        glyph_w = QFontMetricsF(f).horizontalAdvance("\uE70D")
        centre = QPointF(box.right() + 1 - glyph_w / 2, box.center().y() + 0.5)
        p.translate(centre)
        p.rotate(180.0 * self._caret)  # flips over around its own centre as the list opens and closes
        p.translate(-centre)
        p.drawText(box, Qt.AlignRight | Qt.AlignVCenter, "\uE70D")


class NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


def native_frame(hwnd):
    """Give the frameless window back the real Windows frame behaviour: open/close/minimize animations,
    shadow, rounded corners and outline. The visible caption is removed again via WM_NCCALCSIZE."""
    user, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
    user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    style = user.GetWindowLongPtrW(hwnd, -16)  # GWL_STYLE
    style |= 0x00C00000 | 0x00040000 | 0x00020000 | 0x00010000  # CAPTION THICKFRAME MINIMIZEBOX MAXIMIZEBOX
    user.SetWindowLongPtrW(hwnd, -16, style)
    user.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
    dwm.DwmExtendFrameIntoClientArea(ctypes.c_void_p(hwnd), ctypes.byref(margins))
    # rounded corners, outline colour (the app's own border grey), and everything Windows draws behind the
    # client area (caption fill, Mica backdrop) set to the app background, so the maximize animation's
    # ghost frame is black like the app instead of system grey
    for attr, value in ((33, 2), (34, 0x1A1A1A), (35, 0x000000), (38, 1)):
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attr, ctypes.byref(ctypes.c_int(value)), 4)


class CaptionButton(QAbstractButton):
    """Minimize / maximize / close button of the custom title bar, drawn by hand."""

    def __init__(self, kind, parent):
        super().__init__(parent)
        self.kind = kind
        self.hv = 0.0
        self.style_name = "windows"
        self.setFixedSize(46, TITLEBAR_H)
        self.setFocusPolicy(Qt.NoFocus)
        self.fade = QVariantAnimation(self)
        self.fade.setDuration(160)
        self.fade.setEasingCurve(QEasingCurve.OutCubic)
        self.fade.valueChanged.connect(self._set_hover)

    def set_style(self, name):
        self.style_name = name
        self.setFixedSize(34 if name == "mac" else 46, TITLEBAR_H)
        self.update()

    def _set_hover(self, v):
        self.hv = float(v)
        self.update()

    def _go(self, end):
        self.fade.stop()
        self.fade.setStartValue(self.hv)
        self.fade.setEndValue(end)
        self.fade.start()

    def _group(self):
        return [self] + (list(getattr(self, "peers", [])) if self.style_name == "mac" else [])

    def enterEvent(self, e):
        for b in self._group():
            b._go(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        for b in self._group():
            b._go(0.0)
        super().leaveEvent(e)

    def paint_mac(self):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        base, ink = {"min": ((254, 188, 46), (110, 66, 0)), "max": ((40, 200, 64), (0, 80, 8)),
                     "restore": ((40, 200, 64), (0, 80, 8)), "close": ((255, 95, 87), (95, 0, 0))}[self.kind]
        if self.isDown():
            base = tuple(int(c * 0.78) for c in base)
        c = QPointF(self.width() / 2.0, self.height() / 2.0)
        r = 8.0
        p.setPen(QPen(QColor(0, 0, 0, 55), 0.8))  # thin darker rim, like the real thing
        p.setBrush(QColor(*base))
        p.drawEllipse(c, r - 0.4, r - 0.4)
        if self.hv > 0.01:  # glyph fades in on hover (all three together, like macOS)
            pen = QPen(QColor(ink[0], ink[1], ink[2], int(255 * self.hv)), 1.7)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            if self.kind == "close":
                d = 3.1
                p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
                p.drawLine(QPointF(c.x() + d, c.y() - d), QPointF(c.x() - d, c.y() + d))
            elif self.kind == "min":
                p.drawLine(QPointF(c.x() - 3.7, c.y()), QPointF(c.x() + 3.7, c.y()))
            else:
                p.drawLine(QPointF(c.x() - 3.7, c.y()), QPointF(c.x() + 3.7, c.y()))
                p.drawLine(QPointF(c.x(), c.y() - 3.7), QPointF(c.x(), c.y() + 3.7))

    def paintEvent(self, _):
        if self.style_name == "mac":
            return self.paint_mac()
        p = QPainter(self)
        close = self.kind == "close"
        fill = QColor(196, 43, 28, int(255 * self.hv)) if close else QColor(255, 255, 255, int(24 * self.hv))
        p.fillRect(self.rect(), fill)
        col = QColor(255, 255, 255) if close and self.hv > 0.5 else QColor(225, 225, 225)
        p.setPen(QPen(col, 1))
        cx, cy = self.width() // 2, self.height() // 2
        if self.kind == "min":
            p.drawLine(cx - 5, cy, cx + 5, cy)
        elif self.kind == "max":
            p.drawRect(cx - 5, cy - 5, 9, 9)
        elif self.kind == "restore":
            p.drawRect(cx - 5, cy - 3, 8, 8)
            p.drawLine(cx - 3, cy - 5, cx + 5, cy - 5)
            p.drawLine(cx + 5, cy - 5, cx + 5, cy + 3)
        else:
            p.setRenderHint(QPainter.Antialiasing)  # centred on the pixel grid, same size as the max box
            pen = QPen(col, 1.15)
            pen.setCapStyle(Qt.FlatCap)
            p.setPen(pen)
            c, d = QPointF(cx, cy), 4.5
            p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
            p.drawLine(QPointF(c.x() + d, c.y() - d), QPointF(c.x() - d, c.y() + d))



# ========== TITLE BAR / NAVBAR (Lines 1828-2051) ==========
class TitleBar(QWidget):
    """Custom title bar. Transparent at rest; once the page under it is scrolled it shows a blurred,
    darkened copy of the content passing beneath it (like a website navbar)."""
    BLUR_SCALE, BLUR_RADIUS = 3.0, 18.0  # the blur runs at 1/3 size with a gaussian of this radius (bigger = stronger)

    # ===== INITIALIZATION: Set up title bar layout and icon =====
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.blur = None  # (blurred strip, x offset, width, dpr)
        self.cache = None  # blurred levels of the page content around the scroll position
        self.amount = 0.0
        self.snap_next = False
        self.setFixedHeight(TITLEBAR_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.title = QLabel("Kernel")
        self.title.setObjectName("h1")
        self.title.setStyleSheet("font-size: 19px; background: transparent;")
        lay.addSpacing(14)
        icon_widget = QLabel()
        icon_size = 18
        icon_widget.setFixedSize(icon_size, icon_size)
        icon_path = os.path.join(os.path.dirname(__file__), "icon.ico")
        if os.path.exists(icon_path):
            dpr = self.win.devicePixelRatioF()
            px = round(icon_size * dpr)
            pm = QIcon(icon_path).pixmap(QSize(px, px))
            if not pm.isNull():
                pm.setDevicePixelRatio(dpr)
                icon_widget.setPixmap(pm)
        icon_widget.setStyleSheet("background: transparent;")
        lay.addWidget(icon_widget, 0, Qt.AlignVCenter)
        lay.addSpacing(8)
        lay.addWidget(self.title)
        lay.addStretch()
        self.soft = None
        self.b_min = CaptionButton("min", self)
        self.b_max = CaptionButton("max", self)
        self.b_close = CaptionButton("close", self)
        for b in (self.b_min, self.b_max, self.b_close):
            lay.addWidget(b)
        for b in (self.b_min, self.b_max, self.b_close):
            b.peers = [o for o in (self.b_min, self.b_max, self.b_close) if o is not b]
        self.cap_pad = QWidget(self)
        self.cap_pad.setFixedSize(0, TITLEBAR_H)
        lay.addWidget(self.cap_pad)
        self.set_caption_style(getattr(win.cfg, "caption_style", "windows"))
        self.b_min.clicked.connect(win.showMinimized)
        self.b_max.clicked.connect(self.toggle_max)
        self.b_close.clicked.connect(win.close)
        self.target = 0.0
        self.fade = QVariantAnimation(self)
        self.fade.setEasingCurve(QEasingCurve.OutCubic)
        self.fade.valueChanged.connect(self._fade_step)
        self.fade.finished.connect(self._fade_done)
        self.dirty = QTimer(self)
        self.dirty.setSingleShot(True)
        self.dirty.setInterval(24)
        self.dirty.timeout.connect(self.refresh)

    def schedule(self, instant=False, quiet=False):
        self.cache = None  # content, size or page changed: re-blur from scratch
        if instant:
            self.snap_next = True
            self.refresh()
            return
        if quiet:  # window resizing / maximizing: wait until it settles so the UI thread stays free
            self.dirty.start(110)
        elif not self.dirty.isActive():
            self.dirty.start(24)

    def set_caption_style(self, name):
        for b in (self.b_min, self.b_max, self.b_close):
            b.set_style(name)
        self.cap_pad.setFixedWidth(3 if name == "mac" else 0)
        lay = self.layout()
        order = (self.b_max, self.b_min, self.b_close) if name == "mac" else (self.b_min, self.b_max, self.b_close)
        for b in order:
            lay.removeWidget(b)
        idx = lay.indexOf(self.cap_pad)  # buttons always go right before the pad (index of a removed button drifted)
        for i, b in enumerate(order):
            lay.insertWidget(idx + i, b)
        self.update()

    def toggle_max(self):
        self.win.showNormal() if self.win.isMaximized() else self.win.showMaximized()

    def sync(self):
        self.b_max.kind = "restore" if self.win.isMaximized() else "max"
        self.b_max.update()

    def repaint_all(self):
        self.update()
        if self.soft is not None:
            self.soft.update()

    def _fade_step(self, v):
        self.amount = float(v)
        self.repaint_all()

    def _fade_done(self):
        if self.amount <= 0.0:
            self.blur = None

    def _fade_to(self, end):
        if end == self.target:
            return
        self.target = end
        self.fade.stop()
        self.fade.setStartValue(self.amount)
        self.fade.setEndValue(end)
        self.fade.setDuration(80)  # near-instant: new content under the bar is blurred right away
        self.fade.start()

    @staticmethod
    def _blur_levels(img, scale, radius):
        """[img, blurred img]. The blur is a true gaussian run on a copy shrunk by `scale` (cheap), padded with
        replicated edges so the borders stay opaque, then scaled back up in two smooth steps (no blocky pixels)."""
        w, h = img.width(), img.height()
        sw, sh = max(int(w / scale), 1), max(int(h / scale), 1)
        small = img.scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        pad = int(radius * 2.5) + 2
        big = QImage(sw + 2 * pad, sh + 2 * pad, QImage.Format_ARGB32_Premultiplied)
        p = QPainter(big)
        p.drawImage(QRect(0, 0, big.width(), big.height()), small, QRect(0, 0, sw, sh))  # background, replaced below
        p.drawImage(pad, pad, small)
        p.drawImage(QRect(pad, 0, sw, pad), small, QRect(0, 0, sw, 1))
        p.drawImage(QRect(pad, pad + sh, sw, pad), small, QRect(0, sh - 1, sw, 1))
        p.drawImage(QRect(0, 0, pad, big.height()), big, QRect(pad, 0, 1, big.height()))
        p.drawImage(QRect(pad + sw, 0, pad, big.height()), big, QRect(pad + sw - 1, 0, 1, big.height()))
        p.end()
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(QPixmap.fromImage(big))
        fx = QGraphicsBlurEffect()
        fx.setBlurRadius(radius)
        fx.setBlurHints(QGraphicsBlurEffect.QualityHint)
        item.setGraphicsEffect(fx)
        scene.addItem(item)
        out = QImage(big.size(), QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        q = QPainter(out)
        scene.render(q, QRectF(out.rect()), QRectF(big.rect()))
        q.end()
        soft = out.copy(pad, pad, sw, sh)
        mid = soft.scaled(max(int(w / math.sqrt(scale)), 1), max(int(h / math.sqrt(scale)), 1),
                          Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return [img, mid.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)]

    def build_cache(self, page, value):
        """Blur a window of the page content around the scroll position once; scrolling inside that window
        then only blends rows (no re-grab, no re-blur), so the bar stays in step with the page."""
        content = page.widget()
        dpr = self.win.devicePixelRatioF()
        zone = TITLEBAR_H + TITLEBAR_SOFT
        top = max(0, value - CACHE_MARGIN)
        height = min(content.height() - top, zone + 2 * CACHE_MARGIN + (value - top))
        if height <= 0 or content.width() <= 0:
            return None
        w, h = int(round(content.width() * dpr)), int(round(height * dpr))
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor(BG))  # the content itself is transparent; the page background is solid
        img.setDevicePixelRatio(dpr)
        p = QPainter(img)
        content.render(p, QPoint(0, 0), QRegion(0, top, content.width(), height))
        p.end()
        img.setDevicePixelRatio(1.0)
        levels = self._blur_levels(img, self.BLUR_SCALE * dpr, self.BLUR_RADIUS)
        return {"page": page, "top": top, "height": height, "dpr": dpr, "levels": levels,
                "x": page.viewport().mapTo(self.win, QPoint(0, 0)).x(), "w": content.width(),
                "rows": self.row_weights(dpr)}

    @staticmethod
    def row_weights(dpr):
        """Per row of the bar and its fade zone: (how much of the row is the blurred copy, dark tint)."""
        zone = int(round((TITLEBAR_H + TITLEBAR_SOFT) * dpr))
        hold_end = TITLEBAR_H + SOFT_HOLD
        rows = []
        for py in range(zone):
            t = min(1.0, max(0.0, (py / dpr - hold_end) / (TITLEBAR_SOFT - SOFT_HOLD)))
            s = 1.0 - t * t * (3.0 - 2.0 * t)  # smoothstep: 1 under the bar, 0 at the end of the fade
            rows.append((s ** 0.5, int(TINT * s)))
        return rows

    # ===== COMPOSE_BLUR: Render blurred content with fade gradient =====
    def compose_blur(self, c, value):
        """Each row is an opaque mix of the sharp and the blurred content (all blur under the bar, easing back
        to sharp), so it replaces what is underneath instead of glowing on top of it."""
        dpr, (sharp, soft) = c["dpr"], c["levels"]
        w = sharp.width()
        off = int(round((value - c["top"]) * dpr))
        out = QImage(w, len(c["rows"]), QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        p = QPainter(out)
        rows, last = c["rows"], sharp.height()
        for py in range(0, len(rows), BAND):  # blend in thin bands, not single rows: half the draw calls
            mix, tint = rows[py]
            bh = min(BAND, len(rows) - py, last - (off + py))
            if bh <= 0:
                continue
            sy = off + py
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            p.setOpacity(1.0 - mix)
            p.drawImage(0, py, sharp, 0, sy, w, bh)
            p.setOpacity(mix)
            p.drawImage(0, py, soft, 0, sy, w, bh)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setOpacity(1.0)
            p.fillRect(QRect(0, py, w, bh), QColor(0, 0, 0, tint))
        fade_h = min(20, len(c["rows"]) // 4)
        if fade_h > 0:
            fade = QLinearGradient(0, len(c["rows"]) - fade_h, 0, len(c["rows"]))
            fade.setColorAt(0.0, QColor(255, 255, 255, 255))
            fade.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            p.fillRect(QRect(0, len(c["rows"]) - fade_h, w, fade_h), fade)
        p.end()
        return out, dpr

    def viewport_x(self, page):
        return page.viewport().mapTo(self.win, QPoint(0, 0)).x()

    def blur_matches(self):
        """True if the blurred strip was made for the page's current width and position. A strip made for an
        older layout would show the old rows (ghost buttons) shifted sideways, so it must never be drawn."""
        page = self.win.current_scroll_area()
        if page is None or not self.blur:
            return False
        _, x, w, _ = self.blur
        return w == page.widget().width() and x == self.viewport_x(page)

    # ===== REFRESH: Calculate blur amount and update cache =====
    def refresh(self):
        page = self.win.current_scroll_area()
        value = page.verticalScrollBar().value() if page is not None else 0
        # Blur starts at 40% opacity at top, builds to 100% over 140px scroll
        target = 0.0 if page is None else min(1.0, 0.4 + value / 140.0)  # min 0.4 blur at top, builds up over scrolling
        if target > 0.0:
            c = self.cache
            zone = TITLEBAR_H + TITLEBAR_SOFT
            if (c is None or c["page"] is not page or c["dpr"] != self.win.devicePixelRatioF()
                    or c["w"] != page.widget().width() or c["x"] != self.viewport_x(page)
                    or value < c["top"] or (value + zone > c["top"] + c["height"]
                                            and c["top"] + c["height"] < page.widget().height())):
                c = self.cache = self.build_cache(page, value)
            if c is not None:
                img, dpr = self.compose_blur(c, value)
                self.blur = (img, c["x"], c["w"], dpr)
                self.repaint_all()
        if self.snap_next:
            self.snap_next = False
            self.fade.stop()
            self.target = self.amount = target
            self.repaint_all()
        else:
            self._fade_to(target)

    # ===== PAINT_EVENT: Draw the blurred title bar =====
    def paintEvent(self, _):
        p = QPainter(self)
        # the sidebar divider starts below the bar instead of being cut off by it
        p.fillRect(QRectF(0, 0, self.win.sidebar.width() + 6, TITLEBAR_H), QColor(0, 0, 0))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.window().windowHandle():
            WindowState.dragging = True
            self.window().windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.toggle_max()



# ========== SOFT EDGE FADE ZONE (Lines 2054+) ==========
class SoftEdge(QWidget):
    """Blur that keeps going below the title bar and fades out gradually, so the bar has no hard edge."""

    def __init__(self, bar):
        super().__init__(bar.win)
        self.bar = bar
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _):
        b = self.bar
        p = QPainter(self)
        side = QLinearGradient(0, TITLEBAR_H, 0, TITLEBAR_H + SIDE_FADE)  # divider fades in below the bar
        side.setColorAt(0.0, QColor(0, 0, 0, 255))
        side.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(0, TITLEBAR_H, b.win.sidebar.width() + 6, SIDE_FADE), side)
        if not b.blur or b.amount <= 0.0 or self.height() <= 0:
            return
        img, bx, bw, dpr = b.blur
        page = b.win.current_scroll_area()
        if page is None:
            return
        # Always draw: fit the strip to the page's CURRENT position and width, so it never shows rows at an old
        # offset (ghost buttons) and never disappears while the window is dragged or resized. If the layout
        # moved since it was made, ask for a fresh one (it replaces this a moment later).
        x, w = b.viewport_x(page), page.widget().width()
        if (x, w) != (bx, bw):
            b.schedule()
        p.setOpacity(b.amount)
        p.drawImage(QRectF(x, 0, w, self.height()), img, QRectF(0, 0, img.width(), img.height()))



class EdgeGrip(QWidget):
    """Invisible strip on a window edge or corner that starts a native resize."""

    def __init__(self, win, edges, cursor):
        super().__init__(win)
        self.edges = edges
        self.setCursor(cursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemResize(self.edges)


class Sidebar(QFrame):
    """Sidebar whose selection highlight follows the active item on a spring: both edges chase the target,
    the leading edge stiffer than the trailing one (so it stretches), and a new click mid-flight keeps
    its momentum. The divider fades out at both ends."""
    FADE = SIDE_FADE

    def __init__(self):
        super().__init__()
        self.current = None
        self.accent = QColor(255, 255, 255)
        self.edges = None  # (top, bottom) of the highlight right now
        self.goal = None
        self.vel = [0.0, 0.0]
        self.omega = (30.0, 30.0)  # spring stiffness of the (top, bottom) edge
        self.lead_k = 1
        self.last = 0.0
        self.fresh = False
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(8)
        self.timer.timeout.connect(self.tick)

    def set_accent(self, color):
        self.accent = QColor(color)
        self.update()

    def _set_edges(self, edges):
        old, self.edges = self.edges, edges
        if self.current is None or old is None:
            self.update()
            return
        top = int(min(old[0], edges[0])) - 2  # repaint only the strip the highlight crossed
        bottom = int(max(old[1], edges[1])) + 3
        self.update(QRect(0, top, self.width(), bottom - top))

    def select(self, btn):
        """Make btn the highlighted item; the highlight springs over if the sidebar is showing."""
        self.current = btn
        goal = (float(btn.y()), float(btn.y() + btn.height()))
        if self.edges is None or not self.isVisible():
            self.edges = self.goal = goal
            self.vel = [0.0, 0.0]
            self.timer.stop()
            self.update()
            return
        if goal[0] != self.goal[0]:
            down = goal[0] > self.edges[0]
            self.lead_k = 1 if down else 0  # moving down: the bottom edge leads
            self.omega = (31.0, 52.0) if down else (52.0, 31.0)  # the leading edge is stiffer: it stretches, then settles
        self.goal = goal
        if not self.timer.isActive():
            hz = self.screen().refreshRate() if self.screen() else 60.0  # tick at the display's rate
            self.timer.setInterval(max(4, int(1000 / max(hz, 30.0)) - 1))
            self.fresh = False
            self.last = time.monotonic() - 1 / 60
            self.timer.start()
        # Move and paint the first frame right now, inside the click, so the pill reacts instantly instead of
        # waiting for the page switch (which blocks the UI for a moment) to finish.
        self.tick()
        self.repaint()

    def tick(self):
        now = time.monotonic()
        if self.fresh:
            self.fresh = False
            self.last = now
            return
        dt = min(now - self.last, 1 / 30)
        self.last = now
        steps = int(dt / 0.004) + 1
        h = dt / steps
        pos = list(self.edges)
        lead, trail = self.lead_k, 1 - self.lead_k
        base = float(self.current.height())
        wl, wt = 48.0, 38.0  # both critically damped (no wobble); the trailing edge is the softer one
        for _ in range(steps):
            # the leading edge glides to the new item...
            self.vel[lead] += (-wl * wl * (pos[lead] - self.goal[lead]) - 2 * wl * self.vel[lead]) * h
            pos[lead] += self.vel[lead] * h
            # ...and the trailing edge chases the leading edge (one item behind it), not the destination, so the
            # stretch grows and shrinks as one continuous motion instead of two separate edges moving
            follow = pos[lead] - base if trail == 0 else pos[lead] + base
            self.vel[trail] += (-wt * wt * (pos[trail] - follow) - 2 * wt * self.vel[trail]) * h
            pos[trail] += self.vel[trail] * h
        if all(abs(pos[k] - self.goal[k]) < 0.05 and abs(self.vel[k]) < 1.0 for k in (0, 1)):
            pos = list(self.goal)
            self.vel = [0.0, 0.0]
            self.timer.stop()
        self._set_edges((pos[0], pos[1]))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.current is not None:  # layout moved the buttons: snap, don't animate
            self.timer.stop()
            self.vel = [0.0, 0.0]
            self.edges = self.goal = (float(self.current.y()), float(self.current.y() + self.current.height()))
            self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.current is not None and self.edges is not None:
            top, bottom = self.edges
            h = max(1.0, bottom - top)
            stretch = max(0.0, h - self.current.height())
            inset = min(3.5, stretch * 0.04)  # jelly squash: the longer it stretches, the slimmer it gets
            r = QRectF(self.current.x() + inset, top, self.current.width() - 2 * inset, h)
            rc = 10 + min(5.0, stretch * 0.04)  # corners round off a little as it stretches
            body = QPainterPath()
            body.addRoundedRect(r, rc, rc)
            p.fillPath(body, QColor(10, 10, 10))  # solid, dark
            line = QLinearGradient(0, r.top(), 0, r.bottom())  # hairline border, brighter on top
            line.setColorAt(0.0, QColor(255, 255, 255, 26))
            line.setColorAt(1.0, QColor(255, 255, 255, 8))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QBrush(line), 1))
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rc, rc)
            ph = min(18.0 + stretch * 0.35, r.height() - 8)
            pill = QPainterPath()  # accent pill on the left edge
            pill.addRoundedRect(QRectF(r.left() + 4, r.center().y() - ph / 2, 3, ph), 1.5, 1.5)
            pill_color = QColor(self.accent)
            pill_color.setAlpha(235)
            p.fillPath(pill, pill_color)
        top = max(0, self.height() - self.FADE)
        g = QLinearGradient(0, top, 0, self.height())
        g.setColorAt(0.0, QColor(0, 0, 0, 0))
        g.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.fillRect(QRectF(self.width() - 2, top, 2, self.height() - top), g)


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
    update_checked = Signal(int, str, str, str)

    def __init__(self):
        super().__init__()
        self.cfg = Config()
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
        self.setWindowTitle("Kernel")
        avail = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1180, avail.width() - 80), min(820, avail.height() - 80))
        if self.cfg.win_size:
            self.resize(min(max(self.cfg.win_size[0], 640), avail.width()), min(max(self.cfg.win_size[1], 480), avail.height()))
        self.setMinimumSize(640, 480)
        if CUSTOM_TITLEBAR:
            self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint | Qt.WindowMinMaxButtonsHint
                                | Qt.WindowSystemMenuHint)
            try:
                native_frame(int(self.winId()))
            except Exception:
                pass

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        side = Sidebar()
        self.sidebar = side
        side.setObjectName("sidebar")
        side.setFixedWidth(self.fontMetrics().height() * 12)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 24, 14, 14)
        sl.setSpacing(4)
        logo = QLabel("Kernel")
        logo.setObjectName("h1")
        if CUSTOM_TITLEBAR:  # the name lives in the title bar; keep the same room above the nav
            sl.addSpacing(logo.sizeHint().height())
        else:
            sl.addWidget(logo)
        sl.addSpacing(20)
        outer.addWidget(side)

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
        self.sidebar.select(self.navs[0][1])

        self.build_overlay()

        self.logged.connect(self.log.appendPlainText)
        self.jobs.logged.connect(self.log.appendPlainText)
        self.jobs.added.connect(self.add_job_row)

        self.applied_states = {}
        for r, key, _, fn in self.rows:
            active = is_active(fn)
            self.applied_states[key] = active
            r.set_active(active)
        for job in self.jobs.load():
            self.add_job_row(job)
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
        clear = Button("Clear completed")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self.clear_finished)
        head.addWidget(clear)
        pl.addLayout(head)
        self.dl_hint = FlatLabel("No downloads yet. Anything you download or install shows up here.", 15)
        self.dl_hint.setContentsMargins(0, 4, 0, 0)  # a hair lower
        pl.addWidget(self.dl_hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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
        self.dl_bar = IOSScrollBar(scroll, top=4, bottom=4, pill=(4, 6), strip=12)  # same pill as the pages, smaller
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
        if CUSTOM_TITLEBAR:
            self.titlebar = TitleBar(self)
            self.titlebar.soft = self.softedge = SoftEdge(self.titlebar)
            for i in range(self.stack.count()):
                w = self.stack.widget(i)
                for sa in ([w] if isinstance(w, QScrollArea) else w.findChildren(QScrollArea)):
                    sa.verticalScrollBar().valueChanged.connect(self.titlebar.refresh)
            self.grips = [
                EdgeGrip(self, Qt.LeftEdge, Qt.SizeHorCursor), EdgeGrip(self, Qt.RightEdge, Qt.SizeHorCursor),
                EdgeGrip(self, Qt.TopEdge, Qt.SizeVerCursor), EdgeGrip(self, Qt.BottomEdge, Qt.SizeVerCursor),
                EdgeGrip(self, Qt.LeftEdge | Qt.TopEdge, Qt.SizeFDiagCursor),
                EdgeGrip(self, Qt.RightEdge | Qt.TopEdge, Qt.SizeBDiagCursor),
                EdgeGrip(self, Qt.LeftEdge | Qt.BottomEdge, Qt.SizeBDiagCursor),
                EdgeGrip(self, Qt.RightEdge | Qt.BottomEdge, Qt.SizeFDiagCursor)]
        self.place_overlay()

    def current_scroll_area(self):
        cur = self.stack.currentWidget()
        return cur if isinstance(cur, QScrollArea) else cur.findChild(QScrollArea)

    def raise_chrome(self):
        if hasattr(self, "titlebar"):
            self.softedge.raise_()
            self.titlebar.raise_()
            for g in self.grips:
                g.raise_()
        self.pill.raise_()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.WindowStateChange and hasattr(self, "titlebar"):
            self.titlebar.sync()
            self.place_overlay()

    def refresh_pill_backdrop(self):
        if (not self.pill.isVisible() or not self.isActiveWindow() or self.overlay.isVisible()
                or self.fade or self.anim or self.pill.isDown() or WindowState.moving):
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
        ph = min(600, h - 137)
        shift = TITLEBAR_H - 16 if hasattr(self, "titlebar") else 0
        self.overlay.panel.setGeometry(w - 36 - pw, 103 + shift, pw, ph)
        self.pill.adjustSize()
        self.pill.move(w - 36 - self.pill.width(), 47 + shift)
        if hasattr(self, "titlebar"):
            self.titlebar.setGeometry(0, 0, w, TITLEBAR_H)
            self.softedge.setGeometry(0, 0, w, TITLEBAR_H + TITLEBAR_SOFT)
            t, c = 4, 8
            full = not (self.isMaximized() or self.isFullScreen())
            geo = [(0, c, t, h - 2 * c), (w - t, c, t, h - 2 * c), (c, 0, w - 2 * c - CAPTION_W, t),
                   (c, h - t, w - 2 * c, t), (0, 0, c, c), (w - c, 0, c, c), (0, h - c, c, c), (w - c, h - c, c, c)]
            for g, r in zip(self.grips, geo):
                g.setGeometry(*r)
                g.setVisible(full)
            self.titlebar.schedule()  # real time: the blur under the bar follows every resize step
        self.raise_chrome()

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
        self.raise_chrome()

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
            if i == index:
                self.sidebar.select(b)
        if index == self.stack.currentIndex():
            return
        self.finish_anim()
        self.pill.backdrop = None  # old page's blur would flash under the fade
        self.pill.update()
        self.stack.setCurrentIndex(index)
        if hasattr(self, "titlebar"):
            self.titlebar.schedule(instant=True)
        if index == GAMING:
            self.refresh_display_status()
        if index == SPECS:
            self.start_monitor()
        if index == STORAGE and not self.storage_scanned:
            self.refresh_drives(False)  # first visit only (uses saved results if fresh); Rescan forces it
        page = self.stack.currentWidget()
        fx = QGraphicsOpacityEffect(page)
        fx.setOpacity(0.0)
        page.setGraphicsEffect(fx)
        fade = QPropertyAnimation(fx, b"opacity")
        fade.setDuration(150)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        group = QParallelAnimationGroup(self)
        group.addAnimation(fade)
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
        QTimer.singleShot(0, self.refresh_pill_backdrop)

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
        page = self.build_tweaks("Performance", ("Performance", "NVIDIA"))
        self.add_display_controls(page)
        page.col.addStretch()
        return page

    @staticmethod
    def display_key(name):
        return (name or "").casefold()

    def add_display_controls(self, page):
        page.section("Display")
        self.display_applied = {}
        self.vibrance_range = None

        try:
            displays = display_controls.get_all_displays()
        except OSError:
            displays = []
        try:
            primary = display_controls._primary_device_name()
        except OSError:
            primary = None
        self.display_combo = FluentCombo()
        for name, label in displays:
            self.display_combo.addItem(label, name)
        if not displays:
            self.display_combo.addItem("Primary display", primary)
        for i in range(self.display_combo.count()):
            if self.display_key(self.display_combo.itemData(i)) == self.display_key(primary):
                self.display_combo.setCurrentIndex(i)
        self.current_display = self.display_combo.currentData()
        display_row = Row("Display", "Choose which monitor the settings below apply to")
        display_row.right.addWidget(self.display_combo)
        page.add(display_row)

        self.refresh_row = Row(
            "Highest refresh rate",
            "Checking this display's highest rate at its current resolution...")
        self.refresh_row.setObjectName("row")
        self.highest_refresh_button = Button("Check")
        self.highest_refresh_button.clicked.connect(self.apply_highest_refresh)
        self.refresh_row.right.addWidget(self.highest_refresh_button)
        page.add(self.refresh_row)
        self.display_status = QLabel("")
        self.display_status.setObjectName("muted")
        self.display_status.setWordWrap(True)
        self.display_status.setContentsMargins(16, 10, 16, 14)
        page.col.addWidget(self.display_status)

        def make_input():
            edit = QLineEdit()
            edit.setFixedWidth(72)
            edit.setMinimumHeight(40)
            edit.setAlignment(Qt.AlignCenter)
            return edit

        def add_slider_row(title, subtitle, value_label, edit, slider):
            row = Row(title, subtitle)
            row.right.addWidget(value_label)
            row.right.addWidget(edit)
            row.right.addWidget(slider)
            page.add(row)

        self.gamma_slider = QSlider(Qt.Horizontal)
        self.gamma_slider.setRange(40, 280)
        self.gamma_slider.setFixedWidth(220)
        self.gamma_value = QLabel()
        self.gamma_input = make_input()
        add_slider_row("Gamma", "Adjust the NVIDIA driver gamma correction",
                       self.gamma_value, self.gamma_input, self.gamma_slider)

        self.contrast_slider = QSlider(Qt.Horizontal)
        self.contrast_slider.setRange(0, 100)
        self.contrast_slider.setFixedWidth(220)
        self.contrast_value = QLabel()
        self.contrast_input = make_input()
        add_slider_row("Contrast", "Adjust contrast through the NVIDIA driver",
                       self.contrast_value, self.contrast_input, self.contrast_slider)

        self.vibrance_slider = QSlider(Qt.Horizontal)
        self.vibrance_slider.setFixedWidth(220)
        self.vibrance_value = QLabel()
        self.vibrance_input = make_input()
        add_slider_row("Digital vibrance", "Adjust NVIDIA digital vibrance",
                       self.vibrance_value, self.vibrance_input, self.vibrance_slider)

        self.gamma_contrast_timer = QTimer(self)
        self.gamma_contrast_timer.setSingleShot(True)
        self.gamma_contrast_timer.timeout.connect(self.push_gamma_contrast)
        self.vibrance_timer = QTimer(self)
        self.vibrance_timer.setSingleShot(True)
        self.vibrance_timer.timeout.connect(self.push_vibrance)
        self.gamma_slider.valueChanged.connect(self.queue_gamma_contrast)
        self.contrast_slider.valueChanged.connect(self.queue_gamma_contrast)
        self.vibrance_slider.valueChanged.connect(self.queue_vibrance)
        self.gamma_slider.sliderReleased.connect(lambda: self.gamma_contrast_timer.start(0))
        self.contrast_slider.sliderReleased.connect(lambda: self.gamma_contrast_timer.start(0))
        self.vibrance_slider.sliderReleased.connect(lambda: self.vibrance_timer.start(0))
        self.gamma_input.editingFinished.connect(self.gamma_input_changed)
        self.contrast_input.editingFinished.connect(self.contrast_input_changed)
        self.vibrance_input.editingFinished.connect(self.vibrance_input_changed)

        controls = Row("")
        self.display_reset_button = Button("Reset")
        self.display_reset_button.setFixedWidth(ROW_ACTION_BUTTON_WIDTH)
        self.display_reset_button.clicked.connect(self.reset_display_values)
        self.display_apply_button = Button("Apply")
        self.display_apply_button.setFixedWidth(ROW_ACTION_BUTTON_WIDTH)
        self.display_apply_button.clicked.connect(self.apply_display_settings)
        controls.right.addWidget(self.display_reset_button)
        controls.right.addWidget(self.display_apply_button)
        page.add(controls)

        self.migrate_legacy_display_cfg()
        self.apply_saved_display_settings()
        self.load_display(self.current_display)
        self.refresh_display_status()
        self.display_combo.currentIndexChanged.connect(self.on_display_changed)

    def migrate_legacy_display_cfg(self):
        legacy, self.cfg.legacy_display = self.cfg.legacy_display, None
        if not legacy:
            return
        try:
            primary = display_controls._primary_device_name()
        except OSError:
            primary = None
        if primary:
            self.cfg.nvidia_displays.setdefault(
                self.display_key(primary), {"gamma": legacy[0], "contrast": legacy[1]})

    def apply_saved_display_settings(self):
        control = self.nvidia_control
        if control is None or not control.supports_gamma_contrast:
            return
        for name in control.display_names:
            saved = self.cfg.nvidia_displays.get(self.display_key(name))
            if not saved or (saved["gamma"], saved["contrast"]) == (100, 50):
                continue
            if control.select(name):
                try:
                    control.set_gamma_contrast(saved["gamma"] / 100, saved["contrast"])
                except display_controls.DisplayError as ex:
                    self.logged.emit(f"NVIDIA display adjustment failed for {name}: {ex}")

    def load_display(self, name):
        """Show the kept (applied) values of a display in the controls and enable what it supports."""
        control = self.nvidia_control
        supported = control is not None and control.select(name)
        gamma_ok = supported and control.supports_gamma_contrast
        vibrance_ok = supported and control.supports_vibrance
        if control is None:
            status = f"NVIDIA display controls are unavailable: {self.nvidia_error}"
        elif not supported:
            status = ("This display is not connected to an NVIDIA output, so gamma, contrast and "
                      "digital vibrance can't be changed for it.")
        else:
            status = ("Settings are applied through the NVIDIA driver; the NVIDIA App is not required. "
                      "Changes preview live and are only kept once you press Apply.")
            if not gamma_ok:
                status += " NVIDIA gamma-correction control is unavailable."

        self.vibrance_range = None
        state = None
        if supported:
            key = self.display_key(name)
            state = self.display_applied.get(key)
            if state is None:
                saved = self.cfg.nvidia_displays.get(key, {})
                state = {"gamma": saved.get("gamma", 100), "contrast": saved.get("contrast", 50),
                         "vibrance": None}
                self.display_applied[key] = state
            if vibrance_ok:
                try:
                    low, high, current = control.vibrance_info()
                except display_controls.DisplayError as ex:
                    vibrance_ok = False
                    status += f" {ex}"
                else:
                    self.vibrance_range = (low, high)
                    if state["vibrance"] is None:
                        state["vibrance"] = current
            else:
                status += " NVIDIA digital-vibrance control is unavailable."

        self.display_status.setText(status)
        for widget in (self.gamma_slider, self.gamma_input, self.contrast_slider, self.contrast_input):
            widget.setEnabled(bool(gamma_ok))
        for widget in (self.vibrance_slider, self.vibrance_input):
            widget.setEnabled(bool(vibrance_ok))
        self.display_reset_button.setEnabled(bool(gamma_ok or vibrance_ok))
        self.display_apply_button.setEnabled(bool(gamma_ok or vibrance_ok))
        self.set_display_controls(
            state["gamma"] if gamma_ok else None,
            state["contrast"] if gamma_ok else None,
            state["vibrance"] if vibrance_ok else None)

    def set_display_controls(self, gamma, contrast, vibrance):
        """Move the sliders without triggering a live preview; None shows a control as unavailable."""
        sliders = (self.gamma_slider, self.contrast_slider, self.vibrance_slider)
        for slider in sliders:
            slider.blockSignals(True)
        if gamma is not None:
            self.gamma_slider.setValue(gamma)
            self.contrast_slider.setValue(contrast)
        if vibrance is not None and self.vibrance_range:
            self.vibrance_slider.setRange(*self.vibrance_range)
            self.vibrance_slider.setValue(vibrance)
        for slider in sliders:
            slider.blockSignals(False)
        if gamma is None:
            for label, edit in ((self.gamma_value, self.gamma_input), (self.contrast_value, self.contrast_input)):
                label.setText("Unavailable")
                edit.setText("")
        else:
            self.show_gamma_contrast()
        if vibrance is None:
            self.vibrance_value.setText("Unavailable")
            self.vibrance_input.setText("")
        else:
            self.show_vibrance()

    def display_values(self):
        """((gamma, contrast) or None, vibrance or None) currently shown for the selected display."""
        gamma = ((self.gamma_slider.value(), self.contrast_slider.value())
                 if self.gamma_slider.isEnabled() else None)
        vibrance = (self.vibrance_slider.value()
                    if self.vibrance_slider.isEnabled() and self.vibrance_range else None)
        return gamma, vibrance

    def display_dirty(self):
        state = self.display_applied.get(self.display_key(self.current_display))
        if state is None or self.nvidia_control is None:
            return False
        gamma, vibrance = self.display_values()
        return bool((gamma is not None and gamma != (state["gamma"], state["contrast"]))
                    or (vibrance is not None and vibrance != state["vibrance"]))

    def revert_display(self):
        """Drop live previews: put the driver back to the kept values of the selected display."""
        self.gamma_contrast_timer.stop()
        self.vibrance_timer.stop()
        if not self.display_dirty():
            return
        control = self.nvidia_control
        state = self.display_applied[self.display_key(self.current_display)]
        gamma, vibrance = self.display_values()
        if not control.select(self.current_display):
            return
        try:
            if gamma is not None and gamma != (state["gamma"], state["contrast"]):
                control.set_gamma_contrast(state["gamma"] / 100, state["contrast"])
            if vibrance is not None and vibrance != state["vibrance"]:
                control.set_vibrance(state["vibrance"])
        except display_controls.DisplayError as ex:
            self.logged.emit(f"Could not restore the previous display settings: {ex}")

    def on_display_changed(self, *_):
        self.revert_display()
        self.current_display = self.display_combo.currentData()
        self.load_display(self.current_display)
        self.refresh_display_status()

    def reset_display_values(self):
        """Preview gamma 1.00, contrast 50 and vibrance 50; Apply keeps them."""
        if self.gamma_slider.isEnabled():
            self.gamma_slider.setValue(100)
            self.contrast_slider.setValue(50)
        if self.vibrance_slider.isEnabled() and self.vibrance_range:
            low, high = self.vibrance_range
            self.vibrance_slider.setValue(max(low, min(high, 50)))

    def apply_display_settings(self):
        control = self.nvidia_control
        if control is None or not control.select(self.current_display):
            return
        self.gamma_contrast_timer.stop()
        self.vibrance_timer.stop()
        self.push_gamma_contrast()
        self.push_vibrance()
        gamma, vibrance = self.display_values()
        key = self.display_key(self.current_display)
        state = self.display_applied[key]
        if gamma is not None:
            state["gamma"], state["contrast"] = gamma
            self.cfg.nvidia_displays[key] = {"gamma": gamma[0], "contrast": gamma[1]}
        if vibrance is not None:
            state["vibrance"] = vibrance
        self.cfg.save()
        self.logged.emit(f"Applied display settings to {self.display_combo.currentText()}.")

    def set_refresh_row(self, text, button, enabled, attention=False):
        self.refresh_row.sub.setText(text)
        self.highest_refresh_button.setText(button)
        self.highest_refresh_button.setEnabled(enabled)
        self.refresh_row.setObjectName("attention" if attention else "row")
        self.refresh_row.style().unpolish(self.refresh_row)
        self.refresh_row.style().polish(self.refresh_row)

    def refresh_display_status(self):
        try:
            info = display_controls.refresh_info(self.current_display)
        except (display_controls.DisplayError, OSError) as ex:
            self.set_refresh_row(str(ex), "Unavailable", False)
            return
        current, highest = info["current"], info["highest"]
        size = f"{info['width']}×{info['height']}"
        if highest > current:
            self.set_refresh_row(
                f"Highest refresh rate not applied · Current {current} Hz · Available {highest} Hz at {size}.",
                f"Apply {highest} Hz", True, True)
        else:
            self.set_refresh_row(
                f"Highest available rate is active · {current} Hz at {size}.", "Applied", False)

    def apply_highest_refresh(self):
        try:
            display_controls.apply_highest_refresh(self.current_display)
        except (display_controls.DisplayError, OSError) as ex:
            self.display_status.setText(f"Could not apply the highest refresh rate: {ex}")
            self.logged.emit(f"Display refresh-rate change failed: {ex}")
            return
        self.refresh_display_status()
        self.logged.emit(f"Applied the highest refresh rate available for {self.display_combo.currentText()}.")

    @staticmethod
    def vibrance_text(value):
        return f"{value}%" if 0 <= value <= 100 else str(value)

    def show_gamma_contrast(self):
        gamma, contrast = self.gamma_slider.value(), self.contrast_slider.value()
        self.gamma_value.setText(f"{gamma / 100:.2f}")
        self.gamma_input.setText(f"{gamma / 100:.2f}")
        self.contrast_value.setText(f"{contrast}%")
        self.contrast_input.setText(str(contrast))

    def show_vibrance(self):
        value = self.vibrance_slider.value()
        self.vibrance_value.setText(self.vibrance_text(value))
        self.vibrance_input.setText(str(value))

    def queue_gamma_contrast(self, *_):
        self.show_gamma_contrast()
        self.gamma_contrast_timer.start(300)

    def queue_vibrance(self, *_):
        self.show_vibrance()
        self.vibrance_timer.start(300)

    def gamma_input_changed(self):
        try:
            value = round(float(self.gamma_input.text().replace(",", ".")) * 100)
        except ValueError:
            value = self.gamma_slider.value()
        self.gamma_slider.setValue(max(40, min(280, value)))
        self.show_gamma_contrast()

    def contrast_input_changed(self):
        try:
            value = int(self.contrast_input.text())
        except ValueError:
            value = self.contrast_slider.value()
        self.contrast_slider.setValue(max(0, min(100, value)))
        self.show_gamma_contrast()

    def vibrance_input_changed(self):
        if not self.vibrance_range:
            return
        try:
            value = int(self.vibrance_input.text())
        except ValueError:
            value = self.vibrance_slider.value()
        self.vibrance_slider.setValue(max(self.vibrance_range[0], min(self.vibrance_range[1], value)))
        self.show_vibrance()

    def push_gamma_contrast(self):
        control = self.nvidia_control
        if control is None or not self.gamma_slider.isEnabled() or not control.select(self.current_display):
            return
        try:
            control.set_gamma_contrast(self.gamma_slider.value() / 100, self.contrast_slider.value())
        except display_controls.DisplayError as ex:
            for widget in (self.gamma_slider, self.gamma_input, self.contrast_slider, self.contrast_input):
                widget.setEnabled(False)
            self.display_status.setText(f"NVIDIA gamma or contrast adjustment failed: {ex}")
            self.logged.emit(f"NVIDIA display adjustment failed: {ex}")

    def push_vibrance(self):
        control = self.nvidia_control
        if (control is None or self.vibrance_range is None or not self.vibrance_slider.isEnabled()
                or not control.select(self.current_display)):
            return
        try:
            control.set_vibrance(self.vibrance_slider.value())
        except display_controls.DisplayError as ex:
            for widget in (self.vibrance_slider, self.vibrance_input):
                widget.setEnabled(False)
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
        INLINE = {"Appearance", "Performance", "NVIDIA", "System"}
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
                     is_active(fn))
        self.action_rows.append((row, fn))
        return row

    def build_storage(self):
        page = Page("Storage")
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 16, 0)  # rows keep 16px inside their edge: Rescan lines up with their buttons
        page.col.addSpacing(26)
        label = QLabel("Drives")
        label.setObjectName("section")
        header.addWidget(label)
        header.addStretch()
        self.rescan_btn = Button("Rescan")
        self.rescan_btn.setFixedWidth(ROW_ACTION_BUTTON_WIDTH)
        self.rescan_btn.setEnabled(False)
        self.rescan_btn.clicked.connect(lambda: self.refresh_drives(True))
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
        self.storage_scanned = False
        self.apps_expanded = False
        self.apps_anim = self.apps_extra = self.apps_more = None
        self.drive_rows = {}
        self.storage_ready.connect(self.show_usage)
        self.scan_finished.connect(lambda: self.rescan_btn.setEnabled(True))
        self.drives_ready.connect(self.show_drives)
        return page

    def refresh_drives(self, force=True):
        self.storage_scanned = True
        self.scan_force = force
        self.rescan_btn.setEnabled(False)

        def work():
            self.drives_ready.emit(storage.drives())
        threading.Thread(target=work, daemon=True).start()

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
        cached = None if self.scan_force else storage.load_cache(
            os.path.join(self.cfg.dir, "storage_cache.json"), roots)
        for root, label, free, total in items:
            info = self.drive_rows[root]
            info.update(total=total, free=free)
            info["row"].sub.setText(f"{apps.human(total - free)} used · {apps.human(free)} free of {apps.human(total)}")
            info["row"].sub.show()
            if cached:
                continue
            if not info["legend_html"]:
                info["bar"].set_value(total_fraction(total, free))
                info["legend"].setText('<span style="color:#8a8a8a">Scanning...</span>')
            info["bar"].set_scanning(True)
        if cached:  # fresh saved results: no scan at all
            for root in roots:
                self.apply_usage(root, cached[root])
            self.render_apps()
            self.rescan_btn.setEnabled(True)
            return
        self.render_apps()
        cache_path = os.path.join(self.cfg.dir, "storage_cache.json")

        def work():
            storage.lower_priority()
            apps_future = Future()

            def load_apps():
                storage.lower_priority()
                try:
                    apps_future.set_result(storage.installed_apps())
                except Exception:
                    apps_future.set_result([])
            threading.Thread(target=load_apps, daemon=True).start()
            results = {}

            def scan(root):
                if storage.is_removable(root):  # USB sticks etc.: usage bar only
                    res = {"root": [], "user": [], "categories": [], "apps": [], "skipped": True}
                else:
                    try:
                        res = storage.analyze(root, apps_future.result)
                    except Exception:
                        res = {"root": [], "user": [], "categories": [], "apps": [], "failed": True}
                results[root] = res
                self.storage_ready.emit(root, res)  # each drive shows up as soon as it finishes
            with ThreadPoolExecutor(max(1, min(3, len(roots))), initializer=storage.lower_priority) as pool:
                list(pool.map(scan, roots))
            if not any(r.get("failed") for r in results.values()):
                storage.save_cache(cache_path, results)
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

    def apply_usage(self, root, res):
        self.show_drive_breakdown(root, res.get("categories", []))
        self.storage_apps[root] = res.get("apps", [])
        if res.get("skipped") and root in self.drive_rows:
            self.drive_rows[root]["legend"].setText(
                '<span style="color:#8a8a8a">Removable drive, not scanned</span>')

    def show_usage(self, root, res):
        self.apply_usage(root, res)
        self.render_apps()

    def app_row(self, app, top):
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
        row.right.addSpacing(14)  # moves the size left, leaving the button more room
        row.right.addWidget(u)
        return row

    def render_apps(self):
        if self.apps_anim is not None:
            self.apps_anim.stop()
            self.apps_anim = None
        self.apps_extra = self.apps_more = None
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
        top = everything[0]["size"] or 1
        for app in everything[:8]:
            self.apps_box.addWidget(self.app_row(app, top))
        if len(everything) > 8:
            extra = QWidget()
            lay = QVBoxLayout(extra)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
            for app in everything[8:]:
                lay.addWidget(self.app_row(app, top))
            extra.setVisible(self.apps_expanded)
            QTimer.singleShot(0, lambda: isValid(extra) and [w.ensurePolished() for w in extra.findChildren(QWidget)])
            self.apps_extra = extra
            self.apps_box.addWidget(extra)
            more = Button("Show less" if self.apps_expanded else "Show more")
            more.setFixedWidth(ROW_ACTION_BUTTON_WIDTH)
            more.clicked.connect(self.toggle_apps)
            self.apps_more = more
            holder = QHBoxLayout()
            holder.setContentsMargins(0, 8, 16, 0)
            holder.addStretch()
            holder.addWidget(more)
            wrap = QWidget()
            wrap.setLayout(holder)
            self.apps_box.addWidget(wrap)

    def toggle_apps(self):
        extra = self.apps_extra
        if extra is None:
            return
        self.apps_expanded = expanding = not self.apps_expanded
        self.apps_more.setText("Show less" if expanding else "Show more")
        if self.apps_anim is not None:
            self.apps_anim.stop()
            self.apps_anim.deleteLater()
        fx = extra.graphicsEffect()
        if fx is None:
            fx = QGraphicsOpacityEffect(extra)
            fx.setOpacity(1.0 if extra.isVisible() else 0.0)
            extra.setGraphicsEffect(fx)
        if expanding:
            extra.show()
        anim = QPropertyAnimation(fx, b"opacity", self)
        anim.setDuration(240 if expanding else 150)
        anim.setStartValue(fx.opacity())
        anim.setEndValue(1.0 if expanding else 0.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)

        def done():
            self.apps_anim = None
            if isValid(extra):
                extra.setGraphicsEffect(None)
                extra.setVisible(expanding)
        anim.finished.connect(done)
        self.apps_anim = anim
        anim.start()

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
        page.col.setContentsMargins(0, 48 + (TITLEBAR_H - 16 if CUSTOM_TITLEBAR else 0), 0, 120)
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
        else:
            count, titles, optional = result
            if count == 0:
                status = "Windows is up to date"
            else:
                noun = "update" if count == 1 else "updates"
                status = f"{count} {noun} available · {titles[0]}" if titles else f"{count} {noun} available"
                if count > 1:
                    status += f" and {count - 1} more"
            if optional:
                noun = "optional update" if optional == 1 else "optional updates"
                status += f"\n{optional} {noun} (e.g. drivers) under Windows Update > Advanced options > Optional updates"
        row.set_value(status)
        row.set_attention(result is not None and result[0] > 0)

    def copy_all_specs(self):
        QApplication.clipboard().setText(self.spec_text)
        self.copy_specs_btn.setText("Copied")
        QTimer.singleShot(1200, lambda: self.copy_specs_btn.setText("Copy all"))

    def build_activity(self):
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(20, 48 + (TITLEBAR_H - 16 if CUSTOM_TITLEBAR else 0), 20, 28)
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
        caption = Row("Window buttons", "Minimize, maximize and close: Windows or macOS style")
        self.caption_combo = FluentCombo()
        self.caption_combo.addItem("Windows", "windows")
        self.caption_combo.addItem("macOS", "mac")
        self.caption_combo.setCurrentIndex(self.caption_combo.findData(self.cfg.caption_style))
        self.caption_combo.currentIndexChanged.connect(lambda: self.set_caption_style())
        caption.right.addWidget(self.caption_combo)
        page.add(caption)
        accent = Row("Accent color", "Color of the selection pill in the sidebar")
        self.accent_swatches = ColorSwatches(ACCENTS, self.cfg.accent)
        self.accent_swatches.changed.connect(self.set_accent)
        accent.right.addWidget(self.accent_swatches)
        page.add(accent)
        self.sidebar.set_accent(self.cfg.accent)
        page.section("Downloads")
        self.dl_row = Row("Download location", self.cfg.download_dir)
        for text, fn in (("Change", self.pick_dir), ("Open", self.open_dir)):
            b = Button(text)
            b.clicked.connect(fn)
            self.dl_row.right.addWidget(b)
        page.add(self.dl_row)
        page.section("About")
        self.update_url = self.update_tag = self.update_file = ""
        self.update_job = None
        self.update_serial, self.update_pending = 0, False
        self.about_row = Row("Kernel", f"Version {VERSION}")
        self.update_download = Button("Download")
        self.update_download.clicked.connect(self.on_update_download_clicked)
        self.update_download.hide()
        self.update_button = Button("Check for updates")
        self.update_button.clicked.connect(self.check_for_updates)
        self.about_row.right.addWidget(self.update_download)
        self.about_row.right.addWidget(self.update_button)
        self.update_checked.connect(self.show_update_result)
        page.add(self.about_row)
        page.col.addStretch()
        return page

    def check_for_updates(self):
        if self.update_pending:
            return
        self.update_serial += 1
        serial = self.update_serial
        self.update_pending = True
        self.update_button.setEnabled(False)
        self.update_download.hide()
        self.set_update_status("Checking...")
        try:
            threading.Thread(target=self.fetch_latest_release, args=(serial,), daemon=True).start()
        except Exception:
            self.update_timed_out(serial)
            return
        QTimer.singleShot(25000, lambda: self.update_timed_out(serial))

    UPDATE_HEADERS = {"User-Agent": "Kernel-Updater", "Cache-Control": "no-cache", "Pragma": "no-cache"}

    def latest_from_redirect(self):
        # github.com/<repo>/releases/latest redirects to the newest tag; no API rate limit
        import urllib.request
        from urllib.parse import unquote
        req = urllib.request.Request(
            f"https://github.com/{UPDATE_REPO}/releases/latest?nocache={int(time.time())}",
            method="HEAD", headers=self.UPDATE_HEADERS)
        with apps._open(req, 5) as r:
            final = r.geturl()
        if "/releases/tag/" not in final:
            raise LookupError("none")  # the repo answered, but has no published release
        return unquote(final.rsplit("/releases/tag/", 1)[1].split("?")[0])

    def latest_from_api(self):
        # newest tag across ALL published releases, so a stale "Latest" badge can't hide a newer one
        import urllib.request
        req = urllib.request.Request(
            f"https://api.github.com/repos/{UPDATE_REPO}/releases?per_page=30",
            headers={**self.UPDATE_HEADERS, "Accept": "application/vnd.github+json"})
        with apps._open(req, 5) as r:
            data = json.loads(r.read().decode("utf-8"))
        tags = [str(x.get("tag_name", "")) for x in data if not x.get("draft") and not x.get("prerelease")]
        tags = [t for t in tags if t]
        if not tags:
            raise LookupError("none")
        return max(tags, key=version_tuple)

    def fetch_latest_release(self, serial):
        tags, error, none = [], "", False
        for _ in range(2):  # retry once; networks time out now and then
            for method in (self.latest_from_redirect, self.latest_from_api):
                try:
                    tags.append(method())
                except LookupError:
                    none = True
                except Exception as ex:
                    error = str(ex) or "failed"
            if tags:
                break
        if tags:
            tag = max(tags, key=version_tuple)  # newest answer wins
            self.update_checked.emit(serial, tag, f"https://github.com/{UPDATE_REPO}/releases/tag/{tag}", "")
        else:
            self.update_checked.emit(serial, "", "", "none" if none and not error else (error or "failed"))

    def on_update_download_clicked(self):
        if self.update_file and os.path.isfile(self.update_file):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.update_file)])
            return
        self.download_update()

    def resolve_update_asset(self, tag):
        """(url, file name, size) of the installer attached to a release."""
        import urllib.request
        from urllib.parse import quote
        name = f"Kernel-Setup-{tag.lstrip('vV')}.exe"
        guess = (f"https://github.com/{UPDATE_REPO}/releases/download/{quote(tag)}/{name}", name, 0)
        try:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{UPDATE_REPO}/releases/tags/{quote(tag)}",
                headers={**self.UPDATE_HEADERS, "Accept": "application/vnd.github+json"})
            with apps._open(req, 8) as r:
                data = json.loads(r.read().decode("utf-8"))
            for a in data.get("assets", []):
                n = os.path.basename(str(a.get("name", "")))
                if n.lower().endswith(".exe") and a.get("browser_download_url"):
                    return a["browser_download_url"], n, int(a.get("size") or 0)
        except Exception:
            pass
        return guess

    def download_update(self):
        tag = self.update_tag
        if not tag:
            return

        def work(job):
            import urllib.request
            url, name, total = self.resolve_update_asset(tag)
            folder = self.cfg.download_dir
            os.makedirs(folder, exist_ok=True)
            dest = os.path.join(folder, name)
            part = dest + ".part"
            req = urllib.request.Request(url, headers={"User-Agent": "Kernel-Updater"})
            got = 0
            with apps._open(req, 30) as r, open(part, "wb") as f:
                total = int(r.headers.get("Content-Length") or total or 0)
                while not job.cancelled:
                    chunk = r.read(262144)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        job.update(progress=got / total, message=f"{apps.human(got)} / {apps.human(total)}")
            if job.cancelled or (total and got < total):
                try:
                    os.remove(part)
                except OSError:
                    pass
                return False
            os.replace(part, dest)
            job.update(path=dest, size=got)
            self.logged.emit(f"Update downloaded to {dest}")
            return True

        job = Job(f"Kernel {tag.lstrip('vV')} update", "download", fn=work)
        job.done_msg = "Downloaded"
        self.update_job = job
        job.changed.connect(self.on_update_job_changed)  # bound method: runs on the UI thread
        self.update_download.setEnabled(False)
        self.set_update_status("Downloading update...", "#ffb454")
        self.jobs.submit(job, parallel=True)

    def on_update_job_changed(self):
        self.show_update_download(self.update_job)

    def show_update_download(self, job):
        if job.state in ("queued", "running"):
            pct = f" {int(job.progress * 100)}%" if job.progress >= 0 else "..."
            self.set_update_status(f"Downloading update{pct}", "#ffb454")
        elif job.state == "done":
            self.update_file = job.path
            self.update_download.setText("Show file")
            self.update_download.setEnabled(True)
            self.set_update_status(f"Update downloaded: {os.path.basename(job.path)}", "#4fd08a")
        else:
            self.update_download.setText("Download")
            self.update_download.setEnabled(True)
            self.set_update_status("Download failed, try again", "#ffb454")

    def set_update_status(self, text, color=""):
        self.about_row.sub.setStyleSheet(f"color: {color};" if color else "")
        self.about_row.sub.setText(f"Version {VERSION}  \u00b7  {text}")

    def update_timed_out(self, serial):
        if serial == self.update_serial and self.update_pending:
            self.update_pending = False
            self.update_button.setEnabled(True)
            self.set_update_status("Couldn't reach the update server, try again")

    def show_update_result(self, serial, tag, url, error):
        if serial != self.update_serial or not self.update_pending:
            return
        self.update_pending = False
        self.update_button.setEnabled(True)
        if error == "none":
            self.set_update_status("No release published yet")
        elif error or not tag:
            self.set_update_status("Couldn't reach the update server, try again")
        elif version_tuple(tag) > version_tuple(VERSION):
            self.update_url, self.update_tag, self.update_file = url, tag, ""
            self.update_download.setText("Download")
            self.update_download.setEnabled(True)
            self.set_update_status(f"Update available: {tag.lstrip('vV')}", "#ffb454")
            self.update_download.show()
        else:
            self.set_update_status("Up to date", "#4fd08a")

    def set_cpu_temp_mode(self):
        self.cfg.cpu_temp_mode = self.cpu_temp_combo.currentData()
        self.cfg.save()
        if hasattr(self, "latest_monitor_data"):
            self.update_monitor(self.latest_monitor_data)

    def sync_nvidia_controls(self, apply=False):
        self.revert_display()
        self.migrate_legacy_display_cfg()
        self.display_applied.clear()
        if apply:
            self.apply_saved_display_settings()
        self.load_display(self.current_display)

    def set_accent(self, color):
        self.cfg.accent = color
        self.sidebar.set_accent(color)
        self.cfg.save()

    def set_caption_style(self):
        self.cfg.caption_style = self.caption_combo.currentData()
        self.cfg.save()
        if hasattr(self, "titlebar"):
            self.titlebar.set_caption_style(self.cfg.caption_style)

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

    def save_applied_states(self):
        self.cfg.applied = dict(self.applied_states)

    def refresh_tweak_rows(self):
        for row, key, _, fn in self.rows:
            active = is_active(fn)
            self.applied_states[key] = active
            row.set_active(active)
        for row, fn in self.action_rows:
            row.set_active(is_active(fn))

    def closeEvent(self, e):
        self.revert_display()  # unapplied previews are dropped so they don't outlive the app
        self.save_applied_states()
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

    def nativeEvent(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG":
            raw = wintypes.MSG.from_address(int(message))
            m = raw.message
            if m == 0x0231:  # WM_ENTERSIZEMOVE: a drag or resize started
                WindowState.moving = True
                self.titlebar.schedule(instant=True)  # blur matches the content right now, no fade-in
            elif m == 0x0232:  # WM_EXITSIZEMOVE: it ended
                WindowState.moving = WindowState.dragging = False
                self.titlebar.schedule(instant=True)
            elif m == 0x0046 and WindowState.dragging:  # WM_WINDOWPOSCHANGING while dragging the title bar
                # Dragging must only move the window. Fractional display scaling can make the size wobble by
                # a pixel or two on every step, which re-lays out every page and makes the content twitch
                # left and right. Drop those wobbles (real size changes, like un-maximizing or snapping, pass).
                wp = WINDOWPOS.from_address(raw.lParam)
                if not wp.flags & 0x0001:  # SWP_NOSIZE not already set
                    rect = wintypes.RECT()
                    ctypes.windll.user32.GetWindowRect(wintypes.HWND(raw.hWnd), ctypes.byref(rect))
                    if (abs(wp.cx - (rect.right - rect.left)) <= 3 and abs(wp.cy - (rect.bottom - rect.top)) <= 3
                            and not ctypes.windll.user32.IsZoomed(wintypes.HWND(raw.hWnd))):
                        wp.flags |= 0x0001
        if CUSTOM_TITLEBAR and bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0083:  # WM_NCCALCSIZE: the client area is the whole window (no native caption)
                if msg.wParam and ctypes.windll.user32.IsZoomed(wintypes.HWND(msg.hWnd)):
                    params = NCCALCSIZE_PARAMS.from_address(msg.lParam)
                    gm = ctypes.windll.user32.GetSystemMetrics
                    bx, by = gm(32) + gm(92), gm(33) + gm(92)  # sizeframe + padded border
                    r = params.rgrc[0]
                    r.left += bx
                    r.right -= bx
                    r.top += by
                    r.bottom -= by
                return True, 0
        return super().nativeEvent(event_type, message)

    def prewarm_layout(self):
        """The first maximize is slow because the whole UI is laid out and painted at screen size for the
        first time while Windows is already animating. Do that once now, before the window exists on
        screen (nothing visible, no Windows animation involved), then go back to the normal size."""
        try:
            normal = self.size()
            screen = self.screen() or QApplication.primaryScreen()
            self.resize(screen.availableGeometry().size())
            self.grab()  # lays out and renders every visible widget at full-screen size
            self.resize(normal)
            self.grab()  # and back, so the first real paint is cheap too
        except Exception:
            pass

    def prepolish_pages(self, index=0):
        """Applying the style sheet to a page's widgets happens the first time it is shown, which is the lag on
        a page's first visit. Do it ahead of time, one page per idle slice."""
        if index >= self.stack.count():
            return
        page = self.stack.widget(index)
        for w in [page] + page.findChildren(QWidget):
            w.ensurePolished()
        if page.layout() is not None:
            page.layout().activate()
        inner = page.widget() if hasattr(page, "widget") else None
        if inner is not None and inner.layout() is not None:
            inner.layout().activate()
        if index != self.stack.currentIndex() and not WindowState.moving:
            # Paint it once off-screen too: the first paint is what costs (glyph, gradient and image caches
            # fill up), and doing it now means the first click on that page doesn't stall.
            page.resize(self.stack.size())
            page.grab()
        QTimer.singleShot(120, lambda: self.prepolish_pages(index + 1))

    def warm_up(self):
        """Run the blur pipeline once on tiny images so the first maximize doesn't pay its start-up cost."""
        try:
            blurred(QPixmap(32, 32))
            TitleBar._blur_levels(QImage(32, 32, QImage.Format_ARGB32_Premultiplied), 3.0, 18.0)
        except Exception:
            pass

    def showEvent(self, e):
        super().showEvent(e)
        if not hasattr(self, "_warmed"):
            self._warmed = True
            QTimer.singleShot(700, self.warm_up)
            QTimer.singleShot(900, self.prepolish_pages)
        if CUSTOM_TITLEBAR:
            try:
                native_frame(int(self.winId()))
            except Exception:
                pass
        try:  # native dark title bar
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), 20, ctypes.byref(ctypes.c_int(1)), 4)
        except Exception:
            pass
        try:  # black native background brush: unpainted areas during the first maximize are black, not white
            gdi, user = ctypes.windll.gdi32, ctypes.windll.user32
            gdi.CreateSolidBrush.restype = ctypes.c_void_p
            user.SetClassLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            user.SetClassLongPtrW.restype = ctypes.c_void_p
            if not hasattr(self, "_bg_brush"):
                self._bg_brush = gdi.CreateSolidBrush(0x000000)
            user.SetClassLongPtrW(int(self.winId()), -10, self._bg_brush)  # GCLP_HBRBACKGROUND
        except Exception:
            pass

    # ---- tweaks ----

    def irreversible_names(self, operations):
        return [name for name, fn, target in operations if target and fn.__name__ in IRREVERSIBLE]

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
            self.refresh_tweak_rows()
            self.save_applied_states()
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
    w.prewarm_layout()
    w.show()
    sys.exit(app.exec())
