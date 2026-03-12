#!/bin/bash
cd /Users/djscruggs/VSCode/jobsearch
/opt/anaconda3/bin/uv run python main.py >> /tmp/jobhunter.log 2>&1
EXIT_CODE=$?

# Count new queued jobs from DB
NEW_QUEUED=$(/opt/anaconda3/bin/uv run python -c "
from utils.db import get_conn
with get_conn() as conn:
    n = conn.execute(\"SELECT COUNT(*) FROM jobs WHERE status='queued'\").fetchone()[0]
    print(n)
" 2>/dev/null)

if [ $EXIT_CODE -eq 0 ]; then
    osascript -e "display notification \"Pipeline complete. ${NEW_QUEUED} jobs queued for review.\" with title \"Job Hunter\" sound name \"Glass\""
else
    osascript -e "display notification \"Pipeline failed — check /tmp/jobhunter.log\" with title \"Job Hunter\" sound name \"Basso\""
fi
