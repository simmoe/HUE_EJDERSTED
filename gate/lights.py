"""Gate window scenes. LED 1 and LED 55 meet at the bottom.

Cycles 1–3 share one slow “saber moon” animation. Peak brightness depends on
the cycle; MQTT level (low / medium / max) scales it further for battery life.
Mode 0 is off.
"""

import math
import time
from machine import Pin
from neopixel import NeoPixel

N = 55
SCENES = 3
FADE_MS = 1800
_BARE = 4.5
_TOP_SHADE = 16.0

mode = 0
mqtt_up = False
_level = "medium"
_prev = 0
_mode_at = 0

# Brown-gold night white (from the old night fly palette).
_NIGHT = (118, 30, 4)
_CYCLE_PEAK = {1: 0.28, 2: 0.38, 3: 0.50}
_LEVEL_SCALE = {"low": 0.45, "medium": 0.70, "max": 1.0}

I_TOP = N // 2
I_HORN_A = 16
I_HORN_B = 19

D_TOP = 10.0
D_GROW = 4.0
D_HOLD = 30.0
D_COLLAPSE = 5.0
D_DISC = 30.0
_PERIOD = D_TOP + D_GROW + D_HOLD + D_COLLAPSE + D_DISC

_arc_cache = None


def set_mode(n):
    global mode, _prev, _mode_at
    n = int(n)
    if n == 4:
        n = 0
    if n < 0 or n > SCENES or n == mode:
        return
    _prev = mode
    mode = n
    _mode_at = time.ticks_ms()


def set_level(name):
    global _level
    n = str(name).strip().lower()
    if n in _LEVEL_SCALE:
        _level = n


def _clip(v):
    if v < 0:
        return 0
    if v > 255:
        return 255
    return int(v)


def _smooth(t):
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    return t * t * (3 - 2 * t)


def _from_seam(i):
    return min(i + 0.5, N - (i + 0.5))


def _shade(i):
    d = _from_seam(i)
    if d <= _BARE:
        return 0.0
    if d >= _TOP_SHADE:
        return 1.0
    return _smooth((d - _BARE) / (_TOP_SHADE - _BARE))


def _path(i0, i1):
    if i0 == i1:
        return [i0]
    d_fwd = (i1 - i0) % N
    d_back = (i0 - i1) % N
    out = []
    if d_fwd <= d_back:
        i = i0
        while True:
            out.append(i)
            if i == i1:
                break
            i = (i + 1) % N
    else:
        i = i0
        while True:
            out.append(i)
            if i == i1:
                break
            i = (i - 1) % N
    return out


def _arc_indices():
    global _arc_cache
    if _arc_cache is None:
        a = _path(I_TOP, I_HORN_A)
        b = _path(I_TOP, I_HORN_B)
        seen = {}
        for seq in (a, b):
            for j, idx in enumerate(seq):
                if idx not in seen or j < seen[idx]:
                    seen[idx] = j
        _arc_cache = sorted(seen.keys(), key=lambda k: seen[k])
    return _arc_cache


def _peak_scale():
    base = _CYCLE_PEAK.get(mode, _CYCLE_PEAK[2])
    return base * _LEVEL_SCALE.get(_level, 0.7)


def _scale_rgb(rgb, level):
    r, g, b = rgb
    return (_clip(r * level), _clip(g * level), _clip(b * level))


def _top_breath(phase_u, peak):
    """Single top LED, 5 s in and 5 s out."""
    u = phase_u / D_TOP
    env = math.sin(math.pi * u)
    for i in range(N):
        buf[i] = (0, 0, 0)
    if env <= 0:
        return
    buf[I_TOP] = _scale_rgb(_NIGHT, peak * env)


def _arc_strength(idx, grow_k, collapse_k):
    arc = _arc_indices()
    if idx not in arc:
        return 0.0
    pos = arc.index(idx)
    span = max(len(arc) - 1, 1)
    along = pos / span
    top_w = 1.0 - 0.55 * along
    if grow_k < 1.0:
        if along > grow_k:
            return 0.0
        edge = 1.0 - _smooth((along - grow_k * 0.85) / max(0.15, 1.0 - grow_k * 0.85)) if along > grow_k * 0.85 else 1.0
        return top_w * edge * grow_k
    if collapse_k < 1.0:
        if along > collapse_k:
            return 0.0
        return top_w * _smooth(collapse_k - along) / max(collapse_k, 0.001)
    return top_w


def _draw_arc(buf, peak, grow_k=1.0, collapse_k=1.0):
    for i in range(N):
        s = _shade(i)
        if s <= 0:
            buf[i] = (0, 0, 0)
            continue
        a = _arc_strength(i, grow_k, collapse_k)
        if a <= 0:
            buf[i] = (0, 0, 0)
            continue
        buf[i] = _scale_rgb(_NIGHT, peak * s * a)


def _disc_breath(phase_u, peak):
    u = phase_u / D_DISC
    env = 0.15 * (0.5 - 0.5 * math.cos(2 * math.pi * u))
    for i in range(N):
        s = _shade(i)
        if s <= 0:
            buf[i] = (0, 0, 0)
            continue
        buf[i] = _scale_rgb(_NIGHT, peak * s * env)


def _saber(t, buf):
    peak = _peak_scale()
    if peak <= 0 or mode not in _CYCLE_PEAK:
        _off(buf)
        return
    phase = t % _PERIOD
    if phase < D_TOP:
        _top_breath(phase, peak)
    elif phase < D_TOP + D_GROW:
        k = _smooth((phase - D_TOP) / D_GROW)
        _draw_arc(buf, peak, grow_k=k, collapse_k=1.0)
    elif phase < D_TOP + D_GROW + D_HOLD:
        _draw_arc(buf, peak, grow_k=1.0, collapse_k=1.0)
    elif phase < D_TOP + D_GROW + D_HOLD + D_COLLAPSE:
        k = 1.0 - _smooth((phase - D_TOP - D_GROW - D_HOLD) / D_COLLAPSE)
        _draw_arc(buf, peak, grow_k=1.0, collapse_k=k)
    else:
        _disc_breath(phase - D_TOP - D_GROW - D_HOLD - D_COLLAPSE, peak)


def _off(buf):
    for i in range(N):
        buf[i] = (0, 0, 0)


def _render(which, t, buf):
    if which in _CYCLE_PEAK:
        _saber(t, buf)
    else:
        _off(buf)


def _mix(a, b, k):
    return (
        _clip(a[0] + (b[0] - a[0]) * k),
        _clip(a[1] + (b[1] - a[1]) * k),
        _clip(a[2] + (b[2] - a[2]) * k),
    )


def _loop():
    global _mode_at
    Pin(19, Pin.OUT).value(1)
    ring = NeoPixel(Pin(2), N)
    cur = [(0, 0, 0)] * N
    old = [(0, 0, 0)] * N
    _mode_at = time.ticks_ms()
    while True:
        now = time.ticks_ms()
        t = now / 1000
        _render(mode, t, cur)
        elapsed = time.ticks_diff(now, _mode_at)
        if _prev != mode and 0 <= elapsed < FADE_MS:
            _render(_prev, t, old)
            k = _smooth(elapsed / FADE_MS)
            for i in range(N):
                ring[i] = _mix(old[i], cur[i], k)
        else:
            for i in range(N):
                ring[i] = cur[i]
        ring.write()
        time.sleep_ms(40)


def start():
    import _thread

    _thread.start_new_thread(_loop, ())
