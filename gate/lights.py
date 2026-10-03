"""Scenes for the circular gate window. LED 1 and LED 55 meet at the bottom.

The bottom ten LEDs are bare bulbs, five on each side of that seam. They stay
off. Above them the light fades up both sides and is full across the top.
Colours stay dark enough that the hue survives the LED.
"""

import math
import random
import time
from machine import Pin
from neopixel import NeoPixel

N = 55
SCENES = 4
FADE_MS = 1800
# Distance from the bottom seam, in LED steps. At or below this, the bulb is visible.
_BARE = 4.5
# Above this the top is at full strength.
_TOP = 16.0
mode = 1
mqtt_up = False
_prev = 1
_mode_at = 0
_fly = None

# Saturated, and well below white. The top of the window shows these at full.
_COLOURS = (
    (140, 22, 4),
    (110, 14, 6),
    (96, 18, 2),
    (150, 36, 8),
    (88, 12, 10),
    (124, 28, 6),
    (72, 16, 4),
    (132, 20, 8),
    (100, 24, 16),
    (118, 30, 4),
)
_FLIES = (
    (90, 28, 0),
    (70, 16, 0),
    (80, 34, 2),
    (60, 14, 0),
)


def set_mode(n):
    global mode, _prev, _mode_at
    n = int(n)
    if n < 1 or n > SCENES or n == mode:
        return
    _prev = mode
    mode = n
    _mode_at = time.ticks_ms()


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


def _step(edge0, edge1, x):
    if edge1 == edge0:
        return 0.0
    return _smooth((x - edge0) / (edge1 - edge0))


def _from_seam(i):
    return min(i + 0.5, N - (i + 0.5))


def _shade(i):
    """0 on the bare bulbs, 1 across the top, a smooth fall down both sides."""
    d = _from_seam(i)
    if d <= _BARE:
        return 0.0
    if d >= _TOP:
        return 1.0
    return _smooth((d - _BARE) / (_TOP - _BARE))


def _height(i):
    theta = 2 * math.pi * (i + 0.5) / N
    return (1 - math.cos(theta)) / 2, theta


def _sunrise(t, buf):
    # Red stays red, blue stays blue. Slow waves, never pushed up into white.
    breath = 0.55 + 0.45 * math.sin(t * 0.18)
    for i in range(N):
        shade = _shade(i)
        if shade <= 0:
            buf[i] = (0, 0, 0)
            continue
        h, theta = _height(i)
        warm = 1 - _step(0.18, 0.52, h)
        blue = _step(0.48, 0.92, h)
        ember = 0.5 + 0.5 * math.sin(t * 0.15 + theta * 1.4)
        ring = 0.5 + 0.5 * math.sin(t * 0.27 + theta * 2)
        level = (0.35 + 0.65 * breath) * (0.55 + 0.45 * ring) * shade
        # The red they liked, without the bright yellow wash. Blue kept deep.
        wr, wg, wb = 190, 14 + 18 * ember, 0
        br, bg, bb = 16, 36, 150
        buf[i] = (
            _clip((wr * warm + br * blue) * level),
            _clip((wg * warm + bg * blue) * level),
            _clip((wb * warm + bb * blue) * level),
        )


def _lit_index():
    while True:
        i = random.randrange(N)
        if _from_seam(i) > _BARE + 1:
            return i


def _night(t, buf):
    # One lamp. It fades in and out, may step a couple of places, then the ring is black.
    global _fly
    for i in range(N):
        buf[i] = (0, 0, 0)
    if _fly is None:
        _fly = {"alive": False, "until": t + 2.0}
    if not _fly["alive"]:
        if t < _fly["until"]:
            return
        direction = random.choice((-1, 1))
        _fly = {
            "alive": True,
            "born": t,
            "life": 9.0 + random.random() * 7.0,
            "pos": _lit_index(),
            "speed": direction * random.choice((0.0, 0.12, 0.18, 0.25)),
            "rgb": _FLIES[random.randrange(len(_FLIES))],
        }
    age = t - _fly["born"]
    if age >= _fly["life"]:
        _fly = {"alive": False, "until": t + 4.0 + random.random() * 8.0}
        return
    env = math.sin(math.pi * age / _fly["life"])
    pos = _fly["pos"] + _fly["speed"] * age
    i = int(round(pos)) % N
    if _from_seam(i) <= _BARE:
        return
    r, g, b = _fly["rgb"]
    buf[i] = (_clip(r * env), _clip(g * env), _clip(b * env))


def _solids(t, buf):
    span = 10.0 * len(_COLOURS)
    cycle = t % span
    idx = int(cycle / 10.0) % len(_COLOURS)
    u = (cycle % 10.0) / 10.0
    if u < 0.72:
        k = _step(0.0, 0.72, u)
    else:
        k = 1 - _step(0.72, 1.0, u)
    col = _COLOURS[idx]
    for i in range(N):
        shade = _shade(i) * k
        buf[i] = (_clip(col[0] * shade), _clip(col[1] * shade), _clip(col[2] * shade))


def _off(buf):
    for i in range(N):
        buf[i] = (0, 0, 0)


def _render(which, t, buf):
    if which == 2:
        _night(t, buf)
    elif which == 3:
        _solids(t, buf)
    elif which == 4:
        _off(buf)
    else:
        _sunrise(t, buf)


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
