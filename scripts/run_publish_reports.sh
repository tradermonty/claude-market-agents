#!/bin/bash
# Publish Reports to GitHub Pages
# Generates index.html and pushes all HTML reports to gh-pages branch.
# Usage: ./scripts/run_publish_reports.sh [--dry-run] [--no-push]

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Set PATH for cron environment
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:${HOME}/.npm-global/bin:${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"

# Configuration
LOG_DIR="${PROJECT_DIR}/logs"
LOG_FILE="${LOG_DIR}/publish_reports_$(date +%Y-%m-%d).log"

# Create log directory if it doesn't exist
mkdir -p "${LOG_DIR}"

# Set working directory
cd "${PROJECT_DIR}" || exit 1

# Log start time
echo "=======================================" >> "${LOG_FILE}"
echo "Publish Reports - Started: $(date)" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

# Run publish script, forwarding any CLI arguments
python3 "${SCRIPT_DIR}/publish_reports.py" "$@" >> "${LOG_FILE}" 2>&1

# Capture exit status
EXIT_STATUS=$?

# Log completion
echo "" >> "${LOG_FILE}"
echo "Completed: $(date)" >> "${LOG_FILE}"
echo "Exit Status: ${EXIT_STATUS}" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

exit ${EXIT_STATUS}
