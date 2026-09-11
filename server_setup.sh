#!/usr/bin/env bash
# ==============================================================================
# Automated Setup Script for Ubuntu 22.04 / 24.04 (Oracle Cloud / DigitalOcean)
# ==============================================================================
set -e

echo "=================================================="
echo "🚀 Starting FB Scraper Ubuntu Cloud Server Setup..."
echo "=================================================="

# 1. Update system packages
echo "📦 Updating APT packages..."
sudo apt update -y && sudo apt upgrade -y

# 2. Install essential system dependencies (Python, Virtual Display, TMUX)
echo "📦 Installing Python, Virtual Screen (Xvfb), and TMUX..."
sudo apt install -y python3 python3-pip python3-venv git curl wget xvfb tmux libasound2t64 libasound2 2>/dev/null || sudo apt install -y python3 python3-pip python3-venv git curl wget xvfb tmux

# 3. Create Python Virtual Environment
if [ ! -d "venv" ]; then
    echo "⚡ Creating Python virtual environment..."
    python3 -m venv venv
else
    echo "✅ Existing virtual environment found."
fi

# 4. Install Python dependencies
echo "📦 Installing Python requirements..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# 5. Install Playwright Chromium & system browser libraries
echo "🌐 Installing Playwright Chromium browser & OS libraries..."
./venv/bin/playwright install chromium
sudo ./venv/bin/playwright install-deps chromium || true

# 6. Verify .env file exists
if [ ! -f ".env" ]; then
    echo "⚠️ Warning: .env file not found!"
    echo "Please copy .env.example to .env and fill in your keys:"
    echo "cp .env.example .env && nano .env"
else
    echo "✅ .env file detected."
fi

echo ""
echo "=================================================="
echo "✅ Server Setup Complete!"
echo "=================================================="
echo "To start your 24/7 scraper inside a persistent TMUX session:"
echo ""
echo "  1) Start session:   tmux new -s fb_scraper"
echo "  2) Run scraper:     xvfb-run -a ./venv/bin/python continuous_scraper.py"
echo "  3) Detach session:  Press [Ctrl + B], then press [D]"
echo ""
echo "You can now safely disconnect SSH and turn off your Mac!"
echo "=================================================="
