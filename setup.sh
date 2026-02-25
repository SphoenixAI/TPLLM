#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

ENV_NAME="lltpm"
PYTHON_VERSION="3.11"

echo "=========================================="
echo "  LLTPM — AI Technical Program Manager"
echo "=========================================="
echo ""

# ── 1. Create / activate conda environment ──────────────
echo "[1/5] Setting up Python environment..."

if command -v conda &>/dev/null; then
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
else
    echo "  conda not found — falling back to venv"
    ACTUAL_PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo "$ACTUAL_PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$ACTUAL_PY_VERSION" | cut -d. -f2)

    if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
        echo ""
        echo "  ERROR: Python >=3.11 required (found $ACTUAL_PY_VERSION)"
        echo ""
        echo "  Install conda to get the right Python version automatically:"
        echo "    curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-$(uname -s)-$(uname -m).sh -o miniconda.sh"
        echo "    bash miniconda.sh -b -p \$HOME/miniconda3"
        echo "    eval \"\$(\$HOME/miniconda3/bin/conda shell.bash hook)\""
        echo "    conda init"
        echo ""
        echo "  Then re-run: bash setup.sh"
        exit 1
    fi

    if [ -d ".venv" ]; then
        echo "  venv already exists, activating..."
    else
        echo "  Creating venv (.venv) with Python $ACTUAL_PY_VERSION..."
        python3 -m venv .venv
    fi
    source .venv/bin/activate
    echo "  venv active (Python $ACTUAL_PY_VERSION) ✓"
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
