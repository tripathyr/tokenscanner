import asyncio
import websockets
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

async def test_websocket():
    uri = "ws://127.0.0.1:80/websocket"  # Update the URI if needed

    try:
        logging.info(f"Connecting to WebSocket server at {uri}...")
        async with websockets.connect(uri) as websocket:
            logging.info("Connected to WebSocket server.")

            # Send subscription request
            subscription_request = {
                "id": "1",
                "method": "subscribeNewBlock",
                "params": {}
            }
            await websocket.send(json.dumps(subscription_request))
            logging.info("Subscription request sent: %s", subscription_request)

            # Listen for responses
            while True:
                try:
                    response = await websocket.recv()
                    logging.info(f"Received: {response}")
                except websockets.ConnectionClosed as e:
                    logging.warning(f"WebSocket connection closed: {e}")
                    break
                except Exception as e:
                    logging.error(f"Error receiving WebSocket message: {e}")
                    break

    except websockets.exceptions.InvalidStatusCode as e:
        logging.error(f"WebSocket handshake failed with status code: {e.status_code}")
    except websockets.exceptions.ConnectionClosedError as e:
        logging.error(f"WebSocket connection closed unexpectedly: {e}")
    except Exception as e:
        logging.error(f"WebSocket error: {e}")

# Run the WebSocket client
if __name__ == "__main__":
    asyncio.run(test_websocket())
    