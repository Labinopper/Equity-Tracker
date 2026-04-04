# Deployment Guide

**Last Updated:** 2026-04-03

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Deployment Options](#deployment-options)
5. [Security Hardening](#security-hardening)
6. [Monitoring and Maintenance](#monitoring-and-maintenance)
7. [Troubleshooting](#troubleshooting)

## Prerequisites

### System Requirements

**Minimum:**
- Python 3.12 or higher
- 2 GB RAM
- 1 GB disk space (plus database size)
- Windows 10/11, macOS 10.15+, or Linux

**Recommended:**
- Python 3.12
- 4 GB RAM
- 5 GB disk space
- SSD for database performance

### Required Software

- **Python 3.12+**: [python.org](https://www.python.org/downloads/)
- **pip**: Included with Python
- **Git**: For cloning repository (optional)

### Optional Dependencies

- **Docker**: For containerized deployment
- **SQLCipher**: For database encryption (auto-installed)

## Installation

### Method 1: Local Installation

#### 1. Clone Repository

```bash
git clone https://github.com/your-org/equity-tracker.git
cd equity-tracker/equity_tracker
```

#### 2. Create Virtual Environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

#### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -e .
pip install sqlcipher3-binary  # For encryption
```

#### 4. Verify Installation

```bash
python -c "import src; print('Installation successful')"
```

### Method 2: Docker Deployment

#### 1. Build Image

```bash
cd equity_tracker
docker build -t equity-tracker .
```

#### 2. Create Data Volume

```bash
docker volume create equity-data
```

#### 3. Run Container

```bash
docker run -d \
  --name equity-tracker \
  -p 8000:8000 \
  -v equity-data:/data \
  --env-file .env \
  equity-tracker
```

## Configuration

### Environment Variables

Create a `.env` file in the `equity_tracker/` directory:

```bash
# Database Configuration
EQUITY_DB_PATH=C:/Users/you/portfolio.db
EQUITY_DB_PASSWORD=your-secure-passphrase-here
EQUITY_DB_ENCRYPTED=true

# Authentication
EQUITY_TOTP_SECRET=JBSWY3DPEHPK3PXP  # Generate with setup_totp.py
EQUITY_SECRET_KEY=your-secret-key-here  # Generate with secrets.token_hex(32)

# CORS Configuration
EQUITY_ALLOWED_ORIGINS=*  # Or specific origins: http://192.168.1.100:8000

# API Documentation (disable in production)
EQUITY_DOCS_ENABLED=false

# Development Mode (allows HTTP cookies)
EQUITY_DEV_MODE=false

# Beta Features (optional)
EQUITY_BETA_MODE=OFF  # OFF, OBSERVE_ONLY, SHADOW_ONLY, etc.
```

### Generate Secrets

#### TOTP Secret

```bash
cd equity_tracker
python scripts/setup_totp.py
```

**Output:**
```
TOTP Secret: JBSWY3DPEHPK3PXP
QR Code: [displays QR code]
Test Code: 123456
```

Scan QR code with authenticator app (Google Authenticator, Authy, etc.).

#### Session Secret Key

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**Output:**
```
a1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef123456
```

Use this value for `EQUITY_SECRET_KEY`.

### Database Setup

#### Option 1: Encrypted Database (Recommended)

```bash
# Set environment variables
export EQUITY_DB_PATH=/path/to/portfolio.db
export EQUITY_DB_PASSWORD=your-passphrase
export EQUITY_DB_ENCRYPTED=true

# Run migrations
cd equity_tracker
alembic upgrade head
```

#### Option 2: Plain SQLite (Development Only)

```bash
export EQUITY_DB_PATH=/path/to/portfolio.db
export EQUITY_DB_PASSWORD=any-value  # Ignored for plain SQLite
export EQUITY_DB_ENCRYPTED=false

alembic upgrade head
```

### Settings File

Settings are stored in `{db_path}.settings.json`:

```json
{
  "default_gross_income": "80000.00",
  "default_pension_sacrifice": "5000.00",
  "default_student_loan_plan": 2,
  "employer_ticker": "AAPL",
  "concentration_top_holding_alert_pct": "50.00",
  "concentration_employer_alert_pct": "40.00",
  "price_stale_after_days": 1,
  "fx_stale_after_minutes": 10,
  "broker_fee_model": "IBKR_UK_US_STOCK_FIXED"
}
```

**Note:** This file is **not encrypted** and should not contain sensitive data.

## Deployment Options

### Option 1: Local Development

**Use Case:** Development, testing, single-machine access

```bash
cd equity_tracker
python run_api.py
```

**Access:**
- Local: `http://localhost:8000`
- LAN: `http://YOUR_IP:8000` (find IP with `ipconfig` or `ifconfig`)

### Option 2: LAN Deployment

**Use Case:** Access from multiple devices on local network

#### Windows PowerShell

```powershell
# Set environment variables
$env:EQUITY_DB_PATH = "C:\Users\you\portfolio.db"
$env:EQUITY_DB_PASSWORD = "your-passphrase"
$env:EQUITY_TOTP_SECRET = "JBSWY3DPEHPK3PXP"
$env:EQUITY_SECRET_KEY = "your-secret-key"

# Run server
cd equity_tracker
python run_api.py
```

#### Linux/macOS

```bash
# Set environment variables
export EQUITY_DB_PATH=/home/you/portfolio.db
export EQUITY_DB_PASSWORD=your-passphrase
export EQUITY_TOTP_SECRET=JBSWY3DPEHPK3PXP
export EQUITY_SECRET_KEY=your-secret-key

# Run server
cd equity_tracker
python run_api.py
```

**Access from other devices:**
1. Find server IP: `ipconfig` (Windows) or `ifconfig` (Linux/macOS)
2. Access from browser: `http://SERVER_IP:8000`

### Option 3: Docker Deployment

**Use Case:** Containerized, portable deployment

#### Create `.env` File

```bash
# .env
EQUITY_DB_PATH=/data/portfolio.db
EQUITY_DB_PASSWORD=your-passphrase
EQUITY_DB_ENCRYPTED=true
EQUITY_TOTP_SECRET=JBSWY3DPEHPK3PXP
EQUITY_SECRET_KEY=your-secret-key
EQUITY_ALLOWED_ORIGINS=*
EQUITY_DOCS_ENABLED=false
EQUITY_DEV_MODE=false
```

#### Run Container

```bash
docker run -d \
  --name equity-tracker \
  --restart unless-stopped \
  -p 8000:8000 \
  -v equity-data:/data \
  --env-file .env \
  equity-tracker
```

#### Manage Container

```bash
# View logs
docker logs equity-tracker

# Stop container
docker stop equity-tracker

# Start container
docker start equity-tracker

# Remove container
docker rm -f equity-tracker
```

### Option 4: Windows Service (Advanced)

**Use Case:** Auto-start on Windows boot

#### Using NSSM (Non-Sucking Service Manager)

1. Download NSSM: [nssm.cc](https://nssm.cc/download)
2. Install service:

```powershell
nssm install EquityTracker "C:\path\to\venv\Scripts\python.exe" "C:\path\to\equity_tracker\run_api.py"
nssm set EquityTracker AppDirectory "C:\path\to\equity_tracker"
nssm set EquityTracker AppEnvironmentExtra EQUITY_DB_PATH=C:\path\to\portfolio.db EQUITY_DB_PASSWORD=your-passphrase
nssm start EquityTracker
```

## Security Hardening

### Database Encryption

**Always use SQLCipher in production:**

```bash
EQUITY_DB_ENCRYPTED=true
EQUITY_DB_PASSWORD=strong-passphrase-here
```

**Passphrase Requirements:**
- Minimum 20 characters
- Mix of letters, numbers, symbols
- Not stored in plain text
- Backed up securely

### HTTPS Configuration

**For production, use a reverse proxy (Caddy, nginx):**

#### Caddy Example

```caddyfile
# Caddyfile
your-domain.com {
    reverse_proxy localhost:8000
    tls your-email@example.com
}
```

Run Caddy:
```bash
caddy run --config Caddyfile
```

#### nginx Example

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### CORS Configuration

**Restrict origins in production:**

```bash
# Development (allow all)
EQUITY_ALLOWED_ORIGINS=*

# Production (specific origins)
EQUITY_ALLOWED_ORIGINS=https://your-domain.com,https://app.your-domain.com
```

### Rate Limiting

**Built-in rate limiting protects authentication:**

- Login: 5 requests per minute per IP
- Other endpoints: Configurable per route

### Session Security

**Session cookies are:**
- HTTP-only (not accessible to JavaScript)
- Signed with `EQUITY_SECRET_KEY`
- Secure flag (HTTPS-only) when `EQUITY_DEV_MODE=false`

### Firewall Configuration

**Restrict access to port 8000:**

```bash
# Windows Firewall
netsh advfirewall firewall add rule name="Equity Tracker" dir=in action=allow protocol=TCP localport=8000

# Linux (ufw)
sudo ufw allow 8000/tcp

# Linux (iptables)
sudo iptables -A INPUT -p tcp --dport 8000 -j ACCEPT
```

## Monitoring and Maintenance

### Health Check

```bash
curl http://localhost:8000/health
```

**Response:**
```json
{
  "status": "healthy",
  "database": "connected",
  "version": "0.1.0"
}
```

### Logs

**Application logs:**
```bash
# Docker
docker logs equity-tracker

# Local
# Logs printed to stdout/stderr
```

**Log levels:**
- `INFO`: Normal operations
- `WARNING`: Potential issues
- `ERROR`: Error conditions
- `CRITICAL`: Critical failures

### Database Backup

**Backup encrypted database:**

```bash
# Stop application first
# Copy database file
cp portfolio.db portfolio.db.backup.$(date +%Y%m%d)

# Verify backup
ls -lh portfolio.db.backup.*
```

**Automated backup script (Linux/macOS):**

```bash
#!/bin/bash
# backup.sh

DB_PATH="/path/to/portfolio.db"
BACKUP_DIR="/path/to/backups"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"
cp "$DB_PATH" "$BACKUP_DIR/portfolio.db.$DATE"

# Keep only last 30 days
find "$BACKUP_DIR" -name "portfolio.db.*" -mtime +30 -delete
```

**Schedule with cron:**
```bash
# Daily backup at 2 AM
0 2 * * * /path/to/backup.sh
```

### Database Migrations

**Check current version:**
```bash
cd equity_tracker
alembic current
```

**Upgrade to latest:**
```bash
alembic upgrade head
```

**Downgrade one version:**
```bash
alembic downgrade -1
```

### Performance Monitoring

**Monitor database size:**
```bash
# Linux/macOS
du -h portfolio.db

# Windows
dir portfolio.db
```

**Monitor memory usage:**
```bash
# Docker
docker stats equity-tracker

# Linux
ps aux | grep python

# Windows
tasklist | findstr python
```

## Troubleshooting

### Common Issues

#### 1. Database Locked

**Symptom:** `database is locked` error

**Solution:**
```bash
# Check for other processes
# Windows
tasklist | findstr python

# Linux/macOS
ps aux | grep python

# Kill duplicate processes
# Then restart application
```

#### 2. Authentication Fails

**Symptom:** TOTP code rejected

**Solution:**
1. Verify `EQUITY_TOTP_SECRET` matches authenticator app
2. Check system time is synchronized
3. Regenerate TOTP secret if needed:
   ```bash
   python scripts/setup_totp.py
   ```

#### 3. Database Encryption Error

**Symptom:** `file is not a database` or `file is encrypted`

**Solution:**
1. Verify `EQUITY_DB_PASSWORD` is correct
2. Check `EQUITY_DB_ENCRYPTED=true` is set
3. If password lost, database cannot be recovered (encryption working as designed)

#### 4. Port Already in Use

**Symptom:** `Address already in use` error

**Solution:**
```bash
# Find process using port 8000
# Windows
netstat -ano | findstr :8000

# Linux/macOS
lsof -i :8000

# Kill process or change port in run_api.py
```

#### 5. Module Import Errors

**Symptom:** `ModuleNotFoundError`

**Solution:**
```bash
# Reinstall dependencies
pip install --upgrade pip
pip install -e .
pip install sqlcipher3-binary
```

#### 6. SQLCipher Not Found

**Symptom:** `ImportError: No module named 'sqlcipher3'`

**Solution:**
```bash
# Install pre-built binary
pip install sqlcipher3-binary

# Or build from source (requires build tools)
pip install sqlcipher3
```

### Debug Mode

**Enable verbose logging:**

```python
# In run_api.py, change log_level
config = uvicorn.Config(
    app,
    host="0.0.0.0",
    port=8000,
    workers=1,
    reload=False,
    log_level="debug",  # Changed from "info"
)
```

### Getting Help

1. Check logs for error messages
2. Review [Architecture Overview](./01-ARCHITECTURE-OVERVIEW.md)
3. Consult [API Reference](./03-API-REFERENCE.md)
4. Check [Developer Onboarding](./07-DEVELOPER-ONBOARDING.md)

## Related Documentation

- [Architecture Overview](./01-ARCHITECTURE-OVERVIEW.md) - System design
- [Database Schema](./02-DATABASE-SCHEMA.md) - Database structure
- [Developer Onboarding](./07-DEVELOPER-ONBOARDING.md) - Development setup
- [Beta Features](./05-BETA-FEATURES.md) - Beta configuration