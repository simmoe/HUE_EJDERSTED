"""DLNA/UPnP-controller til BeoPlay M5.

M5 eksponerer en standard DLNA Media Renderer (`urn:schemas-upnp-org:device:MediaRenderer:1`)
som vi kan styre via SOAP — hjælpsomt til at afspille arbitrary audio-URLs (fx SR-streams)
direkte uden Spotify-mellemled.

Discovery sker via SSDP M-SEARCH ved første brug og caches; AVTransport-portens nummer
er dynamisk og kan skifte ved reboot, så vi rediscoverer ved fejl.
"""
import asyncio
import re
import socket
import urllib.request

import httpx

from bo_link import BEO_M5_IP

SSDP_TIMEOUT = 4.0
_avt_url: str | None = None
_avt_service: str | None = None
_avt_lock = asyncio.Lock()
_http = httpx.AsyncClient(timeout=10.0)


def _local_ip_for(target_ip: str) -> str:
    """Find den lokale IP kernen ville bruge til at nå target_ip.
    Vigtigt på Pi'en: Tailscale-interfacet (100.x.x.x) vinder default-routing,
    og SSDP-multicast sendt fra det interface bliver aldrig set af M5'en.
    Vi binder derfor eksplicit til LAN-IP'en."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP-connect sender intet, men beder kernen vælge kilde-IP
        s.connect((target_ip, 1))
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        s.close()


def _ssdp_discover_m5() -> tuple[str, str] | None:
    """Synchron SSDP M-SEARCH efter M5'ens AVTransport-endpoint.
    Returnerer (control_url, service_type) eller None."""
    msearch = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 3\r\n"
        "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
        "\r\n"
    ).encode()
    local_ip = _local_ip_for(BEO_M5_IP)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
        if local_ip:
            # Tving multicast ud ad LAN-interface (ikke fx Tailscale)
            s.setsockopt(
                socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip)
            )
            try:
                s.bind((local_ip, 0))
            except OSError as e:
                print(f"[DLNA] bind to {local_ip} failed: {e}")
        s.settimeout(SSDP_TIMEOUT)
        s.sendto(msearch, ("239.255.255.250", 1900))
        desc_url: str | None = None
        try:
            while True:
                data, addr = s.recvfrom(2048)
                if addr[0] != BEO_M5_IP:
                    continue
                for line in data.decode(errors="ignore").split("\r\n"):
                    if line.lower().startswith("location:"):
                        desc_url = line.split(":", 1)[1].strip()
                        break
                if desc_url:
                    break
        except socket.timeout:
            return None
    finally:
        s.close()

    if not desc_url:
        return None

    try:
        with urllib.request.urlopen(desc_url, timeout=4) as r:
            xml = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[DLNA] description fetch error: {e}")
        return None

    base = desc_url.rsplit("/", 1)[0]
    for svc in re.findall(r"<service>(.*?)</service>", xml, flags=re.DOTALL):
        if "AVTransport" not in svc:
            continue
        cu = re.search(r"<controlURL>([^<]+)</controlURL>", svc)
        st = re.search(r"<serviceType>([^<]+)</serviceType>", svc)
        if not (cu and st):
            continue
        control = cu.group(1)
        if not control.startswith("http"):
            if control.startswith("/"):
                m = re.match(r"(https?://[^/]+)", desc_url)
                if m:
                    control = m.group(1) + control
            else:
                control = base + "/" + control
        return control, st.group(1)
    return None


async def _get_avtransport(force: bool = False) -> tuple[str, str] | None:
    global _avt_url, _avt_service
    async with _avt_lock:
        if force:
            _avt_url = None
            _avt_service = None
        if _avt_url and _avt_service:
            return _avt_url, _avt_service
        result = await asyncio.to_thread(_ssdp_discover_m5)
        if result:
            _avt_url, _avt_service = result
            return result
    return None


async def _soap(action: str, body_xml: str, retried: bool = False) -> tuple[bool, str]:
    """Send SOAP-kommando til M5'ens AVTransport-service.
    Hvis det fejler (fx fordi porten har skiftet), rediscoverer vi én gang."""
    found = await _get_avtransport(force=retried)
    if not found:
        return False, "no AVTransport endpoint found via SSDP"
    control_url, service_type = found
    envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:{action} xmlns:u="{service_type}">
      {body_xml}
    </u:{action}>
  </s:Body>
</s:Envelope>"""
    try:
        r = await _http.post(
            control_url,
            content=envelope,
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPAction": f'"{service_type}#{action}"',
            },
        )
        if r.status_code == 200:
            return True, ""
        if not retried:
            print(f"[DLNA] {action} HTTP {r.status_code}, rediscovering …")
            return await _soap(action, body_xml, retried=True)
        return False, f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        if not retried:
            print(f"[DLNA] {action} error ({e}), rediscovering …")
            return await _soap(action, body_xml, retried=True)
        return False, str(e)


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _didl_for(url: str, title: str) -> str:
    """Byg DIDL-Lite metadata til CurrentURIMetaData. Hele blokken bliver
    XML-escaped når den lægges ind i SOAP-body'et."""
    title_esc = _xml_escape(title or "")
    url_esc = _xml_escape(url)
    didl = (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="1" parentID="0" restricted="1">'
        f'<dc:title>{title_esc}</dc:title>'
        '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
        f'<res protocolInfo="http-get:*:audio/mp4:*">{url_esc}</res>'
        "</item>"
        "</DIDL-Lite>"
    )
    return _xml_escape(didl)


async def play_url(url: str, title: str = "") -> tuple[bool, str]:
    """Afspil arbitrary audio-URL på M5 via DLNA AVTransport."""
    if not url:
        return False, "no url"
    didl = _didl_for(url, title)
    url_esc = _xml_escape(url)
    ok, detail = await _soap(
        "SetAVTransportURI",
        f"<InstanceID>0</InstanceID><CurrentURI>{url_esc}</CurrentURI><CurrentURIMetaData>{didl}</CurrentURIMetaData>",
    )
    if not ok:
        return False, f"SetAVTransportURI: {detail}"
    ok, detail = await _soap(
        "Play",
        "<InstanceID>0</InstanceID><Speed>1</Speed>",
    )
    if not ok:
        return False, f"Play: {detail}"
    return True, ""


async def stop() -> tuple[bool, str]:
    return await _soap("Stop", "<InstanceID>0</InstanceID>")


async def pause() -> tuple[bool, str]:
    return await _soap("Pause", "<InstanceID>0</InstanceID>")


async def resume() -> tuple[bool, str]:
    return await _soap("Play", "<InstanceID>0</InstanceID><Speed>1</Speed>")
