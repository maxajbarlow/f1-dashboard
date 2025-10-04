# F1 Dashboard

A real-time Formula 1 race weekend dashboard with live countdowns, configurable meal times, and schedule management.

🔗 **Live Demo**: https://track.pushlap.app

## Features

- 📅 **Live Race Schedule** - Real-time countdowns to F1 sessions (Practice, Qualifying, Race)
- 🍽️ **Meal Times Management** - Configure breakfast, lunch, and dinner times for each day
- 🏨 **Hotel Leave Times** - Set departure times for the race weekend
- 🔐 **GitHub OAuth** - Secure authentication for configuration pages
- 📄 **PDF Upload** - Upload race schedule PDFs
- 🌍 **Timezone Support** - Automatic timezone conversion for international races
- 🔄 **Git Integration** - Built-in Git management interface for version control
- 🐳 **Docker Ready** - Containerized deployment with Docker

## Quick Start

### Using Docker (Recommended)

```bash
docker build -t f1-dashboard .
docker run -d -p 5000:5000 -v $(pwd)/data:/app/data f1-dashboard
```

Access at: http://localhost:5000

### Manual Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py
```

## Configuration

### Environment Variables

Create a `.env` file:

```bash
SECRET_KEY=your-secret-key
GITHUB_CLIENT_ID=your-github-oauth-client-id
GITHUB_CLIENT_SECRET=your-github-oauth-secret
ALLOWED_GITHUB_USERS=username1,username2
```

### GitHub OAuth Setup

See [GITHUB_AUTH_SETUP.md](GITHUB_AUTH_SETUP.md) for detailed OAuth configuration.

### Git Integration

See [GIT_SETUP.md](GIT_SETUP.md) for Git repository management setup.

## Project Structure

```
f1-dashboard/
├── app.py                      # Main Flask application
├── templates/
│   └── index.html             # Dashboard frontend
├── data/
│   ├── schedule_config.json   # Meal/hotel times configuration
│   └── *.json                 # Race schedule data
├── Dockerfile                 # Docker container configuration
├── requirements.txt           # Python dependencies
└── README.md                  # This file
```

## API Endpoints

### Public Endpoints
- `GET /` - Main dashboard
- `GET /api/schedule` - Current race schedule
- `GET /api/time` - Server time (UTC)

### Protected Endpoints (Require GitHub Auth)
- `GET /config` - Configuration page
- `POST /api/config` - Update meal/hotel times
- `POST /upload` - Upload PDF schedule
- `GET /git` - Git management interface
- `POST /git/commit` - Commit changes
- `POST /git/pull` - Pull updates

## Security Features

- ✅ Input validation on all API endpoints
- ✅ PDF magic byte validation
- ✅ Security headers (CSP, X-Frame-Options, etc.)
- ✅ GitHub OAuth authentication
- ✅ HTTPS support via nginx/certbot
- ✅ No SQL injection risk (JSON-based storage)
- ✅ XSS protection via Jinja2 auto-escaping

## Production Deployment

### HTTPS Setup with nginx

```bash
# Install nginx and certbot
sudo apt install nginx certbot python3-certbot-nginx

# Configure nginx reverse proxy
sudo nano /etc/nginx/sites-available/track.pushlap.app

# Obtain SSL certificate
sudo certbot --nginx -d track.pushlap.app

# Restart nginx
sudo systemctl restart nginx
```

### Docker Compose

```yaml
version: '3'
services:
  f1-dashboard:
    build: .
    ports:
      - "5000:5000"
    volumes:
      - ./data:/app/data
    environment:
      - SECRET_KEY=${SECRET_KEY}
      - GITHUB_CLIENT_ID=${GITHUB_CLIENT_ID}
      - GITHUB_CLIENT_SECRET=${GITHUB_CLIENT_SECRET}
    restart: unless-stopped
```

## Technologies

- **Backend**: Python 3.11, Flask, Gunicorn
- **Frontend**: HTML5, CSS3, Vanilla JavaScript
- **Auth**: Flask-Dance (GitHub OAuth)
- **Deployment**: Docker, nginx, Let's Encrypt
- **Version Control**: Git, GitHub CLI

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

MIT License - see LICENSE file for details

## Author

Built for F1 race weekend coordination

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
