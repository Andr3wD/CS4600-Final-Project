import asyncio
import json
import websockets


class Participant:
    def __init__(self, name):
        self.name = name
        self.session = None


class Group:
    def __init__(self, name, participants):
        self.name = name
        self.participants = participants


groups = [
    Group('first', [
        Participant('Alice'),
        Participant('Bob'),
    ]),
    Group('second', [
        Participant('Eve'),
        Participant('Eve\'s not-evil twin sister'),
    ]),
    Group('big', [Participant(name) for name in 'abcdefghijklmnopqrstuvwxyz'])
]


def get_group(name):
    for group in groups:
        if group.name == name:
            return group
    return None


class Session:
    def __init__(self, connection):
        self.connection = connection
        self.participating_as = None

    async def handle_messages(self):
        try:
            async for message in self.connection:
                await self.handle_message(message)
        except:
            await self.handle_closed()

    async def send_message(self, message):
        message = json.dumps(message)
        try:
            await self.connection.send(message)
        except:
            pass

    async def send_error(self, description):
        await self.send_message({'type': 'error', 'description': description})

    async def handle_message(self, message):
        message = json.loads(message)
        print(message)
        if type(message) is not dict:
            await self.send_error('Message must be an object.')
            return
        if 'type' not in message.keys():
            await self.send_error('Message must specify its type.')
            return
        t = message['type']
        if t == 'join':
            if self.participating_as is not None:
                pass
            await self.send_error('unimplemented')
        else:
            await self.send_error('Unrecognized message type ' + t)

    async def handle_closed(self):
        pass


async def handler(connection, _path):
    await Session(connection).handle_messages()


def main():
    server = websockets.serve(handler, 'localhost', 12345)
    asyncio.get_event_loop().run_until_complete(server)
    print('Server running!')
    asyncio.get_event_loop().run_forever()


if __name__ == '__main__':
    main()
