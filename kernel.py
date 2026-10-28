"""kernel - Windows 11 debloat & optimize tool. Build with build.bat."""
import ctypes
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time

from PySide6.QtCore import (
    Property, QEasingCurve, QEvent, QObject, QParallelAnimationGroup, QPoint, QPointF, QPropertyAnimation,
    QRect, QRectF, Qt, QTimer, QUrl, Signal, QSize,
)
from PySide6.QtGui import (
    QBrush, QColor, QDesktopServices, QFont, QFontDatabase, QGradient, QFontMetrics, QIcon, QImage, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QGraphicsBlurEffect, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QGraphicsPixmapItem, QGraphicsScene, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

import apps
from jobs import ACTIVE, Job, JobManager
from tweaks import TASKS, ONE_SHOT, is_active, revert_tweak

BG, BORDER, TEXT, MUTED = "#000000", "#1a1a1a", "#ffffff", "#7a7a7a"

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
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #262626; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #363636; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""

TWEAKS, APPS, ACTIVITY, SETTINGS = range(4)
GLYPH = {"tweaks": "\uE9E9", "apps": "\uF0E2", "downloads": "\uE896", "activity": "\uE9D9",
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
        self.smooth_scroll = True
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
        if isinstance(d.get("download_dir"), str) and d["download_dir"]:
            self.download_dir = d["download_dir"]
        if isinstance(d.get("smooth_scroll"), bool):
            self.smooth_scroll = d["smooth_scroll"]
        ws = d.get("window")
        if isinstance(ws, list) and len(ws) == 2 and all(isinstance(x, int) for x in ws):
            self.win_size = ws
        return True

    def save(self, path=None):
        path = path or self.path
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"toggles": self.toggles, "download_dir": self.download_dir,
                           "smooth_scroll": self.smooth_scroll, "window": self.win_size}, f, indent=2)
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
        self.setCursor(Qt.PointingHandCursor)
        self.a_hover = QPropertyAnimation(self, b"hover", self)
        self.a_press = QPropertyAnimation(self, b"press", self)
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

    hover = Property(float, get_hover, set_hover)
    press = Property(float, get_press, set_press)

    def enterEvent(self, e):
        self.go(self.a_hover, 1.0, 140)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.go(self.a_hover, 0.0, 200)
        super().leaveEvent(e)

    def squish(self, p, strength=0.035):
        c = QRectF(self.rect()).center()
        s = 1.0 - strength * max(self._press, 0.0)
        p.translate(c)
        p.scale(s, s)
        p.translate(-c)


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
        f = QFont(self.font())
        f.setPixelSize(self.k["size"])
        f.setWeight(self.k["weight"])
        self.setFont(f)
        self.setIconSize(QSize(18, 18))
        if kind == "accent":
            glow = QGraphicsDropShadowEffect(self)
            glow.setBlurRadius(34)
            glow.setOffset(0, 0)
            glow.setColor(QColor(110, 50, 250, 150))
            self.setGraphicsEffect(glow)

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
        c = lambda *v: QColor(*(int(x * dark) for x in v))
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.save()
        self.squish(p)
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0.0, c(112, 52, 250) if on else QColor(40, 40, 40))
        g.setColorAt(1.0, c(88, 30, 236) if on else QColor(30, 30, 30))
        p.fillPath(path, QBrush(g))
        edge = QLinearGradient(r.topLeft(), r.bottomLeft())
        edge.setColorAt(0.0, QColor(255, 255, 255, 90 if on else 20))
        edge.setColorAt(1.0, QColor(255, 255, 255, 28 if on else 10))
        p.setPen(QPen(QBrush(edge), 1.2))
        p.drawPath(path)
        p.restore()
        p.setFont(self.font())
        p.setPen(QColor(255, 255, 255) if on else QColor(110, 110, 110))
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
    def __init__(self):
        super().__init__()
        self.value = -1.0
        self.setFixedHeight(5)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)

    def set_value(self, v):
        self.value = v
        if v < 0 and not self.timer.isActive():
            self.timer.start(16)
        elif v >= 0:
            self.timer.stop()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, w, h), 2, 2)
        p.fillPath(track, QColor("#1f1f1f"))
        p.setClipPath(track)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        if self.value >= 0:
            p.drawRect(QRectF(0, 0, w * min(self.value, 1.0), h))
        else:
            seg = w * 0.3
            x = (w + seg) * ((time.monotonic() * 0.8) % 1.0) - seg
            p.drawRect(QRectF(x, 0, seg, h))


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


class Row(HoverFrame):
    def __init__(self, title, subtitle="", icon=None):
        super().__init__()
        self.setObjectName("row")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12 if icon else 16, 16, 12 if icon else 16)
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
        lay.addLayout(text, 1)
        self.right = QHBoxLayout()
        self.right.setSpacing(8)
        lay.addLayout(self.right)


class TaskRow(Row):
    def __init__(self, title, subtitle, on_apply=None, on_revert=None):
        super().__init__(title, subtitle)
        self.setCursor(Qt.PointingHandCursor)
        self.apply = None
        self.revert = None
        if on_apply:
            self.apply = Button("Apply")
            self.apply.setCursor(Qt.PointingHandCursor)
            self.apply.setMinimumWidth(96)
            self.apply.clicked.connect(on_apply)
            self.right.addWidget(self.apply)
        if on_revert:
            self.revert = Button("Revert")
            self.revert.setCursor(Qt.PointingHandCursor)
            self.revert.setMinimumWidth(96)
            self.revert.clicked.connect(on_revert)
            self.right.addWidget(self.revert)
        self.toggle = Toggle()
        self.right.addWidget(self.toggle)

    def set_active(self, active):
        if self.apply is None:
            return
        self.apply.setText("Applied" if active else "Apply")
        self.apply.setEnabled(not active)
        self.revert.setEnabled(active)
        self.toggle.setEnabled(not active)
        if active:
            self.toggle.set_quiet(False)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.toggle.isEnabled():
            self.toggle.set(not self.toggle.on)


class RunRow(Row):
    """Single-use action: one click runs it."""

    def __init__(self, title, subtitle, on_run, on_revert, active=False):
        super().__init__(title, subtitle)
        self.b = Button("Run")
        self.b.setCursor(Qt.PointingHandCursor)
        self.b.setMinimumWidth(116)
        self.b.clicked.connect(on_run)
        self.right.addWidget(self.b)
        self.revert = Button("Revert")
        self.revert.setCursor(Qt.PointingHandCursor)
        self.revert.setMinimumWidth(116)
        self.revert.clicked.connect(on_revert)
        self.right.addWidget(self.revert)
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
        self.buttons = []
        for i, a in enumerate(actions):
            b = Button(a[0])
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(116)
            b.clicked.connect(lambda _=False, i=i: self.click(i))
            self.right.addWidget(b)
            self.buttons.append(b)

    def set_info(self, version, size):
        self.sub.setText(f"v{version.lstrip('vV')}" + (f"  ·  {apps.human(size)}" if size else ""))
        self.sub.show()

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
        content = QWidget()
        content.setObjectName("content")
        self.setWidget(content)
        SmoothScroller(self)
        outer = QHBoxLayout(content)
        outer.setContentsMargins(20, 0, 20, 0)
        col = QWidget()
        col.setMaximumWidth(760)
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
        top_left = self.page.mapFrom(self.parentWidget(), self.geometry().topLeft())
        shot = self.page.grab(QRect(top_left, self.size()))
        if not shot.isNull():
            shot.setDevicePixelRatio(1.0)
            self.backdrop = blurred(shot, radius=22)
            self.update()

    def place(self):
        host = self.parentWidget()
        w = min(760, host.width() - 40)
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
            p.drawPixmap(QRectF(self.rect()), self.backdrop, QRectF(self.backdrop.rect()))
            p.fillPath(path, QColor(14, 14, 14, 125))
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

class Kernel(QWidget):
    logged = Signal(str)
    found = Signal(list)
    icon_ready = Signal(str, QImage)

    def __init__(self):
        super().__init__()
        self.cfg = Config()
        SmoothScroller.on = self.cfg.smooth_scroll
        self.jobs = JobManager(os.path.join(self.cfg.dir, "downloads.json"))
        self.loading = True
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
        self.stack.addWidget(self.build_activity())
        self.stack.addWidget(self.build_settings())

        self.navs = []
        group = QButtonGroup(self)
        for text, idx, g in (("Tweaks", TWEAKS, "tweaks"), ("Applications", APPS, "apps"),
                             ("Activity", ACTIVITY, "activity")):
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

        for r, key, _, _ in self.rows:
            r.toggle.set_quiet(self.cfg.toggles.get(key, False))
            r.toggle.toggled.connect(self.on_toggle)
        for job in self.jobs.load():
            self.add_job_row(job)
        self.loading = False
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
        self.fade = None
        self.place_overlay()

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
        if self.overlay.isVisible():
            return
        self.pill.hide()
        shot = self.grab()
        self.pill.show()
        self.overlay.panel.bg = blurred(shot)
        self.place_overlay()
        self.pill.setChecked(True)
        self.run_fade(0.0, 1.0, show=True)
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
        self.footer.setVisible(index == TWEAKS)
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
                key, version, size = self.info_done.get_nowait()
            except queue.Empty:
                return
            row = self.info_rows.get(key)
            if row is not None and isValid(row) and version:
                row.set_info(version, size)

    def info_loop(self):
        while True:
            key = self.info_q.get()
            try:
                version, size = apps.info(key)
            except Exception:
                continue
            self.info_done.put((key, version, size))

    def on_icon(self, key, img):
        self.icons[key] = img
        for w in self.icon_widgets.get(key, []):
            if isValid(w):
                w.set_image(img)

    # ---- pages ----

    def build_tweaks(self):
        page = Page("Tweaks")
        SECTION_ORDER = ("Appearance", "System", "Gaming")
        CATEGORY_MAP = {
            "Privacy": "System",
            "Apps": "System",
            "Input": "System",
            "Security": "System",
        }
        INLINE = {"Appearance", "Gaming"}
        visible = [(CATEGORY_MAP.get(cat, cat), name, desc, fn)
                   for cat, name, desc, fn in TASKS]
        toggles = [t for t in visible if t[3].__name__ not in ONE_SHOT]
        def run_row(name, fn):
            row = RunRow(name, next(t[2] for t in TASKS if t[3] is fn),
                         lambda _=False, n=name, f=fn: self.run_tasks([(n, f)]),
                         lambda _=False, n=name, f=fn: self.run_reverts([(n, f)]),
                         is_active(fn))
            self.action_rows.append((row, fn))
            return row
        for cat in SECTION_ORDER:
            page.section(cat)
            for _, name, desc, fn in (t for t in toggles if t[0] == cat):
                r = TaskRow(
                    name, desc,
                    lambda _=False, n=name, f=fn: self.run_tasks([(n, f)]),
                    lambda _=False, n=name, f=fn: self.run_reverts([(n, f)]))
                r.set_active(is_active(fn))
                self.rows.append((r, fn.__name__, name, fn))
                page.add(r)
            action_names = ONE_SHOT if cat in INLINE else set()
            if action_names:
                for _, name, desc, fn in (t for t in visible if t[0] == cat and t[3].__name__ in action_names):
                    page.add(run_row(name, fn))
        page.section("Quick actions")
        for _, name, desc, fn in (t for t in TASKS
                                  if t[3].__name__ in ONE_SHOT
                                  and t[0] not in INLINE):
            page.add(run_row(name, fn))
        page.col.addStretch()
        return page

    def app_actions(self, pkg_id):
        def dl(job):
            return apps.download(pkg_id, self.cfg.download_dir, job)
        return [("Download", "Downloaded", "download", dl),
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

    def build_activity(self):
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(20, 28, 20, 28)
        t = QLabel("Activity")
        t.setObjectName("title")
        pl.addWidget(t)
        pl.addSpacing(16)
        bar = QHBoxLayout()
        bar.addStretch()
        clear = Button("Clear")
        clear.setCursor(Qt.PointingHandCursor)
        clear.setMinimumWidth(96)
        bar.addWidget(clear)
        pl.addLayout(bar)
        pl.addSpacing(8)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        clear.clicked.connect(self.log.clear)
        SmoothScroller(self.log)
        pl.addWidget(self.log, 1)
        return page

    def build_settings(self):
        page = Page("Settings")
        page.section("Interface")
        smooth = TaskRow("Smooth scrolling", "Eased, inertial mouse-wheel scrolling")
        smooth.toggle.set_quiet(self.cfg.smooth_scroll)
        smooth.toggle.toggled.connect(self.set_smooth)
        self.smooth_toggle = smooth.toggle
        page.add(smooth)
        page.section("Downloads")
        self.dl_row = Row("Download location", self.cfg.download_dir)
        for text, fn in (("Change", self.pick_dir), ("Open", self.open_dir)):
            b = Button(text)
            b.clicked.connect(fn)
            self.dl_row.right.addWidget(b)
        page.add(self.dl_row)
        page.section("Profile")
        prof = Row("Settings file", "Selected toggles and download location are saved automatically")
        for text, fn in (("Export", self.export_settings), ("Import", self.import_settings)):
            b = Button(text)
            b.clicked.connect(fn)
            prof.right.addWidget(b)
        page.add(prof)
        page.col.addStretch()
        return page

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
            ok = self.cfg.save(path)
            self.logged.emit(f"Settings exported to {path}" if ok else "Export failed.")

    def import_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import settings", "", "JSON (*.json)")
        if not path:
            return
        if not self.cfg.load(path):
            self.logged.emit("Import failed: not a valid settings file.")
            return
        self.loading = True
        for r, key, _, _ in self.rows:
            r.toggle.set_quiet(self.cfg.toggles.get(key, False))
        self.loading = False
        self.dl_row.sub.setText(self.cfg.download_dir)
        self.smooth_toggle.set_quiet(self.cfg.smooth_scroll)
        SmoothScroller.on = self.cfg.smooth_scroll
        self.update_status()
        self.cfg.save()
        self.logged.emit(f"Settings imported from {path}")

    def on_toggle(self, *_):
        if self.loading:
            return
        self.update_status()
        self.cfg.toggles = {key: r.toggle.on for r, key, _, _ in self.rows}
        if not (self.isMaximized() or self.isFullScreen()):
            self.cfg.win_size = [self.width(), self.height()]
        self.cfg.save()

    def update_status(self):
        n = sum(r.toggle.on for r, *_ in self.rows)
        self.status.setText(f"{n} selected" if n else "Nothing selected")

    def closeEvent(self, e):
        self.cfg.toggles = {key: r.toggle.on for r, key, _, _ in self.rows}
        if not (self.isMaximized() or self.isFullScreen()):
            self.cfg.win_size = [self.width(), self.height()]
        self.cfg.save()
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

    def start(self):
        sel = [(name, fn) for r, _, name, fn in self.rows if r.toggle.on]
        self.run_tasks(sel)

    def run_tasks(self, sel):
        self.run_operations(sel, False)

    def run_reverts(self, sel):
        self.run_operations(sel, True)

    def run_operations(self, sel, reverting):
        if not sel or (self.tweak_job and self.tweak_job.active):
            return
        self.btn.setEnabled(False)
        self.goto(ACTIVITY)

        def work(job):
            for name, fn in sel:
                job.log(f"== {'Revert ' if reverting else ''}{name}")
                try:
                    if reverting:
                        revert_tweak(fn, job.log)
                    elif is_active(fn):
                        job.log(f"Skipped {name}: already active.")
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
            self.btn.setEnabled(True)
            for row, _, _, fn in self.rows:
                row.set_active(is_active(fn))
            for row, fn in self.action_rows:
                row.set_active(is_active(fn))


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
