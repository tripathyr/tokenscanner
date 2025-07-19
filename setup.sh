#!/bin/bash

# =====================
# Setup Script for PyFLO, MySQL, Virtual Environment, and FLO Token Tracking
# =====================

# Exit on any error
set -e

# Step 1: Update Package List
echo "Updating package list..."
sudo apt update

# Step 2: Install System Dependencies
echo "Installing system dependencies..."
sudo apt install -y build-essential libssl-dev pkg-config python3-setuptools git

# Inform the user
echo "System dependencies installed successfully."

# Step 3: Check and Install Python 3.7
echo "Checking for Python 3.7..."
if ! python3.7 --version &>/dev/null; then
    echo "Python 3.7 not found. Installing Python 3.7..."
    sudo add-apt-repository ppa:deadsnakes/ppa -y
    sudo apt update
    sudo apt install -y python3.7 python3.7-venv
    echo "Python 3.7 installed successfully."
else
    echo "Python 3.7 is already installed."
fi

# Step 4: Set Up Virtual Environment Using Python 3.7
VENV_NAME="myenv"
echo "Setting up virtual environment using Python 3.7..."
/usr/bin/python3.7 -m venv $VENV_NAME
echo "Virtual environment '$VENV_NAME' created successfully."

# Activate the virtual environment
source $VENV_NAME/bin/activate

# Inform the user
echo "Virtual environment activated. Using Python version:"
python --version

# Step 5: Check and Install MySQL
echo "Checking if MySQL is installed..."
if ! dpkg -l | grep -q mysql-server; then
    echo "MySQL is not installed. Installing MySQL server, client, and development libraries..."
    sudo apt install -y mysql-server mysql-client libmysqlclient-dev
    echo "MySQL installed successfully."
else
    echo "MySQL is already installed. Skipping installation."
fi

# Step 6: Check and Start MySQL Service
echo "Checking if MySQL service is running..."
if systemctl is-active --quiet mysql; then
    echo "MySQL is already running."
else
    echo "MySQL is not running. Starting MySQL..."
    sudo systemctl start mysql
    echo "MySQL service started."
fi

# Enable MySQL to start on boot
sudo systemctl enable mysql

# Step 7: Configure MySQL Default User and Privileges
echo "Configuring MySQL user and privileges..."
MYSQL_USER="FUfB6cwSsGDbQpmA7Qs8zQJxU3Hp"
MYSQL_PASSWORD="RAcifrTM2V75ipy5MeLYaDU3UNcUXtrit933TGM5o7Yj2fs8XdP5!"

sudo mysql -e "CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'localhost' IDENTIFIED BY '${MYSQL_PASSWORD}';"
sudo mysql -e "GRANT ALL PRIVILEGES ON rm_%_db.* TO '${MYSQL_USER}'@'localhost' WITH GRANT OPTION;"
sudo mysql -e "FLUSH PRIVILEGES;"

echo "MySQL user '${MYSQL_USER}' created and granted privileges on databases matching 'rm_%_db'."

# Step 8: Clone the FLO Token Tracking Repository Only if `setup.sh` Is Not Already Inside the Repository Directory
if [ ! -f "tracktokens-smartcontracts.py" ]; then
    echo "Cloning the FLO Token Tracking repository (mysql-migration branch)..."
    git clone --branch mysql-migration https://github.com/ranchimall/flo-token-tracking
    cd flo-token-tracking
else
    echo "Setup is already in the directory containing the repository. Skipping clone."
fi

# Step 9: Install Python Dependencies
echo "Installing Python dependencies..."
if [ ! -f "requirements.txt" ]; then
    # Generate a requirements.txt file if missing
    echo "arduino" > requirements.txt
    echo "pybtc" >> requirements.txt
    echo "config" >> requirements.txt
    echo "pymysql" >> requirements.txt
    echo "Generated requirements.txt with default dependencies."
else
    echo "requirements.txt file exists. Adding pymysql to the list."
    echo "pymysql" >> requirements.txt
fi

# Ensure pip is up-to-date
pip install --upgrade pip

# Install Python packages
pip install --use-pep517 -r requirements.txt

# Step 10: Start the Python Application
echo "Starting the Python application 'tracktokens-smartcontracts.py'..."
python3.7 tracktokens-smartcontracts.py

# Final Instructions
echo "========================================================"
echo "Setup is complete. MySQL server is installed and running."
echo "Virtual environment '$VENV_NAME' is set up and active."
echo "MySQL user '${MYSQL_USER}' has been created with privileges on databases matching 'rm_%_db'."
echo "The Python application has been started."
echo "========================================================"
