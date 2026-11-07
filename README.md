# FoodShare

FoodShare is a food-donation web app. Its frontend is static HTML, CSS and
JavaScript; its API uses FastAPI and MySQL. Donors can register, sign in with
email or Google, manage profiles and donations, and verify email with a code.

## Run locally (Windows)

Requirements: Python 3.10 or newer and a running MySQL server.

1. Create a MySQL database, for example:

   ```sql
   CREATE DATABASE foodshare CHARACTER SET utf8mb4;
   ```

2. From the project root, create a virtual environment and install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
   Copy-Item .env.example .env
   ```

3. Edit `.env` with your database settings. Set separate random values for
   `DONOR_TOKEN_SECRET` and `ADMIN_TOKEN_SECRET`; generate each with:

   ```powershell
   .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Generate an admin password hash with
   `.\.venv\Scripts\python.exe backend\generate_admin_hash.py` and put it in
   `ADMIN_PASSWORD_HASH`. Never commit `.env` or share its contents.

4. Start the API in one terminal:

   ```powershell
   Set-Location backend
   ..\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
   ```

5. Start the frontend in another terminal from the project root:

   ```powershell
   py -m http.server 5500 --bind 127.0.0.1
   ```

   Open <http://127.0.0.1:5500/html/index.html>. API documentation is at
   <http://127.0.0.1:8000/docs>; check <http://127.0.0.1:8000/health> for API,
   database and email status.

Email is in demo mode until SMTP settings are configured. In demo mode,
verification codes are shown by the app. See [backend/EMAIL_SETUP.md](backend/EMAIL_SETUP.md)
to configure email delivery.

## Tests and checks

Run the email-domain validation tests:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m unittest test_email_domain_validation
```

Check JavaScript syntax from the project root:

```powershell
Get-ChildItem js\*.js | ForEach-Object { node --check $_.FullName }
```

## Deployment notes

- Serve the frontend and API over HTTPS, and use a production ASGI server or
  hosting platform rather than Uvicorn's development reload mode.
- Set `CORS_ALLOWED_ORIGINS` to the exact comma-separated frontend origins,
  such as `https://foodshare.example.com`. Localhost origins remain enabled for
  development. Restart the API after changing environment settings.
- Configure production database credentials, distinct strong token secrets,
  a non-demo SMTP provider, and the Google Identity Services client ID as
  environment variables. Do not expose MySQL to the public internet.
- Persist the `uploads` directory if profile photos must survive application
  restarts or redeployments.
- Keep `.env`, database files, and uploaded donor photos out of Git.

For endpoint details, schema notes and project background, see
[PROJECT_HANDOFF.md](PROJECT_HANDOFF.md).
