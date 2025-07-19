#!/bin/bash

# Description:
# Removes ', USE_ASYNC=True' from any line containing
# 'async with get_mysql_conn_ctx(...)' while preserving indentation and syntax

echo "Scanning Python files and fixing 'USE_ASYNC=True' in async DB context lines..."

find . -type f -name "*main_35_fix1.py" | while read -r file; do
    sed -i -E 's/(async[[:space:]]+with[[:space:]]+get_mysql_conn_ctx\([^)]*),[[:space:]]*USE_ASYNC=True/\1/' "$file"
done

echo "✅ Done. All matching lines cleaned."
