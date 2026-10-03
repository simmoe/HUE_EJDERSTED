"""Tiny HTTP so we can push files and exec over Alohomora."""

import _thread
import os
import socket

PORT = 9011


def _read_http(cl):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = cl.recv(1024)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 8192:
            break
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length", "0") or 0)
    while len(rest) < n:
        rest += cl.recv(1024)
    return method, path, rest[:n]


def _send(cl, code, body, ctype="text/plain"):
    if isinstance(body, str):
        body = body.encode()
    cl.send(b"HTTP/1.0 %d OK\r\n" % code if code == 200 else b"HTTP/1.0 %d ERR\r\n" % code)
    cl.send(b"Content-Type: %s\r\n" % ctype.encode())
    cl.send(b"Content-Length: %d\r\n\r\n" % len(body))
    cl.send(body)


def _handle(cl):
    try:
        method, path, body = _read_http(cl)
        if method == "GET" and path == "/":
            import network

            ip = network.WLAN(network.STA_IF).ifconfig()[0]
            try:
                import lights

                mode = lights.mode
                mqtt = 1 if lights.mqtt_up else 0
            except Exception:
                mode = 0
                mqtt = 0
            _send(cl, 200, "laage %s mode %s mqtt %s\n" % (ip, mode, mqtt))
            return
        if method == "GET" and path == "/ls":
            _send(cl, 200, "\n".join(os.listdir()) + "\n")
            return
        if method == "POST" and path == "/exec":
            ns = {}
            exec(body, ns)
            _send(cl, 200, str(ns.get("out", "ok")) + "\n")
            return
        if method == "PUT" and path.startswith("/") and ".." not in path:
            name = path[1:] or "main.py"
            with open(name, "wb") as fh:
                fh.write(body)
            _send(cl, 200, "wrote %s %d\n" % (name, len(body)))
            return
        _send(cl, 404, "no\n")
    except Exception as exc:
        try:
            _send(cl, 500, str(exc) + "\n")
        except Exception:
            pass
    finally:
        try:
            cl.close()
        except Exception:
            pass


def _loop():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", PORT))
    s.listen(2)
    print("netprog", PORT)
    while True:
        cl, _addr = s.accept()
        _handle(cl)


def start():
    _thread.start_new_thread(_loop, ())
