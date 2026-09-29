import io
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, abort, flash, redirect, render_template, request, send_file, session, url_for
from flask_wtf import CSRFProtect
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "change-this-secret-key"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)
csrf = CSRFProtect(app)

SCOPES = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]
ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "ppt", "pptx", "zip"}
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-this-password")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")

ASSIGNMENT_HEADERS = [
    "id", "title", "subject", "description", "price", "drive_file_id", "filename", "created_at"
]
ORDER_HEADERS = [
    "id", "assignment_id", "name", "email", "reference", "status", "created_at", "reviewed_at", "access_token"
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def google_clients():
    if not GOOGLE_CREDENTIALS_JSON or not GOOGLE_SHEET_ID or not GOOGLE_DRIVE_FOLDER_ID:
        raise RuntimeError("Google configuration is incomplete. Set GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_ID and GOOGLE_DRIVE_FOLDER_ID.")
    try:
        info = json.loads(GOOGLE_CREDENTIALS_JSON)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON.") from exc
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    return sheets, drive


def sheet_values(sheet_name):
    sheets, _ = google_clients()
    result = sheets.spreadsheets().values().get(
        spreadsheetId=GOOGLE_SHEET_ID,
        range=f"{sheet_name}!A:Z",
    ).execute()
    return result.get("values", [])


def ensure_sheet_headers():
    sheets, _ = google_clients()
    meta = sheets.spreadsheets().get(spreadsheetId=GOOGLE_SHEET_ID).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    requests = []
    if "Assignments" not in existing:
        requests.append({"addSheet": {"properties": {"title": "Assignments"}}})
    if "Orders" not in existing:
        requests.append({"addSheet": {"properties": {"title": "Orders"}}})
    if requests:
        sheets.spreadsheets().batchUpdate(spreadsheetId=GOOGLE_SHEET_ID, body={"requests": requests}).execute()

    for name, headers in (("Assignments", ASSIGNMENT_HEADERS), ("Orders", ORDER_HEADERS)):
        values = sheets.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID, range=f"{name}!A1:Z1"
        ).execute().get("values", [])
        if not values:
            sheets.spreadsheets().values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f"{name}!A1:{chr(64 + len(headers))}1",
                valueInputOption="RAW",
                body={"values": [headers]},
            ).execute()


def rows_to_dicts(values):
    if not values:
        return []
    headers = values[0]
    return [dict(zip(headers, row + [""] * (len(headers) - len(row)))) for row in values[1:]]


def assignments():
    ensure_sheet_headers()
    return rows_to_dicts(sheet_values("Assignments"))


def orders():
    ensure_sheet_headers()
    return rows_to_dicts(sheet_values("Orders"))


def append_row(sheet_name, row, headers):
    sheets, _ = google_clients()
    values = [row.get(h, "") for h in headers]
    sheets.spreadsheets().values().append(
        spreadsheetId=GOOGLE_SHEET_ID,
        range=f"{sheet_name}!A:{chr(64 + len(headers))}",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [values]},
    ).execute()


def find_row_number(sheet_name, record_id):
    values = sheet_values(sheet_name)
    if not values:
        return None
    try:
        id_col = values[0].index("id")
    except ValueError:
        return None
    for idx, row in enumerate(values[1:], start=2):
        if len(row) > id_col and row[id_col] == record_id:
            return idx
    return None


def update_row(sheet_name, record_id, record, headers):
    row_number = find_row_number(sheet_name, record_id)
    if row_number is None:
        return False
    sheets, _ = google_clients()
    sheets.spreadsheets().values().update(
        spreadsheetId=GOOGLE_SHEET_ID,
        range=f"{sheet_name}!A{row_number}:{chr(64 + len(headers))}{row_number}",
        valueInputOption="RAW",
        body={"values": [[record.get(h, "") for h in headers]]},
    ).execute()
    return True


def delete_row(sheet_name, record_id):
    sheets, _ = google_clients()
    meta = sheets.spreadsheets().get(spreadsheetId=GOOGLE_SHEET_ID).execute()
    sheet = next((s for s in meta.get("sheets", []) if s["properties"]["title"] == sheet_name), None)
    row_number = find_row_number(sheet_name, record_id)
    if not sheet or row_number is None:
        return False
    sheet_id = sheet["properties"]["sheetId"]
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=GOOGLE_SHEET_ID,
        body={"requests": [{"deleteDimension": {"range": {
            "sheetId": sheet_id,
            "dimension": "ROWS",
            "startIndex": row_number - 1,
            "endIndex": row_number,
        }}}]},
    ).execute()
    return True


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def valid_email(value):
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value or "")) and len(value) <= 254


def drive_get_file(drive, file_id):
    return drive.files().get(fileId=file_id, fields="id,name,mimeType,size,trashed").execute()


def drive_download(drive, file_id):
    request_obj = drive.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request_obj, chunksize=1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buffer.seek(0)
    return buffer


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; form-action 'self'"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/")
def index():
    try:
        items = assignments()
    except Exception as exc:
        return render_template("error.html", message=f"Google Sheets is not configured correctly: {exc}"), 500
    q = request.args.get("q", "").strip().lower()
    subject = request.args.get("subject", "").strip().lower()
    if q:
        items = [x for x in items if q in (x.get("title", "") + " " + x.get("description", "")).lower()]
    if subject:
        items = [x for x in items if x.get("subject", "").lower() == subject]
    subjects = sorted({x.get("subject", "") for x in assignments() if x.get("subject")})
    return render_template("index.html", assignments=items, subjects=subjects, q=request.args.get("q", ""), selected_subject=request.args.get("subject", ""))


@app.route("/assignment/<aid>")
def assignment(aid):
    item = next((x for x in assignments() if x.get("id") == aid), None)
    if not item:
        abort(404)
    return render_template("assignment.html", item=item)


@app.route("/buy/<aid>", methods=["GET", "POST"])
def buy(aid):
    item = next((x for x in assignments() if x.get("id") == aid), None)
    if not item:
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        reference = request.form.get("reference", "").strip()
        if not (1 <= len(name) <= 100) or not valid_email(email) or not (1 <= len(reference) <= 100):
            flash("Please enter a valid name, email and payment reference.", "error")
        else:
            order = {
                "id": secrets.token_urlsafe(12),
                "assignment_id": aid,
                "name": name,
                "email": email,
                "reference": reference,
                "status": "pending",
                "created_at": now_iso(),
                "reviewed_at": "",
                "access_token": secrets.token_urlsafe(32),
            }
            append_row("Orders", order, ORDER_HEADERS)
            return redirect(url_for("submitted", token=order["access_token"]))
    return render_template("buy.html", item=item)


@app.route("/submitted/<token>")
def submitted(token):
    order = next((x for x in orders() if secrets.compare_digest(x.get("access_token", ""), token)), None)
    if not order:
        abort(404)
    item = next((x for x in assignments() if x.get("id") == order.get("assignment_id")), None)
    return render_template("submitted.html", order=order, item=item, token=token)


@app.route("/download/<token>")
def download(token):
    order = next((x for x in orders() if secrets.compare_digest(x.get("access_token", ""), token)), None)
    if not order or order.get("status") != "approved":
        abort(404)
    item = next((x for x in assignments() if x.get("id") == order.get("assignment_id")), None)
    if not item:
        abort(404)
    _, drive = google_clients()
    file_meta = drive_get_file(drive, item["drive_file_id"])
    if file_meta.get("trashed"):
        abort(404)
    stream = drive_download(drive, item["drive_file_id"])
    filename = secure_filename(item.get("filename") or file_meta.get("name") or "assignment") or "assignment"
    return send_file(stream, as_attachment=True, download_name=filename, mimetype="application/octet-stream")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if secrets.compare_digest(password, ADMIN_PASSWORD):
            session.clear()
            session["admin"] = True
            return redirect(request.args.get("next") or url_for("admin"))
        flash("Invalid password.", "error")
    return render_template("admin_login.html")


@app.post("/admin/logout")
@admin_required
def admin_logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/admin")
@admin_required
def admin():
    return render_template("admin.html", assignments=assignments(), orders=orders())


@app.post("/admin/order/<oid>/<action>")
@admin_required
def order_action(oid, action):
    if action not in {"approve", "reject"}:
        abort(400)
    data = orders()
    order = next((x for x in data if x.get("id") == oid), None)
    if not order:
        abort(404)
    order["status"] = "approved" if action == "approve" else "rejected"
    order["reviewed_at"] = now_iso()
    update_row("Orders", oid, order, ORDER_HEADERS)
    flash("Order updated.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/assignment/delete/<aid>")
@admin_required
def delete_assignment(aid):
    item = next((x for x in assignments() if x.get("id") == aid), None)
    if not item:
        abort(404)
    _, drive = google_clients()
    try:
        drive.files().delete(fileId=item["drive_file_id"]).execute()
    except Exception:
        # Keep the catalog deletion independent; the file can be removed manually if needed.
        pass
    delete_row("Assignments", aid)
    flash("Assignment removed from the catalog.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/assignment/add", methods=["GET", "POST"])
@admin_required
def add_assignment():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        subject = request.form.get("subject", "").strip()
        description = request.form.get("description", "").strip()
        price_raw = request.form.get("price", "").strip()
        drive_file_id = request.form.get("drive_file_id", "").strip()
        if not title or not subject or not drive_file_id:
            flash("Title, subject and Google Drive file ID are required.", "error")
            return render_template("add_assignment.html")
        try:
            price = float(price_raw)
            if price < 0 or price > 100000:
                raise ValueError
        except ValueError:
            flash("Invalid price.", "error")
            return render_template("add_assignment.html")
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,}", drive_file_id):
            flash("Invalid Google Drive file ID.", "error")
            return render_template("add_assignment.html")
        _, drive = google_clients()
        try:
            meta = drive_get_file(drive, drive_file_id)
        except Exception:
            flash("Could not access that Drive file. Make sure it is shared with the service account and is inside your assignment folder.", "error")
            return render_template("add_assignment.html")
        if meta.get("trashed"):
            flash("That Drive file is in the trash.", "error")
            return render_template("add_assignment.html")
        try:
            if int(meta.get("size") or 0) > 25 * 1024 * 1024:
                flash("Assignment files must be 25 MB or smaller.", "error")
                return render_template("add_assignment.html")
        except (TypeError, ValueError):
            pass
        parents = drive.files().get(fileId=drive_file_id, fields="parents").execute().get("parents", [])
        if GOOGLE_DRIVE_FOLDER_ID not in parents:
            flash("That file is not directly inside the configured assignment folder.", "error")
            return render_template("add_assignment.html")
        filename = secure_filename(meta.get("name", ""))
        if "." not in filename or filename.rsplit(".", 1)[1].lower() not in ALLOWED_EXTENSIONS:
            flash("Allowed files: PDF, DOC, DOCX, PPT, PPTX and ZIP.", "error")
            return render_template("add_assignment.html")
        aid = uuid.uuid4().hex
        item = {
            "id": aid,
            "title": title[:150],
            "subject": subject[:100],
            "description": description[:2000],
            "price": f"{price:.2f}",
            "drive_file_id": drive_file_id,
            "filename": filename,
            "created_at": now_iso(),
        }
        append_row("Assignments", item, ASSIGNMENT_HEADERS)
        flash("Assignment added.", "success")
        return redirect(url_for("admin"))
    return render_template("add_assignment.html")


@app.errorhandler(413)
def too_large(_):
    return render_template("error.html", message="Request too large."), 413


@app.errorhandler(Exception)
def handle_unexpected(error):
    app.logger.exception("Unhandled application error", exc_info=error)
    return render_template("error.html", message="Something went wrong. Check the server logs."), 500


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
