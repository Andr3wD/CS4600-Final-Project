import asyncio
import json
import websockets
import random
import sys
import secrets
import PySimpleGUI as gui
import Crypto

class Client:
    @classmethod # Since python doesn't let `async def __init__()`
    async def create(cls):
        """
        Creates an instance of Client in an asynchronously safe manner.
        """

        self = Client()
        self.connection = await websockets.connect('ws://localhost:12345') # Connect to server
        self.unhandled_messages = asyncio.Queue()
        self.active_participants = []
        self.secrets = [5436] # All secret pairs this client has with other clients. TODO remove the testing numbers.
        self.message_send_queue = []
        self.sent_messages = {}
        self.unhandled_anon_messages = []
        self.collision_timeout = 0
        self.MAX_MESSAGE_BYTES = 280
        asyncio.get_event_loop().create_task(self.poll_loop()) # Start polling for messages.
        return self

    def pairwise_secrets(self):
        nonce = random.getrandbits(256)
        for participants in self.active_participants
            

    async def poll_loop(self):
        while True:
            # TODO Need DH key exchange types for getting keys between pairs for each pair.

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
                self.handle_anonymous_broadcast(message['messages'], message['index'])
            elif message['type'] == 'active_participant_update':
                print("new participants", message)
                self.active_participants = message['active_participants']
            else:
                await self.unhandled_messages.put(message)

    async def handle_receive_from_peer(self, from_member: str, message: str):
        print('received', message, 'from', from_member)

    def handle_anonymous_broadcast(self, messages: dict[str, int], index: int):
        # TODO maybe change to look at sender peer name, kick out peer if missing message for more than a timeout time, maybe.
        # Or have the server send a 'client_disconnect' that will kick that peer out.

        if len(messages)-1 == len(self.secrets):
            decoded_message = 0
            # We've gotten all messages.
            for user in messages:
                decoded_message ^= messages[user]

            # print("decoded:", decoded_message)
            if decoded_message == 0:
                return


            if index in self.sent_messages: # If we sent a message
                if self.sent_messages[index] == decoded_message: # If the message came through successfully
                    # Message broadcast successfully
                    sent_msg = self.message_send_queue.pop(0)
                    self.unhandled_anon_messages.append((sent_msg, True)) # (msg, this_client?)
                else: # If the message isn't correct
                    # Bad, message garbled.
                    # Message hasn't been successfully sent.

                    # TODO maybe change timeout protocol?
                    self.collision_timeout = secrets.randbelow(self.get_collision_padding_len()) # Randomly choose a collision timeout.
                    print(f"WARN! Message collision. Waiting {self.collision_timeout} windows before retrying.")
            elif not self.verify_no_collision(decoded_message): # there's a collision between other peers
                print("WARN! Collision between peers, discarding recieved message.")
            else:
                extracted = self.extract_msg(decoded_message)
                # print("extracted message:", extracted)
                str_msg = extracted.to_bytes(self.MAX_MESSAGE_BYTES, sys.byteorder).decode("ascii").rstrip("\x00")
                # print(f"Anon: {str_msg}")
                self.unhandled_anon_messages.append((str_msg, False)) # (msg, this_client?)
        else:
            print("ERR! Missing peer broadcast!")

    def get_next_n_index(self, current_index: int, n: int) -> int:
        # Yes, this is simple, but if the index turns into some key generator instead, then we can account for it here.
        return current_index+n

    def add_collision_random(self, msg: int):
        """
            Adds a random collision to the provided msg.

            Args:
                msg (int): the message to add the collision random to.
        """

        collision_padding = self.get_collision_padding_len()
        num_bits = self.get_collision_num_bits()
        bit_choices = list(range(collision_padding)) # bit indicies to select from
        bit_selections = [0]*collision_padding # selected bits to flip to 1.

        # Generate the bits to flip
        for i in range(num_bits):
            choice = secrets.randbelow(len(bit_choices))
            bit_selections[bit_choices[choice]] = 1
            del bit_choices[choice]

        # Set the bits in the message
        for x in bit_selections:
            msg <<= 1
            msg += x

        return msg

    def extract_collision_random(self, msg: int):
        """
            Extracts the collision random from the provided msg.

            Args:
                msg (int): the message to extract the collision random from.
        """
        collision_padding = self.get_collision_padding_len()
        msg &= ((2**collision_padding)-1) # Gives bin 1(0*N)-1 = (1*N)
        return msg

    def verify_no_collision(self, msg: int):
        """
            Verifies that the given msg hasn't been collided with, using ONLY the reserved collision space.

            Args:
                msg (int): the decoded message to verify.
        """
        collision_padding_len = self.get_collision_padding_len()
        collision_num_bits = self.get_collision_num_bits()
        collision_bits = self.extract_collision_random(msg)
        tot = 0
        for i in range(collision_padding_len):
            if collision_bits & 1 == 1:
                tot += 1
            collision_bits >>= 1

        return tot == collision_num_bits


    def extract_msg(self, msg: int):
        """
            Extracts the message from the msg, removing the collision random.

            Args:
                msg (int): the message to extract the message from.
        """
        collision_padding = self.get_collision_padding_len()
        msg >>= collision_padding
        return msg

    def get_collision_num_bits(self) -> int:
        """
            Returns:
                the number of bits to select from the collision space to set as 1.
        """
        return self.get_collision_padding_len()//2 # Maybe divide by the number of participants? TODO LOOKAT.

    def get_collision_padding_len(self) -> int:
        """
            Returns:
                the length of the collision padding space.
        """
        return len(self.secrets)+1 #TODO maybe change to be better?

    async def handle_anonymous_broadcast_request(self, index: int):
        """
            Handles the request by the server to send a anonymous broadcast.

            If there's a message to send and there's no collision timeout, then the client will attempt to send it.
            Otherwise the client will send 0.

            The client's message is then XORed with all the peer pair secrets and sent to the server.
        """
        # print('broadcast request', index)
        collision_padding = self.get_collision_padding_len()

        # If there's a message to send, then conver it to bytes and send it.
        if len(self.message_send_queue) > 0 and self.collision_timeout == 0:
            to_send = self.message_send_queue[0]
            temp_msg = int.from_bytes(bytes(to_send, "ascii"), sys.byteorder)

            # Add collision resistance padding and random number.
            temp_msg = self.add_collision_random(temp_msg)

            self.sent_messages[index] = temp_msg
            print(f"Attempting to anonymously broadcast: '{to_send}'")
        else:
            temp_msg = 0

        if self.collision_timeout > 0:
            self.collision_timeout -= 1

        for secret in self.secrets:
            # One of the papers uses a random seed, but some sort of key generation scheme probably works as well.
            random.seed(secret ^ index)
            temp_msg ^= random.getrandbits((self.MAX_MESSAGE_BYTES*8)+collision_padding) # Twitter character limit is 280. *8 for 1/byte character ASCII encoding.

        await self.send({'type': 'anonymous_broadcast', 'index': index, 'message': temp_msg})

    def send_anonymous_message(self, message: str):
        """
            Adds the provided message to the queue to be processed and sent whenever possible.
            This message will be sent whenever the collision_timeout == 0 and the server polls for an anonymous broadcast.

            An immediate response cannot be guaranteed.
        """
        self.message_send_queue.append(message)

    # Waits for a message that cannot be automatically handled. (I.E. the result
    # of some operation.)
    async def recv_unhandled(self):
        return await self.unhandled_messages.get()

    async def send(self, message):
        await self.connection.send(json.dumps(message))
        return await self.recv_unhandled()

    async def join(self, group: str, participant: str, password: str):
        return await self.send({'type': 'join', 'group': group, 'participant': participant, 'password': password})

    async def send_to_peer(self, participant: str, message):
        return await self.send({'type': 'send_to_peer', 'participant': participant, 'message': message})


async def main():
    client = await Client.create()
    group = input('Enter group name > ')
    participant = input('Enter participant name > ')
    password = input('Enter group password > ')
    # TESTING
    if participant == "Bob":
        client.message_send_queue.append("testmsg from BOB")
    else:
        client.message_send_queue.append("testmsg from ALICE")

    print(await client.join(group, participant, password))
    print(await client.send_to_peer('Alice', ['test', 123]))


async def startGUI():
    layout = [
        [gui.Text("Please input the group name below.")],
        [gui.Input()],
        [gui.Text("Please input your name below.")],
        [gui.Input()],
		[gui.Text("Please input the group password below.")],
		[gui.Input(password_char='*')],
        [gui.Button("Join", bind_return_key=True), gui.Button("Quit"), gui.Text(text_color="Red", key="-ERR-")]
    ]

    window = gui.Window("Anonymous Broadcast.", layout)
    client = await Client.create()
    # Wait for acceptable input.
    while True:
        event, values = window.read(0)

        if event == "Quit" or event == gui.WINDOW_CLOSED:
            exit()
        elif event == "Join":
            if values[0] != '' and values[1] != '':
                response = await client.join(values[0], values[1], values[2])
                print(response)
                print(response["type"])
                if response["type"] == "error":
                    window["-ERR-"].update(value=response["description"])
                elif response["type"] == "success":
                    client.active_participants = response["active_participants"]
                    future = asyncio.ensure_future(generate_and_poll_chat(client))
                    window.close()
                    await future
                    break
            else:
                window["-ERR-"].update(value="Missing group or user name!")


async def generate_and_poll_chat(client: Client):
    # TODO restrict input to message character limit.
    layout = [
        [
            gui.Frame("Chat", [
                [gui.Multiline(size=(60, 40), key="-CHAT-", autoscroll=True, disabled=True)]
            ]),
            gui.Frame("Participants", [
                [gui.Multiline(size=(30, 40), key="-PARTICIPANTS-", disabled=True)]
            ])
        ],
        [gui.Input(key="-INPUT-"), gui.Button("Send", bind_return_key=True), gui.Button("Leave")]
    ]
    window = gui.Window("Anonymous Broadcast Chatroom.", layout)

    while True:
        await asyncio.sleep(0.1)
        event, values = window.read(0)
        # print(event, values)
        if event == gui.WIN_CLOSED or event == "Leave":
            break
        else:
            window["-PARTICIPANTS-"].update("\n".join(client.active_participants))
            if len(client.unhandled_anon_messages) > 0:
                # printing instead of updating for efficiency (space and time).
                # But since multithreaded, can't expect the list to not change (between "".join and clearning the list)
                msg_tup = client.unhandled_anon_messages.pop(0)
                if msg_tup[1]: # if this message was from this client
                    window["-CHAT-"].print(msg_tup[0].strip(), text_color="green")
                else:
                    window["-CHAT-"].print(msg_tup[0].strip())


            if event == "Send" and values["-INPUT-"] != "":
                client.send_anonymous_message(values["-INPUT-"])
                window["-INPUT-"].update("")

    window.close()
    exit()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(startGUI())
    loop.run_forever()
