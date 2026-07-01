# CalorieTracker

Food calorie & macro tracker with Google OAuth. Track kcal, fat, protein, and carbs from food labels.

## Setup

### 1. Google OAuth (required for production)

1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Create OAuth 2.0 Client ID (Web application)
3. Add authorized redirect URI: `https://your-domain.com/auth/google`
4. Note the Client ID and Client Secret

### 2. Jenkins Credentials

Add these in Jenkins (Manage Jenkins → Credentials):

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

## Features

- Add products from food labels (kcal, fat, protein, carbs per serving)
- Log daily intake with gram amounts
- Meal categorization (breakfast, lunch, dinner, snack)
- Daily goals with progress bars
- 7-day trend chart
- 30-day history
- Google Sign-In authentication
- SQLite persistent storage
