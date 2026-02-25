#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

ENV_NAME="lltpm"
PYTHON_VERSION="3.11"
MINICONDA_DIR="$HOME/miniconda3"

echo "=========================================="
echo "  LLTPM — AI Technical Program Manager"
echo "=========================================="
echo ""

# ── Helper: install miniconda ────────────────────────────
install_miniconda() {
    echo "  Installing Miniconda..."
    local OS
    OS="$(uname -s)"

    if [ "$OS" = "Darwin" ]; then
        # macOS — use Homebrew
        if ! command -v brew &>/dev/null; then
            echo "  Homebrew not found — installing Homebrew first..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        fi
        echo "  Running: brew install miniconda"
        brew install miniconda
        # Homebrew installs conda to its own prefix
        CONDA_EXE="$(brew --prefix)/Caskroom/miniconda/base/bin/conda"
        if [ ! -x "$CONDA_EXE" ]; then
            # Newer brew versions install as a formula
            CONDA_EXE="$(brew --prefix miniconda)/bin/conda"
        fi
        eval "$("$CONDA_EXE" shell.bash hook)"
        # Init conda for the user's actual shell (zsh on modern macOS)
        local USER_SHELL
        USER_SHELL="$(basename "$SHELL")"
        conda init "$USER_SHELL" --quiet >/dev/null 2>&1 || true
        echo "  Ran 'conda init $USER_SHELL' — restart your terminal after setup"
    else
        # Linux — download installer
        local ARCH URL INSTALLER
        ARCH="$(uname -m)"
        case "$ARCH" in
            x86_64)  ARCH="x86_64" ;;
            aarch64) ARCH="aarch64" ;;
            arm64)   ARCH="aarch64" ;;
            *)       echo "  ERROR: Unsupported architecture: $ARCH"; exit 1 ;;
        esac

        URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-${ARCH}.sh"
        echo "  Downloading from $URL ..."

        INSTALLER="$(mktemp /tmp/miniconda-XXXXXX.sh)"
        if command -v curl &>/dev/null; then
            curl -fsSL "$URL" -o "$INSTALLER"
        elif command -v wget &>/dev/null; then
            wget -q "$URL" -O "$INSTALLER"
        else
            echo "  ERROR: Neither curl nor wget found. Install one and re-run."
            exit 1
        fi

        bash "$INSTALLER" -b -p "$MINICONDA_DIR"
        rm -f "$INSTALLER"

        eval "$("$MINICONDA_DIR/bin/conda" shell.bash hook)"
        local USER_SHELL
        USER_SHELL="$(basename "$SHELL")"
        conda init "$USER_SHELL" --quiet >/dev/null 2>&1 || true
    fi
    echo "  Miniconda installed ✓"
}

# ── Helper: check if system Python >= 3.11 ───────────────
system_python_ok() {
    if ! command -v python3 &>/dev/null; then
        return 1
    fi
    local PY_MAJOR PY_MINOR
    PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
    PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
    [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 11 ]
}

# ── 1. Set up Python environment ─────────────────────────
echo "[1/5] Setting up Python environment..."

USE_CONDA=false

# Check for existing conda install (including our own miniconda)
if command -v conda &>/dev/null; then
    USE_CONDA=true
elif [ -x "$MINICONDA_DIR/bin/conda" ]; then
    eval "$("$MINICONDA_DIR/bin/conda" shell.bash hook)"
    USE_CONDA=true
fi

if $USE_CONDA; then
    # Source conda shell hooks so activate works in scripts
    CONDA_BASE="$(conda info --base)"
    source "$CONDA_BASE/etc/profile.d/conda.sh"

    if conda env list | grep -q "^${ENV_NAME} "; then
        echo "  Conda env '$ENV_NAME' already exists, activating..."
        conda activate "$ENV_NAME"
    else
        echo "  Creating conda env '$ENV_NAME' (Python $PYTHON_VERSION)..."
        conda create -y -n "$ENV_NAME" python="$PYTHON_VERSION" --quiet
        conda activate "$ENV_NAME"
    fi
    echo "  Conda env '$ENV_NAME' active ✓"
elif system_python_ok; then
    # System Python is fine — use venv
    ACTUAL_PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    echo "  conda not found, but Python $ACTUAL_PY_VERSION is sufficient — using venv"

    if [ -d ".venv" ]; then
        echo "  venv already exists, activating..."
    else
        echo "  Creating venv (.venv)..."
        python3 -m venv .venv
    fi
    source .venv/bin/activate
    echo "  venv active (Python $ACTUAL_PY_VERSION) ✓"
else
    # No conda, no suitable Python — install miniconda automatically
    ACTUAL_PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "none")
    echo "  conda not found, system Python ($ACTUAL_PY_VERSION) < 3.11"
    echo "  Miniconda is required — installing automatically..."
    echo ""
    install_miniconda

    # Now create the conda env
    echo "  Creating conda env '$ENV_NAME' (Python $PYTHON_VERSION)..."
    conda create -y -n "$ENV_NAME" python="$PYTHON_VERSION" --quiet
    conda activate "$ENV_NAME"
    echo "  Conda env '$ENV_NAME' active ✓"
fi

ACTIVE_PY=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  Python $ACTIVE_PY ✓"

# ── 2. Install package + dev deps ────────────────────────
echo ""
echo "[2/5] Installing LLTPM and dependencies..."
pip install -e ".[dev,dashboard]" --quiet 2>&1 | tail -3
echo "  Package installed ✓"

# ── 3. Verify PyJWT ──────────────────────────────────────
echo ""
echo "[3/5] Verifying PyJWT..."
python3 -c "import jwt; print(f'  PyJWT {jwt.__version__} ✓')" 2>/dev/null || {
    echo "  Installing PyJWT..."
    pip install "PyJWT>=2.11.0" --quiet
    python3 -c "import jwt; print(f'  PyJWT {jwt.__version__} ✓')"
}

# ── 4. Run tests ─────────────────────────────────────────
echo ""
echo "[4/5] Running test suite..."
TEST_OUTPUT=$(python3 -m pytest tests/ -q 2>&1)
TEST_RESULT=$?
PASS_COUNT=$(echo "$TEST_OUTPUT" | tail -1)

if [ $TEST_RESULT -eq 0 ]; then
    echo "  $PASS_COUNT ✓"
else
    echo "  TESTS FAILED:"
    echo "$TEST_OUTPUT" | tail -20
    echo ""
    echo "  Fix test failures before proceeding."
    exit 1
fi

# ── 5. Quick smoke test ──────────────────────────────────
echo ""
echo "[5/5] Smoke test — JWT auth flow..."
python3 -c "
from lltpm.workflows.security_manager import SecurityManager
from lltpm.artifacts.store import ArtifactStore
from lltpm.models.security import Role
from pathlib import Path
import tempfile

store = ArtifactStore(Path(tempfile.mkdtemp()))
mgr = SecurityManager(store)

# Create API key
key, raw = mgr.create_api_key('setup-test', Role.ADMIN, owner='setup')

# Exchange for JWT
pair = mgr.authenticate_api_key_to_jwt(raw)
assert pair is not None, 'Token pair creation failed'

# Validate
claims = mgr.validate_jwt(pair.access_token)
assert claims is not None, 'Token validation failed'
assert claims.role == Role.ADMIN

# Refresh
new_pair = mgr.refresh_access_token(pair.refresh_token)
assert new_pair is not None, 'Token refresh failed'

perms = mgr.get_role_permissions(claims.role)
print(f'  API key → JWT → validate → refresh: all passed')
print(f'  Role: {claims.role.value}, Permissions: {len(perms)}')
"
echo ""

# ── Done ─────────────────────────────────────────────────
echo "=========================================="
echo "  Setup complete!"
echo "=========================================="
echo ""
echo "Environment: $(if command -v conda &>/dev/null && conda env list | grep -q "^${ENV_NAME} "; then echo "conda ($ENV_NAME)"; else echo "venv (.venv)"; fi)"
echo ""
echo "Activate the environment in new terminals:"
if command -v conda &>/dev/null && conda env list | grep -q "^${ENV_NAME} "; then
    echo "  conda activate $ENV_NAME"
else
    echo "  source .venv/bin/activate"
fi
echo ""
echo "Quick start commands:"
echo ""
echo "  python -m lltpm init        # Setup wizard (configure integrations)"
echo "  python -m lltpm welcome     # Onboarding overview"
echo "  python -m lltpm status      # System status"
echo "  python -m lltpm tools       # List all available tools"
echo "  python -m lltpm chat        # Interactive agent chat"
echo "  python -m lltpm dashboard   # Launch web dashboard"
echo ""
echo "Dashboard API (separate terminal):"
echo ""
echo "  uvicorn lltpm.dashboard:app --reload --port 8080"
echo "  # Then open http://localhost:8080/docs"
echo ""
echo "Run tests anytime:"
echo ""
echo "  python -m pytest tests/ -v"
echo ""
