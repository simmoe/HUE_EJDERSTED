"""Scenes for the circular gate window. LED 1 and LED 54 meet at the bottom."""

import math
import random
import time
from machine import Pin
from neopixel import NeoPixel

N = 54
SCENES = 4
FADE_MS = 1800
mode = 1
mqtt_up = False
_prev = 1
_mode_at = 0
_flies = None

# Ten warm, light colours. These values are the fade's maximum.
_COLOURS = (
    (255, 176, 72),
    (255, 206, 156),
    (255, 148, 64),
    (255, 188, 132),
    (255, 214, 128),
    (255, 168, 146),
    (255, 154, 108),
    (255, 196, 164),
    (255, 132, 86),
    (255, 184, 96),
)
_FLIES = (
    (200, 150, 48),
    (220, 110, 32),
    (180, 160, 80),
    (210, 96, 28),
    (160, 120, 40),
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


def _height(i, drift):
    theta = 2 * math.pi * (i + 0.5) / N
    h = (1 - math.cos(theta)) / 2 + drift
    if h < 0:
        return 0.0, theta
    if h > 1:
        return 1.0, theta
    return h, theta


def _sunrise(t, buf):
    # Slow, wide swings. The bottom embers and the top blue breathe; the middle stays dark.
    breath = 0.5 + 0.5 * math.sin(t * 0.20)
    drift = 0.05 * math.sin(t * 0.09)
    for i in range(N):
        h, theta = _height(i, drift)
        warm = 1 - _step(0.02, 0.46, h)
        blue = _step(0.60, 1.0, h)
        ember = 0.5 + 0.5 * math.sin(t * 0.16 + theta * 2)
        ring = 0.5 + 0.5 * math.sin(t * 0.33 + theta * 2)
        lift = 0.5 + 0.5 * math.sin(t * 0.11 + h * 4)
        level = 0.08 + 0.92 * (breath + ring + lift) / 3
        wr, wg, wb = 255, 40 + 150 * ember, 8 + 16 * ember
        br, bg, bb = 40, 86, 156
        buf[i] = (
            _clip((wr * warm + br * blue) * level),
            _clip((wg * warm + bg * blue) * level),
            _clip((wb * warm + bb * blue) * level),
        )


def _spawn(fly, now):
    fly["pos"] = random.random() * N
    fly["speed"] = random.choice((-1, 1)) * (0.6 + random.random() * 6.5)
    fly["life"] = 5.0 + random.random() * 6.0
    fly["born"] = now
    fly["rgb"] = _FLIES[random.randrange(len(_FLIES))]


def _night(t, buf):
    global _flies
    if _flies is None:
        _flies = []
        for n in range(5):
            fly = {}
            _spawn(fly, t - n * 1.8)
            _flies.append(fly)
    acc = [[0, 0, 0] for _ in range(N)]
    for fly in _flies:
        age = t - fly["born"]
        if age >= fly["life"]:
            _spawn(fly, t)
            age = 0.0
        env = math.sin(math.pi * age / fly["life"])
        env = env * env
        pos = fly["pos"] + fly["speed"] * age
        direction = -1 if fly["speed"] >= 0 else 1
        r, g, b = fly["rgb"]
        for k, w in enumerate((1.0, 0.42, 0.16, 0.05)):
            p = pos + direction * k
            i = int(math.floor(p)) % N
            gain = env * w
            acc[i][0] += r * gain
            acc[i][1] += g * gain
            acc[i][2] += b * gain
    for i in range(N):
        buf[i] = (_clip(acc[i][0]), _clip(acc[i][1]), _clip(acc[i][2]))


def _solids(t, buf):
    span = 10.0 * len(_COLOURS)
    cycle = t % span
    idx = int(cycle / 10.0) % len(_COLOURS)
    u = (cycle % 10.0) / 10.0
    # Rise for most of the ten seconds, then ease back to black so the next colour starts dark.
    if u < 0.72:
        k = _step(0.0, 0.72, u)
    else:
        k = 1 - _step(0.72, 1.0, u)
    col = _COLOURS[idx]
    rgb = (_clip(col[0] * k), _clip(col[1] * k), _clip(col[2] * k))
    for i in range(N):
        buf[i] = rgb


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
