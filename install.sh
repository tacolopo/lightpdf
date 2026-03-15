#!/bin/bash
# LightPDF – install dependencies
set -e

echo "=== System packages ==="
sudo apt install -y \
    python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
    libgirepository1.0-dev python3-dev python3-pip python3-venv \
    opensc pcscd 2>/dev/null || true

echo ""
echo "=== Python virtual environment ==="
# Use system python3 (not conda/pyenv) so GTK bindings are available
SYS_PY="$(command -v /usr/bin/python3 || command -v python3)"
"$SYS_PY" -m venv --system-site-packages .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "============================================"
echo "  Setup complete!"
echo ""
echo "  Run with:"
echo "    source .venv/bin/activate"
echo "    python run.py [file.pdf]"
echo "============================================"
