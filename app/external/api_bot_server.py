import uvicorn
from uvicorn import Config, Server


async def start_api_bot_server():
    config = Config("app.api.main:app", host="0.0.0.0", port=8000, workers=1)
    server = Server(config)
    await server.serve()
