"""Green edge glow that washes over the screen and fades — the deep-breath reward.

A borderless, click-through, all-spaces window above everything; it never takes
focus, so whatever you're doing is untouched. Must run on the main thread —
show() is safe to call from any thread.
"""
import AppKit
from PyObjCTools import AppHelper

_active = []  # keep refs so in-flight windows aren't garbage-collected

GLOW_RGBA = (0.24, 0.72, 0.45, 0.55)   # green — deep breath caught
MISS_RGBA = (0.84, 0.32, 0.25, 0.50)   # red — window ended without one
EDGE_DEPTH = 140.0
FADE_IN = 0.15
HOLD = 0.35


class _GlowView(AppKit.NSView):
    def drawRect_(self, rect):
        b = self.bounds()
        w, h = b.size.width, b.size.height
        depth = min(EDGE_DEPTH, h / 4.0)
        rgba = getattr(self, "glow_rgba", GLOW_RGBA)
        glow = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgba)
        clear = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            rgba[0], rgba[1], rgba[2], 0.0)
        grad = AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(glow, clear)
        grad.drawInRect_angle_(((0, 0), (w, depth)), 90.0)
        grad.drawInRect_angle_(((0, h - depth), (w, depth)), 270.0)
        grad.drawInRect_angle_(((0, 0), (depth, h)), 0.0)
        grad.drawInRect_angle_(((w - depth, 0), (depth, h)), 180.0)


def show(duration=1.2, color=GLOW_RGBA):
    """Flash the glow once. Thread-safe."""
    AppHelper.callAfter(_show_on_main, duration, color)


def _show_on_main(duration, color=GLOW_RGBA):
    screen = AppKit.NSScreen.mainScreen()
    if screen is None:
        return
    frame = screen.frame()
    win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame, AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered, False)
    win.setOpaque_(False)
    win.setBackgroundColor_(AppKit.NSColor.clearColor())
    win.setLevel_(AppKit.NSScreenSaverWindowLevel)
    win.setIgnoresMouseEvents_(True)
    win.setHasShadow_(False)
    win.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
    view = _GlowView.alloc().initWithFrame_(((0, 0), frame.size))
    view.glow_rgba = color
    win.setContentView_(view)
    win.setAlphaValue_(0.0)
    win.orderFrontRegardless()
    _active.append(win)

    AppKit.NSAnimationContext.beginGrouping()
    AppKit.NSAnimationContext.currentContext().setDuration_(FADE_IN)
    win.animator().setAlphaValue_(1.0)
    AppKit.NSAnimationContext.endGrouping()

    def fade_out():
        AppKit.NSAnimationContext.beginGrouping()
        AppKit.NSAnimationContext.currentContext().setDuration_(duration)
        win.animator().setAlphaValue_(0.0)
        AppKit.NSAnimationContext.endGrouping()

        def close():
            win.orderOut_(None)
            if win in _active:
                _active.remove(win)

        AppHelper.callLater(duration + 0.2, close)

    AppHelper.callLater(FADE_IN + HOLD, fade_out)
