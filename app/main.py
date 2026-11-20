"""Docusrv – hostet Quarto-Schulungsunterlagen, austauschbar per ZIP-Upload."""
import hmac
import json
import os
import shutil
import tempfile
import time
import zipfile
from functools import wraps
from pathlib import Path

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, send_from_directory, url_for)

DATA_DIR = Path(os.environ.get("DOCUSRV_DATA", "/data"))
SITES_DIR = DATA_DIR / "sites"
CURRENT = DATA_DIR / "current"
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "1024"))
MAX_EXTRACTED_MB = int(os.environ.get("MAX_EXTRACTED_MB", "4096"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
SITES_DIR.mkdir(parents=True, exist_ok=True)


def _secret_key() -> bytes:
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"].encode()
    key_file = DATA_DIR / ".secret_key"
    try:
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(os.urandom(32))
    except FileExistsError:
        pass
    return key_file.read_bytes()


app.secret_key = _secret_key()


# --------------------------------------------------------------------------- auth
def _authorized() -> bool:
    auth = request.authorization
    if not auth or not ADMIN_PASSWORD:
        return False
    return hmac.compare_digest(auth.username or "", ADMIN_USER) and \
        hmac.compare_digest(auth.password or "", ADMIN_PASSWORD)


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not ADMIN_PASSWORD:
            return Response("ADMIN_PASSWORD ist nicht gesetzt – Admin-Bereich deaktiviert.", 503)
        if not _authorized():
            return Response("Anmeldung erforderlich", 401,
                            {"WWW-Authenticate": 'Basic realm="docusrv admin"'})
        return view(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------- site handling
def current_site() -> Path | None:
    if CURRENT.is_symlink() or CURRENT.exists():
        target = CURRENT.resolve()
        if target.is_dir():
            return target
    return None


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    total = 0
    dest = dest.resolve()
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or name.startswith("__MACOSX/"):
            continue
        target = (dest / name).resolve()
        if dest not in target.parents and target != dest:
            raise ValueError(f"Ungültiger Pfad im Archiv: {info.filename}")
        # Symlinks im Archiv ignorieren
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            continue
        total += info.file_size
        if total > MAX_EXTRACTED_MB * 1024 * 1024:
            raise ValueError("Entpackter Inhalt ist zu groß.")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)


def _find_root(path: Path) -> Path:
    """Steigt in einzelne Top-Level-Ordner ab (z.B. `_site/`), bis index.html gefunden ist."""
    while not (path / "index.html").exists():
        entries = [e for e in path.iterdir() if not e.name.startswith(".")]
        if len(entries) == 1 and entries[0].is_dir():
            path = entries[0]
        else:
            break
    return path


def _meta_path(version: Path) -> Path:
    """Metadaten liegen neben (nicht in) dem Versionsordner, damit sie nicht ausgeliefert werden."""
    return version.with_name(version.name + ".json")


def read_meta(version: Path) -> dict:
    try:
        return json.loads(_meta_path(version).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def install_zip(fileobj, filename: str | None = None) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = Path(tempfile.mkdtemp(prefix=f"upload-{stamp}-", dir=SITES_DIR))
    try:
        with zipfile.ZipFile(fileobj) as zf:
            _safe_extract(zf, staging)
        root = _find_root(staging)
        if not (root / "index.html").exists():
            raise ValueError("Keine index.html im Archiv gefunden.")
        final = SITES_DIR / stamp
        n = 1
        while final.exists():
            final = SITES_DIR / f"{stamp}-{n}"
            n += 1
        root.rename(final)
        _meta_path(final).write_text(json.dumps({"filename": filename}), encoding="utf-8")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    # Symlink atomar umsetzen
    tmp_link = DATA_DIR / ".current.tmp"
    if tmp_link.is_symlink() or tmp_link.exists():
        tmp_link.unlink()
    tmp_link.symlink_to(final)
    os.replace(tmp_link, CURRENT)

    # alte Versionen aufräumen (die letzte vorherige als Backup behalten)
    versions = sorted((p for p in SITES_DIR.iterdir() if p.is_dir() and p != final),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    for old in versions[1:]:
        shutil.rmtree(old, ignore_errors=True)
        _meta_path(old).unlink(missing_ok=True)
    return final


def list_versions():
    cur = current_site()
    out = []
    for p in sorted(SITES_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if p.is_dir() and not p.name.startswith("upload-"):
            out.append({"name": p.name, "current": cur == p.resolve(),
                        "filename": read_meta(p).get("filename")})
    return out


# --------------------------------------------------------------------------- admin routes
@app.route("/_admin/", methods=["GET"])
@admin_required
def admin():
    return render_template("admin.html", versions=list_versions(),
                           max_mb=MAX_UPLOAD_MB)


@app.route("/_admin/upload", methods=["POST"])
@admin_required
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Keine Datei ausgewählt.", "error")
        return redirect(url_for("admin"))
    if not f.filename.lower().endswith(".zip"):
        flash("Bitte eine .zip-Datei hochladen.", "error")
        return redirect(url_for("admin"))
    try:
        final = install_zip(f.stream, Path(f.filename.replace("\\", "/")).name)
    except zipfile.BadZipFile:
        flash("Die Datei ist kein gültiges ZIP-Archiv.", "error")
    except ValueError as e:
        flash(str(e), "error")
    else:
        flash(f"Unterlagen erfolgreich veröffentlicht (Version {final.name}).", "ok")
    return redirect(url_for("admin"))


@app.route("/_admin/activate/<name>", methods=["POST"])
@admin_required
def activate(name):
    target = SITES_DIR / name
    if "/" in name or name.startswith(".") or not target.is_dir():
        abort(404)
    tmp_link = DATA_DIR / ".current.tmp"
    if tmp_link.is_symlink() or tmp_link.exists():
        tmp_link.unlink()
    tmp_link.symlink_to(target)
    os.replace(tmp_link, CURRENT)
    flash(f"Version {name} ist jetzt aktiv.", "ok")
    return redirect(url_for("admin"))


@app.route("/_health")
def health():
    return {"status": "ok", "site": current_site() is not None}


# --------------------------------------------------------------------------- site serving
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    site = current_site()
    if site is None:
        return render_template("empty.html"), 200
    full = (site / path).resolve()
    if site != full and site not in full.parents:
        abort(404)
    if full.is_dir():
        if path and not path.endswith("/"):
            return redirect("/" + path + "/", 301)
        path = (path + "index.html") if path else "index.html"
        full = full / "index.html"
    if not full.is_file():
        notfound = site / "404.html"
        if notfound.is_file():
            resp = send_from_directory(site, "404.html")
            resp.status_code = 404
            return resp
        abort(404)
    resp = send_from_directory(site, path)
    resp.headers["Cache-Control"] = "no-cache"
    return resp
