# Git Integration Setup

## Git Management Interface

Access the Git management page at: **https://track.pushlap.app/git**

### Features:
- 📊 View repository status
- 📝 Commit and push changes
- ⬇️ Pull latest updates
- 📜 View commit history
- 🔄 One-click sync

## Initial Setup

### 1. Set Git Remote URL

```bash
cd /root/f1-dashboard
git remote set-url origin https://github.com/yourusername/f1-dashboard.git
```

### 2. Configure Git Credentials

For HTTPS push access, configure credentials:

```bash
# Option A: Use personal access token
git config credential.helper store
git push  # Enter username and token (token as password)
```

Or use SSH:

```bash
# Option B: Use SSH keys
ssh-keygen -t ed25519 -C "your_email@example.com"
cat ~/.ssh/id_ed25519.pub  # Add this to GitHub

# Change remote to SSH
git remote set-url origin git@github.com:yourusername/f1-dashboard.git
```

### 3. Initial Commit

```bash
git add .
git commit -m "Initial commit: F1 Dashboard"
git branch -M main
git push -u origin main
```

## Using the Web Interface

### Commit Changes:
1. Go to https://track.pushlap.app/git
2. Enter commit message
3. Click "Commit & Push"
4. Changes are automatically pushed to GitHub

### Pull Updates:
1. Go to https://track.pushlap.app/git
2. Click "Pull Latest"
3. Latest changes from GitHub are pulled

### View History:
1. Go to https://track.pushlap.app/git
2. Click "View Log"
3. See last 20 commits

## Protected by GitHub OAuth

The `/git` page requires GitHub authentication (if configured). Only users in `ALLOWED_GITHUB_USERS` can access.

## What Gets Committed?

All files in the repository including:
- `app.py` - Main application
- `templates/` - HTML templates
- `data/` - Schedule data and configs
- `Dockerfile` - Container config
- `requirements.txt` - Python dependencies

Files excluded by `.gitignore`:
- `__pycache__/` - Python cache
- `*.pyc` - Compiled Python
- `.env` - Environment variables
- Log files

## Security Notes

- Git credentials stored securely (credential helper or SSH)
- Only authenticated users can commit/push
- All commits attributed to configured Git user
- Push requires proper GitHub permissions
