import asyncio
import json
import websockets
import socket

# Represents someone belonging to a group which may or may not 
# be connected at the moment.
class Participant:
    def __init__(self, name):
        self.name = name
        self.session = None


# A collection of participants
class Group:
    def __init__(self, name, participants, password):
        self.name = name
        self.participants = participants
        self.password = password
        self.anonymous_messages = []

    # Tries to send all participants an `anonymous_broadcast_request` with
    # the next message index. Skips doing anything if not all participants
    # are connected.
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

    # Returns all participants that clients are connected as
    def get_active_participants(self):
        return [p for p in self.participants if p.session is not None]

    # Returns a participant with a given name, or None if no such participant
    # exists.
    def get_participant(self, name):
        for participant in self.participants:
            if participant.name == name:
                return participant
        return None


# Predefined list of groups that exist.
groups = [
    Group('test', [
        Participant('Alice'),
        Participant('Bob'),
    ], 'password'),
    Group('demo', [
        Participant('Andrew'),
        Participant('Josh'),
        Participant('Hannah')
    ], 'CS4600'),
    Group('big', [Participant(name) for name in 'abcdefghijklmnopqrstuvwxyz'], 'bigpassword')
]


# Returns a group with a particular name, or None if no
# such group exists.
def get_group(name):
    for group in groups:
        if group.name == name:
            return group
    return None


# Represents a connection to a client.
class Session:
    # Constructs a session given a websocket connection.
    def __init__(self, connection):
        self.connection = connection
        self.group = None
        self.participating_as = None

    # A long running function which appropriately handles all incoming messages.
    async def handle_messages(self):
        try:
            async for message in self.connection:
                await self.handle_message(message)
        except Exception as e:
            print(e)
            await self.handle_closed()

    # Encodes a message into JSON format and sends it to the client this session
    # is connected to.
    async def send_message(self, message):
        message = json.dumps(message)
        try:
            await self.connection.send(message)
        except:
            pass

    # Sends {"type": "success"} to the cient
    async def send_success(self):
        await self.send_message({'type': 'success'})

    # Sends {"type": "error", "description": description} to the cient
    async def send_error(self, description):
        await self.send_message({'type': 'error', 'description': description})

    # Called whenever a new message is received from the client.
    async def handle_message(self, message):
        message = json.loads(message)
        print(message)
        # Error handling for when message is not an object or does not
        # contain a "type" field.
        if type(message) is not dict:
            await self.send_error('Message must be an object.')
            return
        if 'type' not in message.keys():
            await self.send_error('Message must specify its type.')
            return
        t = message['type']

        # Indicates that a client wants to show they are part of a particular group.
        if t == 'join':
            # Don't let clients join as multiple participants.
            if self.participating_as is not None:
                await self.send_error('You have already joined a group.')
                return
            if 'group' not in message.keys():
                await self.send_error('Missing required parameter "group".')
                return
            self.group = get_group(message['group'])
            # Error on invalid group name.
            if self.group is None:
                await self.send_error('Invalid group.')
                return
            if 'participant' not in message.keys():
                await self.send_error('Missing required parameter "participant".')
                return
            participant = self.group.get_participant(message['participant'])
            # Error on invalid participant name.
            if participant is None:
                await self.send_error('Invalid participant.')
                return
            # Error if someone has already joined as the specified partner.
            if participant.session is not None:
                await self.send_error('That participant has already joined.')
                return
            self.participating_as = participant

            # Tell all participants about the new client.
            temp_old = self.group.get_active_participants()
            participant.session = self
            temp_new = [p.name for p in self.group.get_active_participants()]
            for part in temp_old:
                await part.session.send_message({'type': 'active_participant_update', 'active_participants': temp_new})

            # Inform the client that they joined successfully, as well as who else has joined.
            await self.send_message({'type': 'success', 'active_participants': temp_new})
        # Indicates that a client wants to send a message to someone else in the group.
        elif t == 'send_to_peer':
            # Error if the client isn't in a group.
            if self.group is None:
                await self.send_error('You have not joined a group.')
                return
            if 'participant' not in message.keys():
                await self.send_error('Missing required parameter "participant".')
                return
            participant = self.group.get_participant(message['participant'])
            # Error if the desired participant name isn't in the current group.
            if participant is None:
                await self.send_error('Invalid participant.')
                return
            # Error if the recipient isn't connected.
            if participant.session is None:
                await self.send_error('That participant has not yet joined.')
                return
            if 'message' not in message.keys():
                await self.send_error('Missing required parameter "message".')
                return
            # Send the recipient the message.
            await participant.session.send_message({'type': 'receive_from_peer', 'from': self.participating_as.name, 'message': message['message']})
            # Tell the sender it worked out.
            await self.send_success()
        # Indicates the client has a contribution for an anonymous broadcast.
        elif t == 'anonymous_broadcast':
            # Clients cannot broadcast messages if they are not in a group.
            if self.group is None:
                await self.send_error('You have not joined a group.')
                return
            if 'index' not in message.keys():
                await self.send_error('Missing required parameter "index".')
                return
            index = message['index']
            # Error on invalid index.
            if index >= len(self.group.anonymous_messages):
                await self.send_error('Invalid index.')
                return
            if 'message' not in message.keys():
                await self.send_error('Missing required parameter "message".')
                return
            message = message['message']
            # Error when sending multiple messages for the same index.
            if self.participating_as.name in self.group.anonymous_messages[index].keys():
                await self.send_error('Cannot submit multiple messages for the same slot.')
                return
            messages = self.group.anonymous_messages[index]
            messages[self.participating_as.name] = message
            # If everyone has submitted a message...
            if len(messages) == len(self.group.participants):
                # ...broadcast all the messages...
                send = {'type': 'anonymous_broadcast', 'messages': messages, 'index': index}
                for participant in self.group.participants:
                    # ...to each connected participant
                    if participant.session is not None:
                        await participant.session.send_message(send)
            # Inform the client that the request was successful.
            await self.send_success()
        else:
            # Error on unrecognized type
            await self.send_error('Unrecognized message type ' + t)

    # Called when the connection is closed
    async def handle_closed(self):
        if self.participating_as is not None:
            # Clear the session of the participant this client was connected as, indicating
            # that participant is no longer connected.
            self.participating_as.session = None
            self.participating_as = None


# Long-running task that tries to send anonymous message broadcast requests to participants
# of every group every second. See Group::try_start_anonymous_message
async def continually_send_anonymous_broadcast_requests():
    while True:
        for group in groups:
            await group.try_start_anonymous_message()
        await asyncio.sleep(1)
        

# Called for every incoming connection on the websocket server.
async def handler(connection, _path):
    # Create a new session for that connection, and start the task that
    # handles incoming messages from it.
    await Session(connection).handle_messages()


# Starts several long-running tasks necessary for the protocol to operate correctly.
def main():
    # Run a websocket server, using handler(...) to create sessions for every incoming connection.
    self_ip = socket.gethostbyname(socket.gethostname())
    server = websockets.serve(handler, self_ip, 12345)
    asyncio.get_event_loop().run_until_complete(server)
    print('Server running!')
    # Try starting anonymous broadcast every second.
    asyncio.get_event_loop().create_task(
        continually_send_anonymous_broadcast_requests())
    # Run those tasks forever
    asyncio.get_event_loop().run_forever()


if __name__ == '__main__':
    main()
