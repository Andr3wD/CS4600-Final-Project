import asyncio
import json
import websockets


class Client:
    @classmethod
    async def create(cls):
        self = Client()
        self.connection = await websockets.connect('ws://localhost:12345')
        self.unhandled_messages = asyncio.Queue()
        asyncio.get_event_loop().create_task(self.poll_loop())
        return self

    async def handle_receive_message(self, from_member, message):
        print('received', message, 'from', from_member)

    async def poll_loop(self):
        while True:
            message = json.loads(await self.connection.recv())
            if message['type'] == 'receive':
                await self.handle_receive_message(message['from'], message['message'])
            elif message['type'] == 'todo':
                pass
            else:
                await self.unhandled_messages.put(message)

    # Waits for a message that cannot be automatically handled. (I.E. the result
    # of some operation.)
    async def recv_unhandled(self):
        return await self.unhandled_messages.get()

    async def send(self, message):
        await self.connection.send(json.dumps(message))
        return await self.recv_unhandled()

    async def join(self, group, participant):
        return await self.send({'type': 'join', 'group': group, 'participant': participant})

    async def send_to_participant(self, participant, message):
        return await self.send({'type': 'send', 'participant': participant, 'message': message})


async def main():
    client = await Client.create()
    print(await client.join('first', 'Alice'))
    print(await client.send_to_participant('Alice', ['test', 123]))

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    loop.run_forever()
