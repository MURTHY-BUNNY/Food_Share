# Turning on real email delivery

FoodShare sends two kinds of mail: the 6-digit code for a new signup, and the
6-digit code for a password reset. Until a mail account is configured the API
runs in **demo mode**: the code is printed in the API console and shown on the
page, and nothing is emailed. No other part of the flow changes.

Everything below is configuration. The sending code is already in place.

## 1. The four settings

Add these to `backend/.env`. `SMTP_HOST` alone is enough for a relay that needs
no login (for example MailHog on your machine), but every hosted provider needs
the user and password too.

| Setting | Meaning |
| --- | --- |
| `SMTP_HOST` | The provider's outgoing mail server |
| `SMTP_PORT` | `587` for STARTTLS (almost always), `465` only with `SMTP_SSL=true` |
| `SMTP_USER` | The account that signs in |
| `SMTP_PASSWORD` | An app password or SMTP key, **not** the normal account password |
| `SMTP_FROM` | Optional. The visible sender. Keep it equal to `SMTP_USER` on Gmail |
| `SMTP_FROM_NAME` | Optional. Display name, defaults to `FoodShare` |
| `SMTP_SSL` | Optional. `true` for port 465, otherwise leave it unset |

### Gmail

```ini
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your.address@gmail.com
SMTP_PASSWORD=abcdefghijklmnop
SMTP_FROM=your.address@gmail.com
```

`SMTP_PASSWORD` is a 16-character **app password**, which Google only offers
once 2-Step Verification is on:

1. Google Account → Security → turn on **2-Step Verification**.
2. Same page → **App passwords** → name it (for example "FoodShare") → Create.
3. Copy the 16 characters; spaces do not matter.

Your normal Google password will not work here — Google rejects it with
`535 5.7.8` or `534 5.7.9`.

### Brevo (free tier, 300 mails a day)

Better for a demo than a personal mailbox, and it survives stricter spam
filters because the sender is verified.

```ini
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USER=your.login@example.com
SMTP_PASSWORD=your-smtp-key
SMTP_FROM=a-verified-sender@example.com
```

The SMTP key is under **SMTP & API → SMTP** in the Brevo dashboard, not the
account password. `SMTP_FROM` has to be an address you verified under
**Senders**.

### Mailtrap (a private test inbox)

Proves the plumbing without mailing a real person — messages are captured in
the Mailtrap dashboard and never delivered.

```ini
SMTP_HOST=sandbox.smtp.mailtrap.io
SMTP_PORT=2525
SMTP_USER=from-your-mailtrap-inbox
SMTP_PASSWORD=from-your-mailtrap-inbox
```

## 2. Restart the API

`.env` is read once at startup, so a running server keeps the old values.

```powershell
cd backend
.\venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

The console prints one of:

- `Email delivery: sending through smtp.gmail.com:587 (STARTTLS) as ...` — ready.
- `WARNING: email delivery is half-configured - ...` — a setting is missing.
- `NOTICE: SMTP is not configured ...` — still demo mode.

## 3. Confirm it in one click

Admin console → **Email delivery**:

- The badge reads **Sending by email** instead of *Demo mode*.
- Server, sender and encryption are filled in.
- Type your own address → **Send test email**. A green message means the
  provider accepted the message; check the inbox and the spam folder.

Then do a real signup. The code arrives by email and the demo box no longer
appears on the page.

Same thing from the API, if you prefer:

```powershell
curl http://127.0.0.1:8000/health                 # "email": "smtp"
curl -H "Authorization: Bearer <admin token>" http://127.0.0.1:8000/admin/mail/status
curl -X POST -H "Authorization: Bearer <admin token>" -H "Content-Type: application/json" `
     -d "{\"email\":\"you@gmail.com\"}" http://127.0.0.1:8000/admin/mail/test
```

## 4. When it does not work

| What you see | Cause |
| --- | --- |
| `535 5.7.8 Username and Password not accepted` | Used the account password, or 2FA is off so app passwords are unavailable |
| `534 5.7.9 Application-specific password required` | Same as above: create an app password |
| `STARTTLS extension not supported` | Wrong port. Use 587 for STARTTLS, or set `SMTP_SSL=true` with 465 |
| `[WinError 10061]` / connection refused / timeout | Wrong host or port, or the firewall blocks outbound mail |
| `550` sender rejected | `SMTP_FROM` is not a verified sender with that provider |
| Test arrives but signup codes do not (or land in spam) | The provider's spam filtering; check the spam folder, and prefer a verified sender such as Brevo |

Failed sends are never silent: the API answers `502` with the provider's reason,
the page shows an error, and the console logs the same text.

## 5. Keep the credentials safe

- An app password is not your account password, and you can revoke it at any
  time from the same Google page.
- `backend/.env` holds the database password and the token secrets as well.
  Do not commit it. If a password ever gets pasted into a chat or a document,
  revoke it and create a new one.
- In demo mode the code is returned to the page as `dev_code` so the flow can be
  tested. That stops automatically once SMTP is configured.
