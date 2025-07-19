#!/bin/bash

# Check which user directory exists
if [ -d "/home/rohit/.flo" ]; then
  BASE_DIR="/home/rohit/.flo"
  BACKUP_DIR="/home/rohit/.flo/backup"
elif [ -d "/home/production/.flo" ]; then
  BASE_DIR="/home/production/.flo"
  BACKUP_DIR="/home/production/.flo/backup"
else
  echo "Neither '/home/rohit/.flo' nor '/home/production/.flo' exists. Exiting..."
  exit 1
fi

# Directories to process
DIRS=(
  "$BASE_DIR/blocks"
  "$BASE_DIR/chainstate"
  "$BASE_DIR/database"
)

# Output hash file
OUTPUT_HASH_FILE="$BACKUP_DIR/all_hashes.txt"

# Ensure the backup folder exists
mkdir -p "$BACKUP_DIR"

# Clear or create the hash file
> "$OUTPUT_HASH_FILE"

echo "Calculating hashes for all files..."

# Loop through the directories and calculate hashes
for dir in "${DIRS[@]}"; do
  if [ -d "$dir" ]; then
    echo "Processing directory: $dir"
    find "$dir" -type f -exec sha256sum {} \; | sort >> "$OUTPUT_HASH_FILE"
  else
    echo "Directory $dir does not exist. Skipping..."
  fi
done

echo "All hashes saved in $OUTPUT_HASH_FILE"
