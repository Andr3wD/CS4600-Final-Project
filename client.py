import asyncio
import json
import websockets
import random
import sys
import secrets


class Client:
    @classmethod # Since python doesn't let `async def __init__()`
    async def create(cls):
        """
        Creates an instance of Client in an asynchronously safe manner.
        """

        self = Client()
        self.connection = await websockets.connect('ws://localhost:12345') # Connect to server
        self.unhandled_messages = asyncio.Queue()
        self.secrets = [5436] # All secret pairs this client has with other clients. TODO remove the testing numbers.
        self.message_send_queue = []
        self.sent_messages = {}
        self.collision_timeout = 0
        self.MAX_MESSAGE_BYTES = 280
        asyncio.get_event_loop().create_task(self.poll_loop()) # Start polling for messages.
        return self

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
            for m in messages:
                decoded_message ^= messages[m]

            print("decoded:", decoded_message)
            if decoded_message == 0:
                return


            if index in self.sent_messages: # If we sent a message
                if self.sent_messages[index] == decoded_message: # If the message came through successfully
                    # Message broadcast successfully
                    self.message_send_queue.pop(0)
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
                print("extracted message:", extracted)
                str_msg = extracted.to_bytes(self.MAX_MESSAGE_BYTES, sys.byteorder).decode("ascii")
                print(f"Anon: {str_msg}")
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
        print('broadcast request', index)
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

    # Waits for a message that cannot be automatically handled. (I.E. the result
    # of some operation.)
    async def recv_unhandled(self):
        return await self.unhandled_messages.get()

    async def send(self, message):
        await self.connection.send(json.dumps(message))
        return await self.recv_unhandled()

    async def join(self, group: str, participant: str):
        return await self.send({'type': 'join', 'group': group, 'participant': participant})

    async def send_to_peer(self, participant: str, message):
        return await self.send({'type': 'send_to_peer', 'participant': participant, 'message': message})



async def main():
    client = await Client.create()
    group = input('Enter group name > ')
    participant = input('Enter participant name > ')
    # TESTING
    if participant == "Bob":
        client.message_send_queue.append("testmsg from BOB")
    else:
        client.message_send_queue.append("testmsg from ALICE")

    print(await client.join(group, participant))
    print(await client.send_to_peer('Alice', ['test', 123]))

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    loop.run_forever()
