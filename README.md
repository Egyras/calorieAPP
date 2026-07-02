# CalorieTracker

Food calorie & macro tracker with barcode scanning, BLE kitchen scale support, and bilingual LT/EN interface. Built with Flask, deployed via Docker + Jenkins CI/CD behind a Cloudflare tunnel.

## Features

- Add products from food labels (kcal, fat, protein, carbs per serving)
- Barcode scanning via camera — looks up nutrition data from the OpenFoodFacts database
- BLE kitchen scale connection — weigh food and auto-calculate calories (tested with Arboleaf CK10A)
- Pre-loaded Lithuanian food products (20 common items with nutrition data)
- Log daily intake with gram amounts
- Meal categorization (breakfast, lunch, dinner, snack)
- Daily goals with progress bars
- 7-day trend chart
- 30-day history
- Bilingual interface (Lithuanian / English) with cookie-persistent language toggle
- Google Sign-In + email-based login
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
| `calorie-allowed-emails` | Secret text | Comma-separated allowed emails |

### 3. Cloudflare Tunnel

Add a public hostname in your Cloudflare tunnel pointing to `http://192.168.8.211:5555`.

### 4. Local Development

```bash
docker compose up --build
# Opens at http://localhost:5555 (dev mode, no Google OAuth required)
```

## Tech Stack

- **Backend:** Flask (Python 3.11)
- **Database:** SQLite
- **Barcode lookup:** OpenFoodFacts API v2
- **Barcode scanning:** html5-qrcode (EAN-13/8, UPC-A/E with checksum validation)
- **BLE:** Web Bluetooth API
- **Auth:** Google OAuth (JS callback mode) + email login
- **Deploy:** Docker, Jenkins CI/CD, Cloudflare Tunnel
- **Hosting:** TrueNAS
