import asyncio
import json
import websockets
import random
import sys
import secrets
import PySimpleGUI as gui
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA256
from Crypto.Cipher import PKCS1_OAEP, AES
from Crypto.Random import get_random_bytes
import socket
import time


public_keyring = {
    "Andrew": "andrew_public.pem",
    "Josh": "josh_public.pem",
    "Hannah": "hannah_public.pem"
}

class Client:
    @classmethod # Since python doesn't let `async def __init__()`
    async def create(cls):
        """
        Creates an instance of Client in an asynchronously safe manner.
        """

        self = Client()
        self_ip = socket.gethostbyname(socket.gethostname()) # Get local ip.
        # If you're the professor (or grader), this was swapped out for the public IPv4 of the AWS IC2 instance for the demo. See the demo branch.
        self.connection = await websockets.connect(f'ws://{self_ip}:12345') # Connect to server
        self.unhandled_messages = asyncio.Queue()
        self.active_participants = [] # All currently active participants (connected to server)
        self.all_participants = [] # All participants setup for this group (active or not)
        self.secrets = {} # All secret pairs this client has with other clients.
        self.secret_handshakes = {} # Keep track of handshake progress {participant_name: stage} where stage=1 if initiated by one side, and stage=2 if done.
        self.message_send_queue = [] # Messages to send
        self.sent_messages = {} # Messages sent on this index in the form [index: message]
        self.unhandled_anon_messages = [] # Messages to be handled by the GUI
        self.collision_timeout = 0 # Current collision timeout to wait.
        self.MAX_MESSAGE_BYTES = 280 # Maximum bytes for a message (sets the size of each anonymous broadcast)
        self.name = "" # The name of this client
        asyncio.get_event_loop().create_task(self.poll_loop()) # Start polling for messages.
        return self

    async def poll_loop(self):
        while True:
            # Receive all incoming messages and handle them appropriately
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
            elif message['type'] == 'generate_secrets':
                print("generating secrets")
                asyncio.get_event_loop().create_task(
                    self.generate_pairwise_secrets()
                )
            elif message['type'] == 'receive_from_peer_secret_handshake':
                asyncio.get_event_loop().create_task(
                    self.handle_handshake_receive_from_peer(message['from'], message['message'])
                )
            else:
                await self.unhandled_messages.put(message)

    async def handle_handshake_receive_from_peer(self, from_member: str, message):
        with open(self.name.lower() + "_private.pem", 'r') as priv_file:
            our_key = RSA.importKey(priv_file.read())
            our_dec = PKCS1_OAEP.new(our_key)
            with open(public_keyring[from_member], 'r') as file:
                part_key = RSA.importKey(file.read())
                part_sig = pkcs1_15.new(part_key) # to verify signature

                # Decrypt session key using RSA.
                session_key = our_dec.decrypt(bytes.fromhex(message["session_key"]))
                session_aes = AES.new(session_key, AES.MODE_EAX, bytes.fromhex(message["cipher_nonce"]))
                plaintext = session_aes.decrypt_and_verify(bytes.fromhex(message["ciphertext"]), bytes.fromhex(message["tag"]))
                package = json.loads(plaintext.decode("utf-8"))

                # Verify signature and timestamp.
                timediff = int(time.time()) - package["timestamp"]
                if timediff < 300:
                    h = SHA256.new(str(package["seed"]).encode())
                    valid = False
                    try:
                        part_sig.verify(h, bytes.fromhex(package["signature"]))
                        valid = True
                    except ValueError:
                        pass

                    if valid:
                        # message came from this participant.
                        if from_member not in self.secrets:
                            self.secrets[from_member] = 0
                            self.secret_handshakes[from_member] = 1 # Keep track of handshake progress
                        else:
                            self.secret_handshakes[from_member] += 1 # Keep track of handshake progress

                        self.secrets[from_member] ^= int(package["seed"])
                    else:
                        print("BAD SIGNATURE FROM:", from_member)

                else:
                    print(f"BAD TIMESTAMP {timediff} > 30 FROM: {from_member}")

        # if receive all secrets, then send OK.
        # record recv from each user.
        if self.check_secret_handshake_complete():
            print("ALL SEEDS RECEIVED!")
            await self.send({'type': 'secrets_generated'})
        else:
            print("Still waiting for random seeds from all participants")

    def check_secret_handshake_complete(self):
        """
            Verifies if this client has finished the pairwise secret handshake with every other participant.

            returns True if so
            returns False otherwise
        """
        for part in self.active_participants:
            if part != self.name and (part not in self.secret_handshakes or self.secret_handshakes[part] != 2):
                return False
        return True


    async def handle_receive_from_peer(self, from_member: str, message: str):
        print('received', message, 'from', from_member)

    def handle_anonymous_broadcast(self, messages: dict[str, int], index: int):
        if len(messages)-1 == len(self.secrets):
            # We've gotten all messages.

            decoded_message = 0
            for user in messages:
                decoded_message ^= messages[user]

            # if empty message, then stop early.
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

                    self.collision_timeout = secrets.randbelow(self.get_collision_padding_len()) # Randomly choose a collision timeout.
                    print(f"WARN! Message collision. Waiting {self.collision_timeout} windows before retrying.")
            elif not self.verify_no_collision(decoded_message): # there's a collision between other peers
                print("WARN! Collision between peers, discarding recieved message.")
            else:
                extracted = self.extract_msg(decoded_message) # Extracted message in bytes

                # Turns bytes to string, removing all extra null bytes.
                str_msg = extracted.to_bytes(self.MAX_MESSAGE_BYTES, sys.byteorder).decode("ascii").rstrip("\x00")
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
        return self.get_collision_padding_len()//2

    def get_collision_padding_len(self) -> int:
        """
            Returns:
                the length of the collision padding space.
        """
        return len(self.secrets)*2

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

        for part in self.secrets:
            random.seed(self.secrets[part] ^ index) # Set random seed
            # XOR next secret session keys.
            temp_msg ^= random.getrandbits((self.MAX_MESSAGE_BYTES*8)+collision_padding) # Twitter character limit is 280. *8 for 1/byte character ASCII encoding.

        await self.send({'type': 'anonymous_broadcast', 'index': index, 'message': temp_msg})

    def send_anonymous_message(self, message: str):
        """
            Adds the provided message to the queue to be processed and sent whenever possible.
            This message will be sent whenever the collision_timeout == 0 and the server polls for an anonymous broadcast.

            An immediate response cannot be guaranteed.
        """
        self.message_send_queue.append(message)

    async def generate_pairwise_secrets(self):
        seed = random.getrandbits(256) # generate our random seed.
        # Sign with self private key, then encrypt with participant's public key.

        with open(self.name.lower() + "_private.pem", 'r') as priv_file:
            our_key = RSA.importKey(priv_file.read()) # our private key

            # parse through all other participants than this participant..
            for part in self.active_participants:
                if part != self.name:
                    with open(public_keyring[part], 'r') as file: # public key of participant.

                        # If handshake not started already, then init it.
                        # Otherwise, mark the handshake as being in the next stage.
                        if part not in self.secrets:
                            self.secrets[part] = 0
                            self.secret_handshakes[part] = 1
                        else:
                            self.secret_handshakes[part] += 1

                        self.secrets[part] ^= seed # Xor any existing secret (would be the participant's seed or 0) with our seed.

                        part_key = RSA.importKey(file.read()) # Import participant's public key.
                        part_enc = PKCS1_OAEP.new(part_key) # Init participant's public key for ENCRYPTION
                        session_key = get_random_bytes(16) # Generate random session key

                        h = SHA256.new(str(seed).encode()) # Hash our seed
                        signature = pkcs1_15.new(our_key).sign(h) # Sign our hash
                        timestamp = int(time.time()) # Generate timestamp

                        session_aes = AES.new(session_key, AES.MODE_EAX) # Encrypt session key with participant's public key.

                        # Encrypt {timestamp, seed, signature} with the session key.
                        # This is done because RSA cannot encrypt a large amount of bits, but AES can.
                        ciphertext, tag = session_aes.encrypt_and_digest(json.dumps({"timestamp": timestamp, "seed": seed, "signature": signature.hex()}).encode())
                        # Bundle everything up for final sending.
                        to_send = {'session_key': part_enc.encrypt(session_key).hex(), "ciphertext": ciphertext.hex(), "cipher_nonce": session_aes.nonce.hex(), "tag": tag.hex()}

                        await self.send_peer_secret_handshake(part, to_send)

        # If all seeds received then tell the server.
        if self.check_secret_handshake_complete():
            print("ALL SEEDS RECEIVED!")
            await self.send({'type': 'secrets_generated'})
        else:
            print("Still waiting for random seeds from all participants")


    # Waits for a message that cannot be automatically handled. (I.E. the result
    # of some operation.)
    async def recv_unhandled(self):
        return await self.unhandled_messages.get()

    async def send(self, message):
        """
            Send message to server. Message is serialized into json before sending.
        """
        await self.connection.send(json.dumps(message))
        return await self.recv_unhandled()

    async def join(self, group: str, participant: str, password: str):
        """
            Send a request to the server to join the given group as the given participant.
        """
        return await self.send({'type': 'join', 'group': group, 'participant': participant, 'password': password})

    async def send_to_peer(self, participant: str, message):
        """
            Send a request to the server to send a message to the provided participant peer.
        """
        return await self.send({'type': 'send_to_peer', 'participant': participant, 'message': message})

    async def send_peer_secret_handshake(self, participant: str, message):
        """
            Send a message to the server to send a handshake message to the provided participant peer.
        """
        return await self.send({'type': 'send_to_peer_secret_handshake', 'participant': participant, 'message': message})

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
        event, values = window.read(0) # Poll events

        if event == "Quit" or event == gui.WINDOW_CLOSED:
            exit()
        elif event == "Join":
            # Validate inputs
            if values[0] != '' and values[1] != '' and values[2] != '':
                # Attempt to join server
                response = await client.join(values[0], values[1], values[2])

                # If server replies with an error type, then display it next to the buttons.
                if response["type"] == "error":
                    window["-ERR-"].update(value=response["description"])
                elif response["type"] == "success":
                    client.name = values[1] # Set client name
                    # Set the current active participants.
                    client.active_participants = response["active_participants"]
                    print(response)
                    client.all_participants = response["all_participants"]

                    # Start new thread for chat.
                    future = asyncio.ensure_future(generate_and_poll_chat(client))
                    window.close()
                    await future # Wait for chat thread to exit.
                    break
            else:
                window["-ERR-"].update(value="Missing group, user name, and/or password!")


async def generate_and_poll_chat(client: Client):
    layout = [
        [
            gui.Frame("Chat", [
                [gui.Multiline(size=(60, 40), key="-CHAT-", autoscroll=True, disabled=True)]
            ]),
            gui.Frame("Participants", [
                [gui.Multiline(size=(30, 40), key="-PARTICIPANTS-", disabled=True)]
            ])
        ],
        [gui.Input(key="-INPUT-", disabled=True), gui.Button("Send", bind_return_key=True, key="-SEND-", disabled=True), gui.Button("Leave")]
    ]
    window = gui.Window("Anonymous Broadcast Chatroom.", layout)

    while True:
        await asyncio.sleep(0.1)
        event, values = window.read(0) # Poll GUI for events and values

        if event == gui.WIN_CLOSED or event == "Leave": # If leave or window closed.
            break
        else:
            window["-PARTICIPANTS-"].update("\n".join(client.active_participants)) # update active participant list

            # If missing participants, then disable the input.
            if len(client.active_participants) == len(client.all_participants):
                window["-INPUT-"].update(disabled=False)
                window["-SEND-"].update(disabled=False)
                window.refresh()
            else:
                window["-INPUT-"].update(disabled=True)
                window["-SEND-"].update(disabled=True)
                window.refresh()

            if len(client.unhandled_anon_messages) > 0:
                # printing instead of updating for efficiency (space and time).
                # But since multithreaded, can't expect the list to not change (between "".join and clearning the list)
                msg_tup = client.unhandled_anon_messages.pop(0)
                if msg_tup[1]: # if this message was from this client, then print it green.
                    window["-CHAT-"].print(msg_tup[0].strip(), text_color="green")
                else:
                    window["-CHAT-"].print(msg_tup[0].strip())

            # If GUI send event is activated and input isn't empty.
            if event == "-SEND-" and values["-INPUT-"] != "":
                client.send_anonymous_message(values["-INPUT-"]) # Add message to queue to be sent.
                window["-INPUT-"].update("") # Clear input

    window.close()
    exit()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(startGUI())
    loop.run_forever()
