"""One-time Gmail OAuth consent for kartik.pillai@lh2.ai — read-only scope.

Prereq: client_secret.json (Desktop-app OAuth client) in the project root.
Run:    python opsdata/gmail_auth.py
A browser opens; sign in AS KARTIK, approve. The refresh token lands in
.gmail_token.json (gitignored) and never needs re-consent unless revoked.

Stdlib-only on purpose: the whole flow is one auth URL, one localhost redirect,
one token POST — no google-* packages to install or keep patched.
"""
import base64, hashlib, http.server, json, os, secrets, socket, sys, threading
import urllib.parse, urllib.request, webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT_SECRET = os.path.join(ROOT, "client_secret.json")
TOKEN_FILE = os.path.join(ROOT, ".gmail_token.json")
# read-only: this project only ever pulls mail; sending stays human
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
EXPECTED_USER = "kartik.pillai@lh2.ai"

def main():
    if not os.path.exists(CLIENT_SECRET):
        sys.exit(f"client_secret.json not found at {CLIENT_SECRET} — download it from "
                 "Google Cloud Console (Credentials -> OAuth client ID, Desktop app).")
    cfg = json.load(open(CLIENT_SECRET, encoding="utf-8"))
    cfg = cfg.get("installed") or cfg.get("web") or {}
    for k in ("client_id", "client_secret", "auth_uri", "token_uri"):
        if k not in cfg:
            sys.exit(f"client_secret.json is missing {k!r} — wrong file? It must be a "
                     "'Desktop app' OAuth client export.")

    # localhost redirect catcher on a free port
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    redirect = f"http://127.0.0.1:{port}/"
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            msg = "Authorized — you can close this tab." if "code" in got else \
                  f"Authorization failed: {got.get('error', 'unknown')}"
            self.wfile.write(f"<h2>{msg}</h2>".encode())
        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = cfg["auth_uri"] + "?" + urllib.parse.urlencode({
        "client_id": cfg["client_id"],
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",   # this is what yields a refresh token
        "prompt": "consent",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "login_hint": EXPECTED_USER,
    })
    print(f"Opening browser — sign in as {EXPECTED_USER} and approve.\n"
          f"If no browser opens, paste this URL yourself:\n{url}\n")
    webbrowser.open(url)
    srv.socket.settimeout(300)
    while "code" not in got and "error" not in got:
        pass
    if "error" in got:
        sys.exit(f"consent failed: {got['error']}")

    body = urllib.parse.urlencode({
        "code": got["code"], "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"], "redirect_uri": redirect,
        "grant_type": "authorization_code", "code_verifier": verifier,
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(
            cfg["token_uri"], data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"})) as r:
        tok = json.loads(r.read())
    if "refresh_token" not in tok:
        sys.exit(f"no refresh_token in response ({list(tok)}) — re-run; if it persists, "
                 "remove the app at myaccount.google.com/permissions and try again.")

    # confirm which mailbox actually consented before trusting the token
    req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": f"Bearer {tok['access_token']}"})
    with urllib.request.urlopen(req) as r:
        who = json.loads(r.read())["emailAddress"]

    json.dump({"refresh_token": tok["refresh_token"], "token_uri": cfg["token_uri"],
               "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
               "scope": SCOPE, "authorized_as": who},
              open(TOKEN_FILE, "w", encoding="utf-8"), indent=2)
    print(f"Token saved to {TOKEN_FILE}")
    if who.lower() != EXPECTED_USER:
        print(f"WARNING: you authorized {who!r}, expected {EXPECTED_USER!r}. "
              "If that's the wrong mailbox, delete .gmail_token.json and re-run.")
    else:
        print(f"Authorized as {who} — done. Gmail pull scripts can now run.")

if __name__ == "__main__":
    main()
