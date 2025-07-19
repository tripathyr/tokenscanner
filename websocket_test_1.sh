#!/bin/bash

# Variables
WEBSOCKET_URL="http://127.0.0.1:80/websocket"
WEBSOCKET_KEY="x3JJHMbDL1EzLkh9GBhXDw=="
WEBSOCKET_VERSION="13"

# Perform the WebSocket handshake using curl
echo "Performing WebSocket handshake..."
curl -i -N \
    -H "Connection: Upgrade" \
    -H "Upgrade: websocket" \
    -H "Host: 127.0.0.1:80" \
    -H "Origin: http://127.0.0.1:80" \
    -H "Sec-WebSocket-Key: $WEBSOCKET_KEY" \
    -H "Sec-WebSocket-Version: $WEBSOCKET_VERSION" \
    "$WEBSOCKET_URL" &

# Wait for the server to establish the connection
sleep 2

# Generate a valid subscription request for the WebSocket server
SUBSCRIPTION_REQUEST='{
  "id": "1",
  "method": "subscribeNewBlock",
  "params": {}
}'

# Use curl to send the subscription request
echo "Sending subscription request..."
curl -i -N \
    -H "Content-Type: application/json" \
    -d "$SUBSCRIPTION_REQUEST" \
    "$WEBSOCKET_URL" &

# Listen for incoming messages (from the server)
echo "Listening for server responses..."
curl -i -N \
    -H "Connection: Upgrade" \
    -H "Upgrade: websocket" \
    -H "Host: 127.0.0.1:80" \
    -H "Origin: http://127.0.0.1:80" \
    -H "Sec-WebSocket-Key: $WEBSOCKET_KEY" \
    -H "Sec-WebSocket-Version: $WEBSOCKET_VERSION" \
    "$WEBSOCKET_URL"

# End of script
