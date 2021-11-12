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
        self.anonymous_messages = []

    async def try_start_anonymous_message(self):
        for participant in self.participants:
            if participant.session is None:
                return False
        index = len(self.anonymous_messages)
        self.anonymous_messages.append({})
        for participant in self.participants:
            await participant.session.send_message(
                {'type': 'anonymous_broadcast_request', 'index': index})
        return True

    def get_participant(self, name):
        for participant in self.participants:
            if participant.name == name:
                return participant
        return None


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
        self.group = None
        self.participating_as = None

    async def handle_messages(self):
        try:
            async for message in self.connection:
                await self.handle_message(message)
        except Exception as e:
            print(e)
            await self.handle_closed()

    async def send_message(self, message):
        message = json.dumps(message)
        try:
            await self.connection.send(message)
        except:
            pass

    async def send_success(self):
        await self.send_message({'type': 'success'})

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
                await self.send_error('You have already joined a group.')
                return
            if 'group' not in message.keys():
                await self.send_error('Missing required parameter "group".')
                return
            self.group = get_group(message['group'])
            if self.group is None:
                await self.send_error('Invalid group.')
                return
            if 'participant' not in message.keys():
                await self.send_error('Missing required parameter "participant".')
                return
            participant = self.group.get_participant(message['participant'])
            if participant is None:
                await self.send_error('Invalid participant.')
                return
            if participant.session is not None:
                await self.send_error('That participant has already joined.')
                return
            self.participating_as = participant
            participant.session = self
            await self.send_success()
        elif t == 'send_to_peer':
            if self.group is None:
                await self.send_error('You have not joined a group.')
                return
            if 'participant' not in message.keys():
                await self.send_error('Missing required parameter "participant".')
                return
            participant = self.group.get_participant(message['participant'])
            if participant is None:
                await self.send_error('Invalid participant.')
                return
            if participant.session is None:
                await self.send_error('That participant has not yet joined.')
                return
            if 'message' not in message.keys():
                await self.send_error('Missing required parameter "message".')
                return
            await participant.session.send_message({'type': 'receive_from_peer', 'from': self.participating_as.name, 'message': message['message']})
            await self.send_success()
        elif t == 'anonymous_broadcast':
            if self.group is None:
                await self.send_error('You have not joined a group.')
                return
            if 'index' not in message.keys():
                await self.send_error('Missing required parameter "index".')
                return
            index = message['index']
            if index >= len(self.group.anonymous_messages):
                await self.send_error('Invalid index.')
                return
            if 'message' not in message.keys():
                await self.send_error('Missing required parameter "message".')
                return
            message = message['message']
            if self.participating_as.name in self.group.anonymous_messages[index].keys():
                await self.send_error('Cannot submit multiple messages for the same slot.')
                return
            messages = self.group.anonymous_messages[index]
            messages[self.participating_as.name] = message
            if len(messages) == len(self.group.participants):
                send = {'type': 'anonymous_broadcast', 'messages': messages, 'index': index}
                for participant in self.group.participants:
                    if participant.session is not None:
                        await participant.session.send_message(send)
            await self.send_success()
        else:
            await self.send_error('Unrecognized message type ' + t)

    async def handle_closed(self):
        if self.participating_as is not None:
            self.participating_as.session = None
            self.participating_as = None


async def handler(connection, _path):
    await Session(connection).handle_messages()


async def continually_send_anonymous_broadcast_requests():
    while True:
        for group in groups:
            await group.try_start_anonymous_message()
        await asyncio.sleep(2)


def main():
    server = websockets.serve(handler, 'localhost', 12345)
    asyncio.get_event_loop().run_until_complete(server)
    print('Server running!')
    asyncio.get_event_loop().create_task(
        continually_send_anonymous_broadcast_requests())
    asyncio.get_event_loop().run_forever()


if __name__ == '__main__':
    main()
