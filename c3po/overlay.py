#!/usr/bin/env python3
"""Floating bilingual subtitle overlay (native macOS window).

A translucent always-on-top panel that tails c3po's subtitle file and shows the
most recent lines as "English original" above "Chinese translation", so the
subtitles stay visible while Zoom/Teams covers the browser.

Runs as its own process (PyObjC needs the main thread for its run loop, which
uvicorn already owns). The server starts it and toggles visibility by
creating/removing a flag file, which this process polls on its refresh timer.

Controls:
    Move          - drag anywhere on the window
    Resize        - drag the diagonal grip in the bottom-right corner
    Appearance    - right-click for font / size / lines / opacity
    Quit          - right-click menu -> Quit

Appearance and window geometry persist to
~/Library/Application Support/meeting-translator/overlay.json

Usage:
    python -m c3po.overlay [--subtitle-file PATH] [--visible-flag PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

import objc
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory,
    NSPanel, NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
    NSBackingStoreBuffered, NSColor, NSFont, NSTextField, NSScreen,
    NSStatusWindowLevel, NSTimer, NSMakeRect, NSMakeSize, NSMakePoint, NSView,
    NSMenu, NSMenuItem, NSBezierPath, NSOnState, NSOffState, NSCursor,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSLineBreakByWordWrapping, NSViewWidthSizable, NSViewHeightSizable,
    NSViewMinXMargin, NSViewMaxYMargin,
    NSMutableAttributedString, NSAttributedString,
    NSFontAttributeName, NSForegroundColorAttributeName,
    NSVisualEffectView, NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectStateActive, NSVisualEffectMaterialHUDWindow,
)

# NSGlassEffectView is the macOS 26 "Liquid Glass" material. Absent on older
# systems, where the window falls back to NSVisualEffectView.
try:
    from AppKit import NSGlassEffectView
except ImportError:      # pragma: no cover - depends on the OS version
    NSGlassEffectView = None

SETTINGS_PATH = os.path.expanduser(
    "~/Library/Application Support/meeting-translator/overlay.json"
)

DEFAULTS = {
    "font_name": None,      # None = system font (PingFang for Chinese)
    "font_size": 24,
    "max_lines": 2,         # number of subtitle ENTRIES (each is 2 rendered rows)
    "bg_alpha": 0.6,
    "text_alpha": 1.0,
    "show_english": True,
    "x": None,              # None = horizontally centred
    "y": 120,
    "width": 900,
    "height": 170,          # allows for 2 bilingual entries plus the padding
}

# Fonts verified present on macOS. Chinese needs a CJK-capable family.
FONT_CHOICES = [
    (None, "System Default (PingFang)"),
    ("PingFang SC Semibold", "PingFang Semibold"),
    ("Heiti SC Medium", "Heiti"),
    ("Lantinghei SC Demibold", "Lantinghei"),
    ("Songti SC Bold", "Songti (Serif)"),
    ("Kaiti SC Bold", "Kaiti"),
    ("Yuanti SC Bold", "Yuanti (Rounded)"),
]
FONT_SIZE_CHOICES = [16, 18, 20, 24, 28, 32, 40, 48]
LINE_CHOICES = [1, 2, 3, 4, 5]
ALPHA_CHOICES = [(0.0, "Transparent"), (0.25, "25%"), (0.4, "40%"),
                 (0.6, "60%"), (0.8, "80%"), (1.0, "Opaque")]

MIN_WIDTH, MIN_HEIGHT = 240, 44
MAX_WIDTH, MAX_HEIGHT = 2400, 600
# Roomier padding than a plain box needs: the rounded corners eat into the
# usable area, and Liquid Glass reads better with generous margins.
PAD_X, PAD_Y = 26, 18
GRIP_SIZE = 18              # side length of the bottom-right resize handle
GRIP_INSET = 8              # keeps the grip clear of the rounded corner
REFRESH_SEC = 0.3

# macOS 26 rounded-rectangle geometry. 26pt matches the concentric radius Apple
# uses for floating panels of this size; the shape is a continuous curve
# (squircle), which NSGlassEffectView draws natively.
CORNER_RADIUS = 26.0

# The English original renders smaller and dimmer than the Chinese so the
# translation stays the primary line.
EN_SIZE_RATIO = 0.62
EN_ALPHA_RATIO = 0.6

# Only the tail of the subtitle file is read: it grows for the whole meeting and
# re-reading it in full would get slower and slower.
TAIL_BYTES = 8192


def _pid_alive(pid: int) -> bool:
    """True if `pid` still exists. Signal 0 only performs the existence check."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True     # exists but is owned by someone else
    return True


def load_settings() -> dict:
    """Read settings, tolerating a missing or corrupt file, and clamp ranges."""
    data = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            data.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass

    data["font_size"] = min(max(int(data["font_size"]), 10), 96)
    data["max_lines"] = min(max(int(data["max_lines"]), 1), 10)
    data["bg_alpha"] = min(max(float(data["bg_alpha"]), 0.0), 1.0)
    data["text_alpha"] = min(max(float(data["text_alpha"]), 0.2), 1.0)
    data["show_english"] = bool(data["show_english"])
    data["width"] = max(float(data["width"]), MIN_WIDTH)
    data["height"] = max(float(data["height"]), MIN_HEIGHT)
    return data


def save_settings(data: dict) -> None:
    """Write settings atomically so a crash mid-write cannot corrupt them."""
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(SETTINGS_PATH))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_PATH)
    except OSError as exc:
        print(f"Failed to save overlay settings: {exc}", file=sys.stderr)


class ResizeGrip(NSView):
    """Bottom-right drag handle. Resizes the window, pinning its bottom-left."""

    def initWithOwner_frame_(self, owner, frame):
        self = objc.super(ResizeGrip, self).initWithFrame_(frame)
        if self is None:
            return None
        self._owner = owner
        self._start_mouse = None
        self._start_frame = None
        return self

    # Must be False, or setMovableByWindowBackground_ steals the drag and the
    # window moves instead of resizing.
    def mouseDownCanMoveWindow(self):
        return False

    # A non-activating panel never becomes the key window, so the very first
    # click has to be accepted explicitly.
    def acceptsFirstMouse_(self, event):
        return True

    def resetCursorRects(self):
        self.addCursorRect_cursor_(self.bounds(), NSCursor.crosshairCursor())

    def drawRect_(self, rect):
        """Three diagonal strokes hinting that this corner is draggable."""
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.55).setStroke()
        width = self.bounds().size.width
        for offset in (4, 9, 14):
            path = NSBezierPath.bezierPath()
            path.setLineWidth_(1.5)
            path.moveToPoint_(NSMakePoint(width - offset, 3))
            path.lineToPoint_(NSMakePoint(width - 3, offset))
            path.stroke()

    def mouseDown_(self, event):
        win = self.window()
        self._start_frame = win.frame()
        self._start_mouse = win.convertPointToScreen_(event.locationInWindow())

    def mouseDragged_(self, event):
        if self._start_mouse is None:
            return
        win = self.window()
        now = win.convertPointToScreen_(event.locationInWindow())
        dx = now.x - self._start_mouse.x
        dy = now.y - self._start_mouse.y        # AppKit's +y points up

        frame = self._start_frame
        new_w = min(max(frame.size.width + dx, MIN_WIDTH), MAX_WIDTH)
        new_h = min(max(frame.size.height - dy, MIN_HEIGHT), MAX_HEIGHT)
        # origin is passed through unchanged, so the bottom-left corner stays
        # put and the window grows up and to the right.
        win.setFrame_display_(
            NSMakeRect(frame.origin.x, frame.origin.y, new_w, new_h), True
        )
        self._owner.layout_label()

    def mouseUp_(self, event):
        self._start_mouse = None
        self._owner.persist_frame()


class SubtitleView(NSView):
    """Content view inside the glass material; hosts the right-click menu."""

    def setOwner_(self, owner):
        self._owner = owner

    def acceptsFirstMouse_(self, event):
        return True

    # Dragging this view must still move the window: the material view sits
    # between it and the window, so movableByWindowBackground needs an explicit
    # opt-in from every view in the chain.
    def mouseDownCanMoveWindow(self):
        return True

    def rightMouseDown_(self, event):
        NSMenu.popUpContextMenu_withEvent_forView_(
            self._owner.build_menu(), event, self
        )


class OverlayApp:
    """The overlay window plus its file-polling refresh loop."""

    def __init__(
        self,
        subtitle_file: str,
        visible_flag: str | None,
        parent_pid: int | None = None,
    ):
        self.subtitle_file = subtitle_file
        self.visible_flag = visible_flag
        self.parent_pid = parent_pid
        self.settings = load_settings()

        self.app = NSApplication.sharedApplication()
        self.app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        s = self.settings
        screen = NSScreen.mainScreen().frame()
        x = s["x"] if s["x"] is not None else (screen.size.width - s["width"]) / 2

        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, s["y"], s["width"], s["height"]),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        self.panel.setLevel_(NSStatusWindowLevel)
        self.panel.setOpaque_(False)
        self.panel.setMovableByWindowBackground_(True)
        self.panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        self.panel.setContentMinSize_(NSMakeSize(MIN_WIDTH, MIN_HEIGHT))
        self.panel.setContentMaxSize_(NSMakeSize(MAX_WIDTH, MAX_HEIGHT))
        # The window itself draws nothing; the rounded material below is the
        # visible surface. Without a clear colour the square window background
        # would show through outside the corner radius.
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(True)

        full = NSMakeRect(0, 0, s["width"], s["height"])
        self.material = self._make_material(full)
        content = SubtitleView.alloc().initWithFrame_(full)
        content.setOwner_(self)
        content.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        # NSGlassEffectView hosts its content via setContentView_; the older
        # NSVisualEffectView is a plain superview.
        if self.material.respondsToSelector_("setContentView:"):
            self.material.setContentView_(content)
        else:
            self.material.addSubview_(content)
        self.panel.setContentView_(self.material)
        self.content = content

        self.label = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.label.setBezeled_(False)
        self.label.setDrawsBackground_(False)
        self.label.setEditable_(False)
        self.label.setSelectable_(False)
        self.label.cell().setWraps_(True)
        self.label.cell().setLineBreakMode_(NSLineBreakByWordWrapping)
        self.label.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        content.addSubview_(self.label)

        # Added after the label so it wins hit-testing in the corner. Inset from
        # the very edge so it sits inside the corner radius rather than under
        # the curve, where part of it would be clipped.
        self.grip = ResizeGrip.alloc().initWithOwner_frame_(
            self,
            NSMakeRect(
                s["width"] - GRIP_SIZE - GRIP_INSET, GRIP_INSET,
                GRIP_SIZE, GRIP_SIZE,
            ),
        )
        self.grip.setAutoresizingMask_(NSViewMinXMargin | NSViewMaxYMargin)
        content.addSubview_(self.grip)

        # Strong reference: a garbage-collected menu makes right-click do nothing.
        self._menu = None
        self._last_size = -1
        self._visible = False

        self.apply_appearance()

        # Start hidden when the server drives visibility, so launching c3po does
        # not throw a window over whatever the user is doing.
        if self.visible_flag is None:
            self._show()
        elif os.path.exists(self.visible_flag):
            self._show()

        self.timer = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                REFRESH_SEC, self, "tick:", None, True
            )
        )

    # ---------------- window material ----------------

    def _make_material(self, frame):
        """Build the rounded translucent backing view.

        Prefers NSGlassEffectView (macOS 26 Liquid Glass): it renders the
        continuous-curve rounded rectangle and the specular edge natively.
        Falls back to NSVisualEffectView with a manually rounded layer.
        """
        if NSGlassEffectView is not None:
            glass = NSGlassEffectView.alloc().initWithFrame_(frame)
            glass.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            if glass.respondsToSelector_("setCornerRadius:"):
                glass.setCornerRadius_(CORNER_RADIUS)
            self._apply_tint(glass)
            return glass

        effect = NSVisualEffectView.alloc().initWithFrame_(frame)
        effect.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        effect.setMaterial_(NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(NSVisualEffectStateActive)
        effect.setWantsLayer_(True)
        layer = effect.layer()
        layer.setCornerRadius_(CORNER_RADIUS)
        # Continuous curvature is the squircle Apple uses; the default
        # "circular" curve looks visibly tighter at this radius.
        try:
            layer.setCornerCurve_("continuous")
        except (AttributeError, ValueError):
            pass
        layer.setMasksToBounds_(True)
        return effect

    def _apply_tint(self, glass) -> None:
        """Tint the glass so white subtitles stay legible over bright video.

        bg_alpha keeps its meaning from the old solid-background window: 0 is a
        clear pane, 1 is a nearly opaque dark slab.
        """
        if not glass.respondsToSelector_("setTintColor:"):
            return
        alpha = self.settings["bg_alpha"]
        if alpha <= 0:
            glass.setTintColor_(None)
        else:
            glass.setTintColor_(
                NSColor.colorWithCalibratedWhite_alpha_(0.0, alpha)
            )

    # ---------------- visibility ----------------

    def _show(self) -> None:
        if not self._visible:
            self.panel.orderFrontRegardless()
            self._visible = True

    def _hide(self) -> None:
        if self._visible:
            self.panel.orderOut_(None)
            self._visible = False

    # ---------------- appearance ----------------

    def current_font(self, size: float | None = None):
        s = self.settings
        size = s["font_size"] if size is None else size
        if s["font_name"]:
            font = NSFont.fontWithName_size_(s["font_name"], size)
            if font is not None:
                return font
            print(
                f"Font '{s['font_name']}' not found; using the system font",
                file=sys.stderr,
            )
        return NSFont.boldSystemFontOfSize_(size)

    def apply_appearance(self) -> None:
        if NSGlassEffectView is not None:
            self._apply_tint(self.material)
        else:
            # The fallback material has no tint API; dim via layer opacity.
            self.material.setAlphaValue_(
                0.35 + 0.65 * self.settings["bg_alpha"]
            )
        self.layout_label()
        self.refresh_text(force=True)

    def layout_label(self) -> None:
        """Keep the text area filling the window, minus padding."""
        size = self.panel.frame().size
        self.label.setFrame_(
            NSMakeRect(
                PAD_X,
                PAD_Y,
                max(size.width - PAD_X * 2, 20),
                max(size.height - PAD_Y * 2, 20),
            )
        )

    def persist_frame(self) -> None:
        frame = self.panel.frame()
        self.settings.update(
            {
                "x": float(frame.origin.x),
                "y": float(frame.origin.y),
                "width": float(frame.size.width),
                "height": float(frame.size.height),
            }
        )
        save_settings(self.settings)

    # ---------------- right-click menu ----------------

    def build_menu(self):
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        s = self.settings

        def add_submenu(title, choices, current, action):
            item = NSMenuItem.alloc().init()
            item.setTitle_(title)
            sub = NSMenu.alloc().init()
            sub.setAutoenablesItems_(False)
            for value, label in choices:
                entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    label, action, ""
                )
                entry.setTarget_(self)
                entry.setRepresentedObject_(value)
                entry.setState_(NSOnState if value == current else NSOffState)
                sub.addItem_(entry)
            item.setSubmenu_(sub)
            menu.addItem_(item)

        add_submenu("Font", FONT_CHOICES, s["font_name"], "setFont:")
        add_submenu(
            "Font Size",
            [(v, f"{v} pt") for v in FONT_SIZE_CHOICES],
            s["font_size"],
            "setSize:",
        )
        add_submenu(
            "Lines",
            [(v, "1 entry" if v == 1 else f"{v} entries") for v in LINE_CHOICES],
            s["max_lines"],
            "setLines:",
        )
        add_submenu(
            "Background Opacity", ALPHA_CHOICES, s["bg_alpha"], "setBgAlpha:"
        )
        add_submenu(
            "Text Opacity",
            [(v, label) for v, label in ALPHA_CHOICES if v >= 0.2],
            s["text_alpha"],
            "setTextAlpha:",
        )

        menu.addItem_(NSMenuItem.separatorItem())
        english = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Show English Original", "toggleEnglish:", ""
        )
        english.setTarget_(self)
        english.setState_(NSOnState if s["show_english"] else NSOffState)
        menu.addItem_(english)

        menu.addItem_(NSMenuItem.separatorItem())
        for title, action in (
            ("Reset Size & Position", "resetFrame:"),
            ("Quit", "quit:"),
        ):
            entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, action, ""
            )
            entry.setTarget_(self)
            menu.addItem_(entry)

        self._menu = menu   # keep alive
        return menu

    def _update(self, key, value) -> None:
        self.settings[key] = value
        self.apply_appearance()
        save_settings(self.settings)

    def setFont_(self, sender):
        self._update("font_name", sender.representedObject())

    def setSize_(self, sender):
        self._update("font_size", int(sender.representedObject()))

    def setLines_(self, sender):
        self._update("max_lines", int(sender.representedObject()))

    def setBgAlpha_(self, sender):
        self._update("bg_alpha", float(sender.representedObject()))

    def setTextAlpha_(self, sender):
        self._update("text_alpha", float(sender.representedObject()))

    def toggleEnglish_(self, sender):
        self._update("show_english", not self.settings["show_english"])

    def resetFrame_(self, sender):
        screen = NSScreen.mainScreen().frame()
        width, height = DEFAULTS["width"], DEFAULTS["height"]
        self.panel.setFrame_display_(
            NSMakeRect(
                (screen.size.width - width) / 2, DEFAULTS["y"], width, height
            ),
            True,
        )
        self.layout_label()
        self.persist_frame()

    def quit_(self, sender):
        self.persist_frame()
        self.app.terminate_(None)

    # ---------------- subtitle refresh ----------------

    def _read_entries(self) -> list[tuple[str, str]]:
        """Return recent (english, chinese) pairs from the tail of the file."""
        try:
            with open(self.subtitle_file, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - TAIL_BYTES))
                chunk = fh.read()
        except OSError:
            return []

        text = chunk.decode("utf-8", errors="ignore")
        if size > TAIL_BYTES:
            # The first line is probably cut mid-record; drop it.
            text = text.split("\n", 1)[-1]

        entries = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            english, _, chinese = line.partition("\t")
            # Tolerate plain single-language lines from an older file format.
            entries.append((english.strip(), chinese.strip() or english.strip()))
        return entries

    def _build_attributed(self, entries) -> NSMutableAttributedString:
        """Render entries as Chinese lines, each optionally preceded by English."""
        s = self.settings
        zh_font = self.current_font()
        zh_color = NSColor.colorWithCalibratedWhite_alpha_(1.0, s["text_alpha"])
        en_font = self.current_font(max(s["font_size"] * EN_SIZE_RATIO, 9))
        en_color = NSColor.colorWithCalibratedWhite_alpha_(
            1.0, s["text_alpha"] * EN_ALPHA_RATIO
        )

        out = NSMutableAttributedString.alloc().init()

        def append(text, font, color):
            out.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_(
                    text,
                    {
                        NSFontAttributeName: font,
                        NSForegroundColorAttributeName: color,
                    },
                )
            )

        for index, (english, chinese) in enumerate(entries):
            if index:
                append("\n", zh_font, zh_color)
            if s["show_english"] and english and english != chinese:
                append(english + "\n", en_font, en_color)
            append(chinese, zh_font, zh_color)
        return out

    def refresh_text(self, force: bool = False) -> None:
        entries = self._read_entries()
        if entries:
            recent = entries[-self.settings["max_lines"]:]
            self.label.setAttributedStringValue_(self._build_attributed(recent))
        elif force:
            self.label.setAttributedStringValue_(
                self._build_attributed([("", "Waiting for subtitles…")])
            )

    def tick_(self, _timer):
        # Exit if the server that spawned us is gone. terminate() in the
        # server's shutdown path covers a clean stop, but a SIGKILL skips it and
        # would leave this window on screen with nothing feeding it.
        if self.parent_pid is not None and not _pid_alive(self.parent_pid):
            self.persist_frame()
            self.app.terminate_(None)
            return

        # Visibility is polled here rather than via a signal handler: POSIX
        # signals and the PyObjC run loop do not mix well.
        if self.visible_flag is not None:
            if os.path.exists(self.visible_flag):
                self._show()
            else:
                self._hide()
                return      # nothing to redraw while hidden

        try:
            size = os.path.getsize(self.subtitle_file)
        except OSError:
            return
        if size == self._last_size:
            return
        self._last_size = size
        self.refresh_text()

    def run(self) -> None:
        self.app.run()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subtitle-file",
        default="/tmp/c3po-subtitles.txt",
        help="file the translator appends 'english<TAB>chinese' lines to",
    )
    parser.add_argument(
        "--visible-flag",
        default=None,
        help="path whose existence means 'visible'; omit to always show",
    )
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=None,
        help="exit when this pid disappears, so a killed server leaves no orphan",
    )
    args = parser.parse_args()

    # Make sure the file exists so the first poll does not just fail.
    if not os.path.exists(args.subtitle_file):
        try:
            open(args.subtitle_file, "w").close()
        except OSError as exc:
            print(f"Cannot create {args.subtitle_file}: {exc}", file=sys.stderr)
            raise SystemExit(1)

    OverlayApp(args.subtitle_file, args.visible_flag, args.parent_pid).run()


if __name__ == "__main__":
    main()
