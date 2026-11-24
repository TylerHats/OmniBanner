#!/bin/bash
# OmniBanner Central Service Installer
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0;0m'

echo -e "${BLUE}===============================================${NC}"
echo -e "${BLUE}       OmniBanner Central Service Installer    ${NC}"
echo -e "${BLUE}===============================================${NC}"

REPO_RAW_URL=${1:-"https://raw.githubusercontent.com/TylerHats/OmniBanner/main"}

echo -e "\n${BLUE}[1/5] Creating directory structures...${NC}"
mkdir -p service/config service/data service/static/css service/static/js service/static/brand service/static/uploads service/templates
echo -e "${GREEN}Directories created successfully.${NC}"

echo -e "\n${BLUE}[2/5] Downloading service files...${NC}"

FILES=(
    "service/requirements.txt"
    "service/main.py"
    "service/database.py"
    "service/config_store.py"
    "service/schemas.py"
    "service/auth.py"
    "service/smtp.py"
    "service/kuma.py"
    "service/webhooks.py"
    "service/analytics_buffer.py"
    "service/config/brand.json"
    "service/static/css/dashboard.css"
    "service/static/js/dashboard.js"
    "service/static/js/dashboard_app.js"
    "service/static/js/embed.js"
    "service/static/js/marked.min.js"
    "service/static/js/qrious.min.js"
    "service/templates/base.html"
    "service/templates/setup.html"
    "service/templates/login.html"
    "service/templates/totp_setup.html"
    "service/templates/dashboard.html"
    "service/templates/pair_info.html"
    "service/templates/unsubscribe.html"
    "service/templates/status.html"
)

for file in "${FILES[@]}"; do
    echo -e "Downloading ${file}..."
    curl -sSL "${REPO_RAW_URL}/${file}" -o "${file}" || {
        echo -e "${RED}Warning: Failed to fetch ${file} from repository.${NC}"
    }
done

if [ -f "service/config/logo.png" ]; then
    cp service/config/logo.png service/static/brand/logo.png
else
    echo -e "Downloading brand logo..."
    curl -sSL "${REPO_RAW_URL}/service/static/brand/logo.png" -o service/static/brand/logo.png || true
fi

echo -e "\n${BLUE}[3/5] Setting up Python virtual environment...${NC}"
cd service

if [ ! -d ".venv" ]; then
    if python3 -m venv .venv 2>/dev/null; then
        echo -e "${GREEN}Virtual environment created.${NC}"
    else
        echo -e "${RED}python3-venv ensurepip missing. Bootstrapping pip...${NC}"
        python3 -m venv --without-pip .venv
        curl -sSL https://bootstrap.pypa.io/get-pip.py -o get-pip.py
        .venv/bin/python3 get-pip.py
        rm get-pip.py
    fi
fi

echo -e "\n${BLUE}[4/5] Installing dependencies...${NC}"
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo -e "\n${BLUE}[5/5] Creating run.sh script...${NC}"
cat << 'EOF' > run.sh
#!/bin/bash
set -e
RELOAD_FLAG=""
if [ "${OMNIBANNER_RELOAD:-0}" = "1" ]; then
    RELOAD_FLAG="--reload"
fi
exec .venv/bin/uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*" $RELOAD_FLAG
EOF
chmod +x run.sh

echo -e "\n${GREEN}===============================================${NC}"
echo -e "${GREEN}         Installation Completed Successfully!   ${NC}"
echo -e "${GREEN}===============================================${NC}"
echo -e "To start the central service:"
echo -e "  ${BLUE}cd service && ./run.sh${NC}"
echo -e "Then open http://localhost:8000 to complete the setup wizard."
