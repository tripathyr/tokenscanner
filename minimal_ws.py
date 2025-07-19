from quart import Quart, websocket
from hypercorn.asyncio import serve
from hypercorn.config import Config

app = Quart(__name__)

@app.websocket("/websocket")
async def echo_ws():
    while True:
        message = await websocket.receive()
        await websocket.send(f"Echo: {message}")

async def main():
    config = Config()
    config.bind = ["0.0.0.0:8080"]
    config.loglevel = "debug"
    await serve(app, config)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
