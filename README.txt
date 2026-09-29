# StudyShelf — Local Assignment Marketplace

A simple personal Flask website for manually selling assignments using a GPay QR. There is **no database**, Supabase, Razorpay, seller registration, or automatic payment verification.

## Flow
1. You log in at `/admin`.
2. Add assignment metadata and upload its file.
3. Buyer opens the assignment and scans your GPay QR.
4. Buyer submits name, email, and GPay transaction/reference ID.
5. You check GPay yourself.
6. You approve/reject the order from `/admin`.
7. Only approved orders can download the file.

## Setup
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Set environment variables. On Windows PowerShell:
```powershell
$env:SECRET_KEY="a-long-random-secret"
$env:ADMIN_PASSWORD="a-strong-password"
```

Put your GPay QR image at:
`static/images/gpay-qr.png`

Run:
```bash
python app.py
```
Open `http://127.0.0.1:5000`.

## Important
- The default admin password is intentionally unsafe. Always set `ADMIN_PASSWORD`.
- Assignment files live in `assignments/` and order/catalog data live in `data/`.
- Do not expose the `data/` directory through a static web server.
- Back up `data/` and `assignments/`.
- Payment is manually verified; a submitted transaction ID never grants access automatically.
- For public deployment use HTTPS and set `COOKIE_SECURE=1`.
- This project does not automatically inspect uploaded files for malware. Since only the site owner uploads them, only upload trusted files.
