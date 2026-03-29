"""Exchange a Spotify auth code for tokens (one-time use)."""
import json, time, ssl, certifi
import urllib.parse, urllib.request
from pathlib import Path

ctx = ssl.create_default_context(cafile=certifi.where())

CONFIG = Path(__file__).parent / "spotify_config.json"
cfg = json.loads(CONFIG.read_text())

CODE = "AQCIrWqn21_4K3otkoGQOvmU8M34QI_c2PbR8um9cX5lPqOIZ0M2QuXSU51Hx0CbdM1lb9KS7LwAJ4fJzDo-3gNjMjTECIw95OHW3nw3IKSDX3olp5EnsDxJwqORE4MN3tGvr94VHpxJ9sOEBRHUOkyZh3SYBzpcIQQ_kQhG1ZioNuEzwI-iIG6D5tO17LL9XhRIXwd6c67SAsR1WdefFhfrZxlKvbLqaT_HwjxNH_SnipxaUkjcpvJqAHcvTs91CkQbBcMNOdPid9ZuP5FoIhMuLwNi"

data = urllib.parse.urlencode({
    "grant_type": "authorization_code",
    "code": CODE,
    "redirect_uri": "http://127.0.0.1:8888/callback",
    "client_id": cfg["client_id"],
    "client_secret": cfg["client_secret"],
}).encode()

req = urllib.request.Request(
    "https://accounts.spotify.com/api/token",
    data=data,
    headers={"Content-Type": "application/x-www-form-urlencoded"},
)
try:
    resp = urllib.request.urlopen(req, context=ctx)
    tokens = json.loads(resp.read())
    cfg["access_token"] = tokens["access_token"]
    cfg["refresh_token"] = tokens["refresh_token"]
    cfg["expires_at"] = int(time.time()) + tokens["expires_in"]
    cfg["redirect_uri"] = "http://127.0.0.1:8888/callback"
    CONFIG.write_text(json.dumps(cfg, indent=2))
    print("SUCCESS - tokens saved!")
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()}")
except Exception as e:
    print(f"Error: {e}")
