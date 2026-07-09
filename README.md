# CalorieTracker

Food calorie & macro tracker with barcode scanning, BLE kitchen scale support, and bilingual LT/EN interface. Built with Flask, deployed via Docker + Jenkins CI/CD behind a Cloudflare tunnel.

## Features

- Add products from food labels (kcal, fat, protein, carbs per serving) with inline editing
- Barcode scanning via camera — looks up nutrition data from the OpenFoodFacts database
- BLE kitchen scale connection — weigh food and auto-calculate calories (tested with Arboleaf CK10A)
- Pre-loaded Lithuanian food products (9 common items with nutrition data)
- Product deduplication across group members — no duplicates shown in shared view
- Log daily intake with gram amounts
- Meal categorization (breakfast, lunch, dinner, snack)
- Daily goals with progress bars
- 30-day history with per-day breakdown
- Recipes — combine products into reusable recipes with gram amounts and optional instructions, log an entire recipe in one click, searchable ingredient dropdown
- Groups — predefined Family and Friends groups, invite by email, accept/decline requests, shared product & recipe library with separate daily logs and goals
- QR code invite system — share a token-based QR code, invited users request access, admins approve/decline from the admin panel
- Admin panel — manage allowed emails, approve/decline pending access requests, admin-only access for designated emails
- Bilingual interface (Lithuanian / English) with cookie-persistent language toggle on all pages including login and invite
- Google Sign-In + email-based login with autocomplete support
- Input validation on product add/edit with bilingual flash messages
- Mobile-responsive navigation with compact layout on small screens
- SQLite persistent storage

## Browser Compatibility

| Feature | Chrome (Android/Desktop) | Bluefy (iOS) | Safari (iOS) |
|---|---|---|---|
| Barcode scanning | Yes | Yes | Yes |
| BLE kitchen scale | Yes | Yes (Web Bluetooth) | No |
| Google Sign-In | Yes | No (use email login) | Yes |

iOS does not support Web Bluetooth in Safari or Chrome. Use the [Bluefy](https://apps.apple.com/app/bluefy-web-ble-browser/id1492822055) browser for BLE scale access on iPhone/iPad.

## Supported Scales

- **Arboleaf CK10A** — kitchen scale, BLE protocol reverse-engineered (weight at bytes 9-10, big-endian uint16, 0.1g resolution)
- Other BLE scales with standard Weight Scale Service (0x181D) should also work

## Setup

### 1. Google OAuth (required for production)

1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Create OAuth 2.0 Client ID (Web application)
3. Add authorized redirect URI: `https://your-domain.com/auth/google`
4. Note the Client ID and Client Secret

### 2. Jenkins Credentials

Add these in Jenkins (Manage Jenkins > Credentials):

| Credential ID | Type | Value |
|---|---|---|
| `dockerhub` | Username/Password | Docker Hub login |
| `calorie-google-client-id` | Secret text | Google OAuth Client ID |
| `calorie-google-client-secret` | Secret text | Google OAuth Client Secret |
| `calorie-secret-key` | Secret text | Random string for Flask sessions |
| `calorie-allowed-emails` | Secret text | Comma-separated allowed emails (seeds DB on first run) |

### 3. Cloudflare Tunnel

Add a public hostname in your Cloudflare tunnel pointing to `http://192.168.8.211:5555`.

### 4. Local Development

```bash
docker compose up --build
# Opens at http://localhost:8080 (dev mode, no Google OAuth required)
```

## Access Control

Email whitelist is stored in SQLite (`allowed_emails` table), seeded from the `ALLOWED_EMAILS` env var on first run. After that, manage access from the in-app admin panel.

Admin users are hardcoded in `web.py` (`ADMIN_EMAILS` list).

Invite flow: existing user shares QR code containing a token-based invite link. New user scans QR, sees an invite page explaining the process, signs in, and their request goes to the admin panel for approval.

## Tech Stack

- **Backend:** Flask (Python 3.11)
- **Database:** SQLite
- **Barcode lookup:** OpenFoodFacts API v2
- **Barcode scanning:** html5-qrcode (EAN-13/8, UPC-A/E with checksum validation)
- **BLE:** Web Bluetooth API
- **QR codes:** QRious (CDN)
- **Auth:** Google OAuth (JS callback mode) + email login
- **Deploy:** Docker, Jenkins CI/CD, Cloudflare Tunnel
- **Hosting:** TrueNAS
