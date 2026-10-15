"""Subscribe to have_laage. Payload 1–3 is a cycle; 0 (or legacy 4) is off.

Presence is MQTT last-will plus a retained heartbeat on have_laage/status.
The cycle itself lives in Firestore on the hub — this board only renders
the last command it was given.
"""

import time

import lights

_TOPIC = b"have_laage"
_STATUS = b"have_laage/status"
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
            client.set_last_will(_STATUS, b"offline", retain=True)
            client.set_callback(_on)
            client.connect()
            client.subscribe(_TOPIC)
            client.publish(_STATUS, b"online", retain=True)
            lights.mqtt_up = True
            last = time.ticks_ms()
            while True:
                client.check_msg()
                if time.ticks_diff(time.ticks_ms(), last) > 15000:
                    client.ping()
                    client.publish(_STATUS, b"online", retain=True)
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
