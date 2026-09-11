#!/usr/bin/env bash

# Exit on unexpected failures
set -e

# Project directory
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# Preferred Python binary (favor 3.13 or 3.12 over experimental 3.14 on macOS)
find_python() {
    if command -v python3.13 >/dev/null 2>&1; then
        echo "python3.13"
    elif command -v python3.12 >/dev/null 2>&1; then
        echo "python3.12"
    elif command -v python3.11 >/dev/null 2>&1; then
        echo "python3.11"
    else
        echo "python3"
    fi
}

# Ensure virtual environment exists and is healthy
ensure_venv() {
    if [ ! -f "venv/bin/activate" ]; then
        echo "⚡ Virtual environment not found or broken. Creating a fresh virtual environment..."
        PYTHON_CMD=$(find_python)
        echo "Using Python: $PYTHON_CMD ($($PYTHON_CMD --version))"
        rm -rf venv
        "$PYTHON_CMD" -m venv venv
        ./venv/bin/python -m pip install --upgrade pip
    fi
    # Install requirements on first run and again whenever requirements.txt changes
    if ! cmp -s requirements.txt venv/.requirements.installed; then
        echo "📦 Installing project dependencies..."
        ./venv/bin/python -m pip install -r requirements.txt
        echo "🌐 Installing Playwright Chromium browser..."
        ./venv/bin/python -m playwright install chromium
        cp requirements.txt venv/.requirements.installed
        echo "✅ Environment setup complete!"
    fi
}

# Function to run components
run_setup() {
    ensure_venv
    echo "🔑 Starting Facebook account login session setup..."
    ./venv/bin/python setup_sessions.py
}

run_scraper() {
    ensure_venv
    echo "🚀 Starting scraper + auto-messaging (restarts by itself after a crash, Ctrl+C to stop)..."
    trap 'echo; echo "👋 Scraper stopped."; exit 0' INT TERM
    while true; do
        # Exit code 0 = stopped on purpose (e.g. an account needs logging in) - don't restart
        if ./venv/bin/python continuous_scraper.py; then
            break
        fi
        echo "⚠️ Scraper crashed - restarting in 60 seconds..."
        sleep 60
    done
}

run_crm() {
    ensure_venv
    echo "📱 Launching Streamlit CRM Dashboard..."
    ./venv/bin/python -m streamlit run app.py
}

reinstall_deps() {
    echo "🔄 Reinstalling environment and dependencies..."
    rm -rf venv
    ensure_venv
    echo "✅ Reinstall complete!"
}

PLIST="$HOME/Library/LaunchAgents/com.dealhunter.scraper.plist"

autostart_on() {
    if [ "$(uname)" != "Darwin" ]; then
        echo "Auto-start from this menu is for macOS. On a Linux server, use tmux as shown by server_setup.sh."
        return
    fi
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.dealhunter.scraper</string>
    <key>ProgramArguments</key>
    <array><string>/bin/bash</string><string>$PROJECT_DIR/run.sh</string><string>scraper</string></array>
    <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>$PROJECT_DIR/scraper.log</string>
    <key>StandardErrorPath</key><string>$PROJECT_DIR/scraper.log</string>
</dict>
</plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load -w "$PLIST"
    echo "✅ Auto-start is ON: the scraper starts now and every time you log in (log file: $PROJECT_DIR/scraper.log)."
}

autostart_off() {
    if [ -f "$PLIST" ]; then
        launchctl unload -w "$PLIST" 2>/dev/null || true
        rm -f "$PLIST"
    fi
    echo "✅ Auto-start is OFF."
}

# Handle command line argument if provided
ACTION="${1:-}"

case "$ACTION" in
    setup)
        run_setup
        exit 0
        ;;
    scraper|scrape|outreach|worker)
        run_scraper
        exit 0
        ;;
    crm|dashboard|app)
        run_crm
        exit 0
        ;;
    install|reinstall)
        reinstall_deps
        exit 0
        ;;
    autostart)
        autostart_on
        exit 0
        ;;
    autostart-off)
        autostart_off
        exit 0
        ;;
    "")
        # Interactive menu if no argument given
        ensure_venv
        echo ""
        echo "=================================================="
        echo "     📱 FB Marketplace Scraper & CRM Runner       "
        echo "=================================================="
        echo "1) Log in Facebook Accounts (setup_sessions.py)"
        echo "2) Run Scraper + Auto-Messaging, all accounts (continuous_scraper.py)"
        echo "3) Launch Streamlit CRM Dashboard (app.py)"
        echo "4) Turn ON auto-start at login (scraper starts by itself)"
        echo "5) Turn OFF auto-start"
        echo "6) Reinstall / Update Dependencies"
        echo "7) Exit"
        echo "=================================================="
        read -p "Select an option [1-7]: " choice

        case "$choice" in
            1) run_setup ;;
            2) run_scraper ;;
            3) run_crm ;;
            4) autostart_on ;;
            5) autostart_off ;;
            6) reinstall_deps ;;
            7) echo "Exiting."; exit 0 ;;
            *) echo "Invalid option."; exit 1 ;;
        esac
        ;;
    *)
        echo "Usage: ./run.sh [setup|scraper|crm|autostart|autostart-off|install]"
        exit 1
        ;;
esac
