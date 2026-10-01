"""Subscribe to have_laage and switch the scene. Payload is 1, 2, or 3."""

import time

import lights

_TOPIC = b"have_laage"
_HOST = "mqtt.nextservices.dk"


def _on(_topic, msg):
    try:
        n = int(msg.decode().strip())
    except Exception:
        return
    lights.set_mode(n)


def _run():
    from umqtt.simple import MQTTClient

    while True:
        client = None
        try:
            client = MQTTClient("laage-have", _HOST, port=1883, keepalive=30)
            client.set_callback(_on)
            client.connect()
            client.subscribe(_TOPIC)
            lights.mqtt_up = True
            last = time.ticks_ms()
            while True:
                client.check_msg()
                if time.ticks_diff(time.ticks_ms(), last) > 15000:
                    client.ping()
                    last = time.ticks_ms()
                time.sleep_ms(200)
        except Exception as exc:
            lights.mqtt_up = False
            print("mqtt", exc)
            time.sleep(5)
        finally:
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass


def start():
    import _thread

    _thread.start_new_thread(_run, ())
