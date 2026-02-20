#!/bin/bash

# Silicon Swarm 4G Runner Script
# This script initializes the workspace and runs the agent swarm

echo "🚀 Silicon Swarm 4G - Autonomous LTE Baseband Design"
echo "=================================================="

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check if Python is available
if ! command -v python &> /dev/null; then
    echo "❌ Python is not installed or not in PATH"
    exit 1
fi

# Check Python version (should be 3.8+)
PYTHON_VERSION=$(python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"; then
    echo "✅ Python $PYTHON_VERSION detected"
else
    echo "❌ Python 3.8+ required, found $PYTHON_VERSION"
    exit 1
fi

# Check if required packages are installed
echo "📦 Checking dependencies..."
python -c "import langgraph" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "❌ langgraph package not found. Installing..."
    pip install langgraph
    if [ $? -ne 0 ]; then
        echo "❌ Failed to install langgraph. Please install manually: pip install langgraph"
        exit 1
    fi
fi

echo "✅ Dependencies OK"

# Run the initialization script if directories don't exist
if [ ! -d "tasks" ] || [ ! -d "specs" ] || [ ! -d "rtl" ]; then
    echo "🔧 Running workspace initialization..."
    ./init_workspace.sh
fi

# Run the swarm
echo ""
echo "🤖 Starting the Silicon Swarm 4G..."
echo "=================================="
python silicon_swarm_4g.py

echo ""
echo "🏁 Swarm execution completed!"
echo "Check the output above for results."