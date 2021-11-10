import asyncio
import json
import websockets

async def main():
    connection = await websockets.connect('ws://localhost:12345')
    await connection.send(json.dumps({ 'type': 'hello' }))
    print(await connection.recv())

if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())
