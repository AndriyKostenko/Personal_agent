"""One-time helper: prints the Google OAuth refresh token for the booking feature.

Prerequisite: a Google Cloud project with the Calendar API enabled and an OAuth client of type
"Desktop app". Put GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET into backend/.env, then run from
backend/:  python utils/google_consent.py
Open the printed URL, approve access, and copy the printed GOOGLE_REFRESH_TOKEN into .env.
"""

import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.settings import settings  # noqa: E402

PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{PORT}"
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
]


class _Handler(BaseHTTPRequestHandler):
    code: str | None = None

    def do_GET(self):
        _Handler.code = parse_qs(urlparse(self.path).query).get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Done. You can close this tab and return to the terminal.")

    def log_message(self, *args):
        pass


def main():
    if not (settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET):
        sys.exit("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in backend/.env first.")

    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
        {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",  # forces a refresh token even if access was granted before
        }
    )
    print(f"Open this URL and approve access:\n\n{url}\n")

    HTTPServer(("127.0.0.1", PORT), _Handler).handle_request()
    if not _Handler.code:
        sys.exit("No authorization code received.")

    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": _Handler.code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )
    response.raise_for_status()
    refresh_token = response.json().get("refresh_token")
    if not refresh_token:
        sys.exit("Google returned no refresh token. Revoke the app's access and retry.")
    print(f"Add this to backend/.env:\n\nGOOGLE_REFRESH_TOKEN={refresh_token}")


if __name__ == "__main__":
    main()
