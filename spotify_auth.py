"""One-time Spotify token fetcher.

1. Add  http://localhost:8888/callback  to your Spotify Dashboard Redirect URIs
2. Run: python3.13 spotify_auth.py
3. Browser opens → log in → tokens saved to spotify_config.json
"""

import json, webbrowser, urllib.parse, ssl, certifi
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import urllib.request

SSL_CTX = ssl.create_default_context(cafile=certifi.where())

CONFIG = Path(__file__).parent / "spotify_config.json"
REDIRECT = "http://127.0.0.1:8888/callback"
cfg = json.loads(CONFIG.read_text())

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = qs.get("code", [None])[0]
        if not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"No code received")
            return

        # Exchange code for tokens
        data = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
        }).encode()
        req = urllib.request.Request(
            "https://accounts.spotify.com/api/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        import time
        resp = urllib.request.urlopen(req, context=SSL_CTX)
        tokens = json.loads(resp.read())

        cfg["access_token"] = tokens["access_token"]
        cfg["refresh_token"] = tokens["refresh_token"]
        cfg["expires_at"] = int(time.time()) + tokens["expires_in"]
        cfg["redirect_uri"] = REDIRECT
        CONFIG.write_text(json.dumps(cfg, indent=2))

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h1>Done! Tokens saved. You can close this tab.</h1>")
        print("\n\033[32mTokens saved to spotify_config.json!\033[0m")

        # Shut down after response
        import threading
        threading.Thread(target=self.server.shutdown).start()

    def log_message(self, *a): pass

params = urllib.parse.urlencode({
    "client_id": cfg["client_id"],
    "response_type": "code",
    "redirect_uri": REDIRECT,
    "scope": "streaming user-read-email user-read-playback-state user-modify-playback-state user-read-currently-playing",
})
url = f"https://accounts.spotify.com/authorize?{params}"

print(f"Opening browser for Spotify login...")
print(f"Redirect URI: {REDIRECT}")
print(f"(Add this EXACT URI to your Spotify Dashboard if not already done)\n")
webbrowser.open(url)

HTTPServer(("127.0.0.1", 8888), Handler).serve_forever()
