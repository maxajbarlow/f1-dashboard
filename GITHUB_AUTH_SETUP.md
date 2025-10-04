# GitHub OAuth Authentication Setup

## Step 1: Create GitHub OAuth App

1. Go to https://github.com/settings/developers
2. Click "New OAuth App"
3. Fill in:
   - **Application name**: F1 Dashboard
   - **Homepage URL**: `https://track.pushlap.app`
   - **Authorization callback URL**: `https://track.pushlap.app/login/github/authorized`
4. Click "Register application"
5. Copy the **Client ID** and generate a **Client Secret**

## Step 2: Configure Environment Variables

Add these environment variables when running the Docker container:

```bash
docker stop f1-dashboard
docker rm f1-dashboard

docker run -d --name f1-dashboard \
  -p 5000:5000 \
  -v /root/f1-dashboard/data:/app/data \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  -e GITHUB_CLIENT_ID="your_github_client_id_here" \
  -e GITHUB_CLIENT_SECRET="your_github_client_secret_here" \
  -e ALLOWED_GITHUB_USERS="yourusername,otherusername" \
  f1-dashboard
```

## Step 3: Restart Nginx

```bash
sudo systemctl reload nginx
```

## How It Works

### Protected Endpoints (Require GitHub Login):
- `/upload` - File upload page
- `/config` - Configuration page
- `/api/config` (POST only) - Save configuration
- `/api/config/reset` - Reset configuration

### Public Endpoints:
- `/` - Main dashboard (no auth required)
- `/api/sessions` - Session data (no auth required)
- `/api/config` (GET only) - Read configuration (no auth required)

### Authentication Flow:
1. User visits protected endpoint
2. Redirected to `/login/github`
3. GitHub login/authorization
4. Callback to `/login/github/authorized`
5. If username is in `ALLOWED_GITHUB_USERS`, access granted
6. If not in list, access denied

### Without GitHub OAuth:
If `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` are not set, the app will run without authentication (backward compatible, but less secure).

## Security Notes

- Only users in `ALLOWED_GITHUB_USERS` can access admin features
- Usernames are case-sensitive GitHub usernames
- Multiple users: comma-separated (no spaces)
- OAuth tokens are session-based
