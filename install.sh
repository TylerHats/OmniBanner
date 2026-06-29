#!/bin/bash
# OmniBanner Central Service Installer
# This script deploys the central OmniBanner service in a standalone directory,
# automates python virtual environments, and installs all dependencies securely.

set -e

# Terminal Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0;0m' # No Color

echo -e "${BLUE}===============================================${NC}"
echo -e "${BLUE}       OmniBanner Central Service Installer    ${NC}"
echo -e "${BLUE}===============================================${NC}"

# Target Repository Config
REPO_RAW_URL=${1:-"https://raw.githubusercontent.com/TylerHats/OmniBanner/main"}

# Create directory structure
echo -e "\n${BLUE}[1/5] Creating directory structures...${NC}"
mkdir -p service/config service/static/css service/static/js service/static/brand service/templates
echo -e "${GREEN}Directories created successfully.${NC}"

# Download source files from the Git repository
echo -e "\n${BLUE}[2/5] Downloading service files...${NC}"

FILES=(
    "service/requirements.txt"
    "service/main.py"
    "service/database.py"
    "service/auth.py"
    "service/smtp.py"
    "service/kuma.py"
    "service/config/brand.json"
    "service/static/css/dashboard.css"
    "service/static/js/dashboard.js"
    "service/templates/base.html"
    "service/templates/setup.html"
    "service/templates/login.html"
    "service/templates/totp_setup.html"
    "service/templates/dashboard.html"
)

# Download each file
for file in "${FILES[@]}"; do
    echo -e "Downloading ${file}..."
    curl -sSL "${REPO_RAW_URL}/${file}" -o "${file}" || {
        echo -e "${RED}Warning: Failed to fetch ${file} from repository.${NC}"
    }
done

# Download QR code library
echo -e "Downloading static/js/qrious.min.js..."
curl -sSL "https://cdnjs.cloudflare.com/ajax/libs/qrious/4.0.2/qrious.min.js" -o service/static/js/qrious.min.js

# Copy default brand logo if present in config, otherwise placeholder download
if [ -f "service/config/logo.png" ]; then
    cp service/config/logo.png service/static/brand/logo.png
else
    echo -e "Downloading brand logo fallback..."
    curl -sSL "${REPO_RAW_URL}/service/static/brand/logo.png" -o service/static/brand/logo.png || {
        echo -e "${RED}Warning: Could not fetch brand logo. An onboarding logo upload will be required.${NC}"
    }
fi

# Setup Virtual Environment
echo -e "\n${BLUE}[3/5] Setting up Python virtual environment (PEP 668 bypass)...${NC}"
cd service

if [ ! -d ".venv" ]; then
    echo "Initializing virtual environment..."
    # Attempt standard venv
    if python3 -m venv .venv 2>/dev/null; then
        echo -e "${GREEN}Virtual environment created.${NC}"
    else
        echo -e "${RED}Python ensurepip missing. Bootstrapping virtual environment manually...${NC}"
        # Create without pip, then download get-pip
        python3 -m venv --without-pip .venv
        curl -sSL https://bootstrap.pypa.io/get-pip.py -o get-pip.py
        .venv/bin/python3 get-pip.py
        rm get-pip.py
        echo -e "${GREEN}Bootstrapped virtual environment with pip successfully.${NC}"
    fi
fi

# Install requirements
echo -e "\n${BLUE}[4/5] Installing dependencies...${NC}"
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
echo -e "${GREEN}Dependencies installed successfully.${NC}"

# Create run script
echo -e "\n${BLUE}[5/5] Creating run.sh script...${NC}"
cat << 'EOF' > run.sh
#!/bin/bash
echo "Starting OmniBanner backend server..."
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
EOF
chmod +x run.sh

echo -e "\n${GREEN}===============================================${NC}"
echo -e "${GREEN}         Installation Completed Successfully!   ${NC}"
echo -e "${GREEN}===============================================${NC}"
echo -e "To start the central service, run:"
echo -e "  ${BLUE}cd service && ./run.sh${NC}"
echo -e "And then configure branding and admins at http://localhost:8000"
echo -e "==============================================="
