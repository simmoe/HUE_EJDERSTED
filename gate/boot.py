import time
from machine import Pin
from neopixel import NeoPixel
import network
import webrepl
import wifi

pwr = Pin(19, Pin.OUT)
pwr.value(1)
dot = NeoPixel(Pin(20), 1)


def show(rgb):
    dot[0] = rgb
    dot.write()


show((40, 10, 0))
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
try:
    wlan.config(hostname="laage")
except Exception:
    pass
if not wlan.isconnected():
    wlan.connect(wifi.SSID, wifi.PASSWORD)
    for _ in range(50):
        if wlan.isconnected():
            break
        time.sleep_ms(200)
if wlan.isconnected():
    show((0, 40, 0))
    print("wifi", wlan.ifconfig()[0])
else:
    show((40, 0, 0))
    print("wifi fail")
webrepl.start()
import netprog
netprog.start()
import mqtt_laage
mqtt_laage.start()
import lights
lights.start()
print("webrepl :8266 netprog :9011 mqtt have_laage")
