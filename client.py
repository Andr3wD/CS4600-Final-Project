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

    async def handle_receive_from_peer(self, from_member, message):
        print('received', message, 'from', from_member)

    async def handle_anonymous_broadcast_request(self, index):
        print('broadcast request', index)
        await self.send({'type': 'anonymous_broadcast', 'index': index, 'message': 0})
    
    def handle_anonymous_broadcast(self, messages):
        print('Anonymous broadcast: ', messages)

    async def poll_loop(self):
        while True:
            message = json.loads(await self.connection.recv())
            if message['type'] == 'receive_from_peer':
                asyncio.get_event_loop().create_task(
                    self.handle_receive_from_peer(
                        message['from'], message['message'])
                )
            elif message['type'] == 'anonymous_broadcast_request':
                asyncio.get_event_loop().create_task(
                    self.handle_anonymous_broadcast_request(message['index'])
                )
            elif message['type'] == 'anonymous_broadcast':
                self.handle_anonymous_broadcast(message['messages'])
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

    async def send_to_peer(self, participant, message):
        return await self.send({'type': 'send_to_peer', 'participant': participant, 'message': message})


async def main():
    client = await Client.create()
    group = input('Enter group name > ')
    participant = input('Enter participant name > ')
    print(await client.join(group, participant))
    print(await client.send_to_peer('Alice', ['test', 123]))

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    loop.run_forever()
