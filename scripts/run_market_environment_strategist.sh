#!/bin/bash
# Market Environment Strategist - Command Line Script
# Usage: ./run_market_environment_strategist.sh
# Generates comprehensive market environment analysis report

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Set PATH for cron environment (node, npm, homebrew, etc.)
# Adjust these paths based on your system configuration
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:${HOME}/.npm-global/bin:${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"

# Configuration
LOG_DIR="${PROJECT_DIR}/logs"
LOG_FILE="${LOG_DIR}/market_environment_$(date +%Y-%m-%d).log"

# Create log directory if it doesn't exist
mkdir -p "${LOG_DIR}"

# Set working directory
cd "${PROJECT_DIR}" || exit 1

# Log start time
echo "=======================================" >> "${LOG_FILE}"
echo "Market Environment Strategist - Started: $(date)" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

# Run Claude Code with the market-environment-strategist agent
claude -p "Analyze the current market environment using the market-environment-strategist agent. Gather comprehensive market data from MCP servers (indices, sectors, volatility, breadth metrics), assess market conditions across multiple timeframes, identify the current market regime, and generate a detailed HTML strategic outlook report saved to /reports/ directory with the filename format: market_environment_$(date +%Y-%m-%d).html" \
  --dangerously-skip-permissions \
  >> "${LOG_FILE}" 2>&1

# Capture exit status
EXIT_STATUS=$?

# Log completion
echo "" >> "${LOG_FILE}"
echo "Completed: $(date)" >> "${LOG_FILE}"
echo "Exit Status: ${EXIT_STATUS}" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

# Display result
if [ ${EXIT_STATUS} -eq 0 ]; then
    echo "Market environment analysis complete. Check /reports/ for the HTML report."
else
    echo "Analysis failed. Check log: ${LOG_FILE}"
fi

exit ${EXIT_STATUS}
