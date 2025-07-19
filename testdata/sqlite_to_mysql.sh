#!/bin/bash

# Check if input file is provided
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <sqlite_sql_file>"
    exit 1
fi

# Input file
input_file=$1

# Output file
output_file="${input_file%.sql}_mysql.sql"

# Create a new output file
> "$output_file"

# Function to modify the SQL dump for MySQL compatibility
convert_sqlite_to_mysql() {
    awk '
        # Remove SQLite-specific PRAGMA and transaction statements
        /PRAGMA.*;/ { next }
        /BEGIN TRANSACTION;/ { next }
        /COMMIT;/ { next }

        # Replace AUTOINCREMENT with AUTO_INCREMENT
        { gsub(/AUTOINCREMENT/, "AUTO_INCREMENT") }

        # Adjust integer types to auto increment if they are primary keys
        { gsub(/INTEGER NOT NULL/, "INT NOT NULL AUTO_INCREMENT") }

        # Convert VARCHAR to TEXT
        { gsub(/VARCHAR/, "TEXT") }

        # Convert BOOLEAN to TINYINT for MySQL compatibility
        { gsub(/\bBOOLEAN\b/, "TINYINT(1)") }

        # Handle CREATE TABLE blocks to remove quotes around column names
        /CREATE TABLE/ { in_create_table=1 }
        in_create_table && /;/ { in_create_table=0 }
        in_create_table { gsub(/"([a-zA-Z0-9_]+)"/, "&"); gsub(/"/, "") }

        # Print the modified line
        { print }
    ' "$1" |
# Replace '' with \' according to the specified conditions
sed "/^INSERT INTO/ { s/\([^,(]\)''/\1\\\\'/g; s/''\([^,)]\)/\\\\'\1/g }" > "$output_file"


}

# Call the conversion function
convert_sqlite_to_mysql "$input_file"

# Print success message
echo "Conversion complete. MySQL-compatible file created: $output_file"
