#!/bin/bash

# MySQL credentials
MYSQL_USER="new_user"
MYSQL_PASSWORD="user_password"
MYSQL_HOST="localhost"

# Output file
OUTPUT_FILE="rm_databases_dump.sql"

# Fetch databases matching the pattern
DATABASES=$(mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" -h"$MYSQL_HOST" -e "SHOW DATABASES LIKE 'rm%db';" -s --skip-column-names)

# Check if databases were found
if [ -z "$DATABASES" ]; then
    echo "No databases found matching the pattern 'rm%db'."
    exit 1
fi

# Dump each database into the output file
for DB in $DATABASES; do
    echo "Dumping database: $DB"
    mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" -h"$MYSQL_HOST" --databases "$DB" >> "$OUTPUT_FILE"
done

echo "All databases have been dumped to $OUTPUT_FILE."
