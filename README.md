# StudyShelf — Flask + Google Drive + Google Sheets + GPay

Personal-use assignment marketplace. No Supabase, SQL database, or payment gateway.

## Architecture
- Flask application on Render (or another Python host)
- Private Google Drive folder stores assignment files
- Google Sheet `Assignments` stores catalog metadata
- Google Sheet `Orders` stores buyer/payment requests
- GPay QR is a static image in `static/images/gpay-qr.jpeg`
- Payments are manually verified by the admin

## Google setup
1. Create a Google Cloud project.
2. Enable Google Drive API and Google Sheets API.
3. Create a service account and download its JSON key.
4. Create a Google Sheet and copy its ID from the URL.
5. Create a private Google Drive folder for assignments.
6. Share the Sheet and Drive folder with the service-account email as Editor.
7. Put the Drive folder ID and Sheet ID in environment variables.
8. Put the entire service-account JSON into `GOOGLE_SERVICE_ACCOUNT_JSON` as one-line JSON.

On first request, the app creates two tabs if needed: `Assignments` and `Orders`.

## Adding an assignment
1. Upload the PDF/DOC/DOCX/PPT/PPTX/ZIP manually into the private Drive folder.
2. Copy the file ID from its Drive URL.
3. Admin → Add assignment.
4. Enter title, subject, description, price and Drive file ID.

The app verifies that the file is directly inside the configured folder and that the service account can access it.

## Payment
1. Buyer scans `static/images/gpay-qr.jpeg` and pays the displayed amount.
2. Buyer submits name, email and GPay reference ID.
3. Order is `pending`.
4. Admin checks GPay manually.
5. Admin approves/rejects the order.
6. Approved buyer gets a private download through Flask; the Google Drive file is never exposed publicly.

## Local run
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Render
Use:
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app`
- Health check: `/health`

Set the environment variables from `.env.example` in Render. Never commit the service-account JSON or real secrets.
