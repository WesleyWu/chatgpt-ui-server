#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import re
# Builtins
import sys
import time
import os
import uuid
from queue import Queue
from typing import Tuple

import requests
from django.http import StreamingHttpResponse

from chat.models import Message, Conversation
from chatgpt_ui_server import settings
# Local
from .classes import openai as OpenAI
from .classes import chat as ChatHandler
from .classes import spinner as Spinner
from .classes import exceptions as Exceptions

# Fancy stuff
import colorama
from colorama import Fore

from .classes.utils import sse_pack

colorama.init(autoreset=True)
session = requests.Session()


class Options:
    def __init__(self):
        self.log: bool = True
        self.proxies: str or dict or None = None
        self.track: bool or None = False
        self.verify: bool = True
        self.pass_moderation: bool = False
        self.chat_log: str or None = None
        self.id_log: str or None = None

    def __repr__(self):
        return f"<Options log={self.log} proxies={self.proxies} track={self.track} " \
               f"verify={self.verify} pass_moderation={self.pass_moderation} " \
               f"chat_log={self.chat_log} id_log={self.id_log}>"


class Chat:
    def __init__(self,
                 email: str,
                 password: str,
                 options: Options or None = None,
                 conversation_id: str or None = None,
                 parent_message_id: str or None = None):
        self.email = email
        self.password = password
        self.options = options

        self.conversation_id = conversation_id
        self.parent_message_id = parent_message_id

        self.__auth_access_token: str or None = None
        self.__auth_access_token_expiry: int or None = None
        self.__chat_history: list or None = None

        self._setup()

    @staticmethod
    def _create_if_not_exists(file: str):
        if not os.path.exists(file):
            with open(file, 'w') as f:
                f.write("")

    def log(self, inout):
        if self.options is not None and self.options.log:
            print(inout, file=sys.stderr)

    def _setup(self):
        if self.options is not None:
            # If track is enabled, create the chat log and id log files if they don't exist
            if not isinstance(self.options.track, bool):
                raise Exceptions.PyChatGPTException("Options to track conversation must be a boolean.")
            if not isinstance(self.options.log, bool):
                raise Exceptions.PyChatGPTException("Options to log must be a boolean.")

            if self.options.track:
                if self.options.chat_log is not None:
                    self._create_if_not_exists(os.path.abspath(self.options.chat_log))
                    self.options.chat_log = os.path.abspath(self.options.chat_log)
                else:
                    # Create a chat log file called chat_log.txt
                    self.options.chat_log = "chat_log.txt"
                    self._create_if_not_exists(self.options.chat_log)

                if self.options.id_log is not None:
                    self._create_if_not_exists(os.path.abspath(self.options.id_log))
                    self.options.id_log = os.path.abspath(self.options.id_log)
                else:
                    # Create a chat log file called id_log.txt
                    self.options.id_log = "id_log.txt"
                    self._create_if_not_exists(self.options.id_log)

            if self.options.proxies is not None:
                if not isinstance(self.options.proxies, dict):
                    if not isinstance(self.options.proxies, str):
                        raise Exceptions.PyChatGPTException("Proxies must be a string or dictionary.")
                    else:
                        self.proxies = {"http": self.options.proxies, "https": self.options.proxies}
                        self.log(f"{Fore.GREEN}>> Using proxies: True.")

            if self.options.track:
                self.log(f"{Fore.GREEN}>> Tracking conversation enabled.")
                if not isinstance(self.options.chat_log, str) or not isinstance(self.options.id_log, str):
                    raise Exceptions.PyChatGPTException(
                        "When saving a chat, file paths for chat_log and id_log must be strings.")
                elif len(self.options.chat_log) == 0 or len(self.options.id_log) == 0:
                    raise Exceptions.PyChatGPTException(
                        "When saving a chat, file paths for chat_log and id_log cannot be empty.")

                self.__chat_history = []
        else:
            self.options = Options()

        if not self.email or not self.password:
            self.log(f"{Fore.RED}>> You must provide an email and password when initializing the class.")
            raise Exceptions.PyChatGPTException("You must provide an email and password when initializing the class.")

        if not isinstance(self.email, str) or not isinstance(self.password, str):
            self.log(f"{Fore.RED}>> Email and password must be strings.")
            raise Exceptions.PyChatGPTException("Email and password must be strings.")

        if len(self.email) == 0 or len(self.password) == 0:
            self.log(f"{Fore.RED}>> Email cannot be empty.")
            raise Exceptions.PyChatGPTException("Email cannot be empty.")

        if self.options is not None and self.options.track:
            try:
                with open(self.options.id_log, "r") as f:
                    # Check if there's any data in the file
                    if os.path.getsize(self.options.id_log) > 0:
                        self.parent_message_id = f.readline().strip()
                        self.conversation_id = f.readline().strip()
                    else:
                        self.conversation_id = None
            except IOError:
                raise Exceptions.PyChatGPTException(
                    "When resuming a chat, conversation id and previous conversation id in id_log must be separated by newlines.")
            except Exception:
                raise Exceptions.PyChatGPTException(
                    "When resuming a chat, there was an issue reading id_log, make sure that it is formatted correctly.")

        # Check for access_token & access_token_expiry in env
        if OpenAI.token_expired():
            self.log(f"{Fore.RED}>> Access Token missing or expired."
                     f" {Fore.GREEN}Attempting to create them...")
            self._create_access_token()
        else:
            access_token, expiry, cookie = OpenAI.get_access_token()
            self.__auth_access_token = access_token
            self.__auth_access_token_expiry = expiry

            try:
                self.__auth_access_token_expiry = int(self.__auth_access_token_expiry)
            except ValueError:
                self.log(f"{Fore.RED}>> Expiry is not an integer.")
                raise Exceptions.PyChatGPTException("Expiry is not an integer.")

            if self.__auth_access_token_expiry < time.time():
                self.log(f"{Fore.RED}>> Your access token is expired. {Fore.GREEN}Attempting to recreate it...")
                self._create_access_token()

    def _create_access_token(self) -> bool:
        openai_auth = OpenAI.Auth(email_address=self.email, password=self.password, proxy=self.options.proxies)
        openai_auth.create_token()

        # If after creating the token, it's still expired, then something went wrong.
        is_still_expired = OpenAI.token_expired()
        if is_still_expired:
            self.log(f"{Fore.RED}>> Failed to create access token.")
            return False

        # If created, then return True
        return True

    def ask(self, prompt: str,
            conversation_id: str or None = None,
            parent_message_id: str or None = None,
            user=None,
            rep_queue: Queue or None = None
            ) -> Tuple[str or None, str or None, str or None] or None:

        if prompt is None:
            self.log(f"{Fore.RED}>> Enter a prompt.")
            raise Exceptions.PyChatGPTException("Enter a prompt.")

        if not isinstance(prompt, str):
            raise Exceptions.PyChatGPTException("Prompt must be a string.")

        if len(prompt) == 0:
            raise Exceptions.PyChatGPTException("Prompt cannot be empty.")

        if rep_queue is not None and not isinstance(rep_queue, Queue):
            raise Exceptions.PyChatGPTException("Cannot enter a non-queue object as the response queue for threads.")

        if conversation_id is None != parent_message_id is None:
            raise Exceptions.PyChatGPTException('ChatGPTUnofficialProxyAPI.sendMessage: conversation_id and parent_message_id must both be set or both be undefined')

        if conversation_id is not None and not is_valid_uuid_v4(conversation_id) :
            raise Exceptions.PyChatGPTException(
                'ChatGPTUnofficialProxyAPI.sendMessage: conversation_id is not a valid v4 UUID')

        if parent_message_id is not None and not is_valid_uuid_v4(parent_message_id) :
            raise Exceptions.PyChatGPTException(
                'ChatGPTUnofficialProxyAPI.sendMessage: parent_message_id is not a valid v4 UUID')

        # Check if the access token is expired
        if OpenAI.token_expired():
            self.log(f"{Fore.RED}>> Your access token is expired. {Fore.GREEN}Attempting to recreate it...")
            did_create = self._create_access_token()
            if did_create:
                self.log(f"{Fore.GREEN}>> Successfully recreated access token.")
            else:
                self.log(f"{Fore.RED}>> Failed to recreate access token.")
                raise Exceptions.PyChatGPTException("Failed to recreate access token.")

        # Get access token
        access_token = OpenAI.get_access_token()

        if conversation_id is not None:
            # get the conversation
            conversation_obj = Conversation.objects.get(id=conversation_id)
        # else:
        #     # create a new conversation
        #     conversation_obj = Conversation(user=user)
        #     conversation_obj.save()

        # Set conversation IDs if supplied
        if parent_message_id is not None:
            self.parent_message_id = parent_message_id
        if conversation_id is not None:
            self.conversation_id = conversation_id

        def stream_content():
            auth_token, expiry, cookie = access_token

            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {auth_token}',
                'Accept': 'text/event-stream',
                'Referer': 'https://chat.openai.com/chat?model=gpt-4',
                'Origin': 'https://chat.openai.com',
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15',
                'Cookie': f'{cookie}',
                'X-OpenAI-Assistant-App-Id': ''
            }

            if self.parent_message_id is None:
                self.parent_message_id = str(uuid.uuid4())

            if self.conversation_id is not None and len(self.conversation_id) == 0:
                # Empty string
                self.conversation_id = None

            if hasattr(self, 'proxies') and self.proxies is not None:
                session.proxies.update(self.proxies)

            data = {
                "action": "next",
                "messages": [
                    {
                        "id": str(uuid.uuid4()),
                        "role": "user",
                        "author": {"role": "user"},
                        "content": {"content_type": "text", "parts": [str(prompt)]},
                    }
                ],
                # "conversation_id": conversation_id,
                # "parent_message_id": parent_message_id,
                "model": "gpt-4"
            }
            if conversation_id is not None and len(conversation_id) > 0:
                data["conversation_id"] = conversation_id
            if parent_message_id is not None and len(parent_message_id) > 0:
                data["parent_message_id"] = parent_message_id
            else:
                data["parent_message_id"] = ""
            try:
                data_json = json.dumps(data)
                openai_response = session.post(
                    "https://chat.openai.com/backend-api/conversation",
                    headers=headers,
                    data=data_json,
                    stream=True
                )
                collected_events = []
                completion_text = ''
                if openai_response.status_code != 200:
                    raise Exceptions.PyChatGPTException(f"[Status Code] {openai_response.status_code} | "
                                                        f"[Response Text] {openai_response.text}")


                response_text = openai_response.text
                # iterate through the stream of events
                for line in response_text.split("\n\n"):
                    # filter out keep-alive new lines
                    if line:
                        # for event in response:
                        if line.startswith("data: {"):
                            line = line[6:]
                        if line.endswith("[DONE]"):
                            break

                        event = json.loads(line)
                        collected_events.append(event)  # save the event response

                        # todo web接口返回的结构和api不同
                        # {
                        #     "message": {
                        #         "id": "8a431ea6-b8c6-4858-9021-eac6fb57383a",
                        #         "author": {
                        #             "role": "system",
                        #             "name": null,
                        #             "metadata": {}
                        #         },
                        #         "create_time": 1679262119.453857,
                        #         "update_time": null,
                        #         "content": {
                        #             "content_type": "text",
                        #             "parts": [""]
                        #         },
                        #         "end_turn": true,
                        #         "weight": 1.0,
                        #         "metadata": {},
                        #         "recipient": "all"
                        #     },
                        #     "conversation_id": "f05a206e-3aaf-4d95-96ce-7ec98889dcdd",
                        #     "error": null
                        # }
                        # if debug
                        if settings.DEBUG:
                            print(event)
                        role = event['message']['author']['role']
                        if role == "system" or role == "user":
                            continue
                        if 'parts' in event['message']['content']:
                            event_text = event['message']['content']['parts'][0]
                            delta = event_text[len(completion_text):]
                            completion_text = event_text  # append the text
                            yield sse_pack('message', {'content': delta})

                conversation_id_returned = event['conversation_id']
                message_id_returned = event['message']['id']

                # conversation_id not set, save the new conversation
                if conversation_id is None:
                    # create a new conversation
                    conversation_obj = Conversation(id=conversation_id_returned, user=user)
                    conversation_obj.save()

                # insert the message user input
                user_message_obj = Message(
                    id=uuid.uuid4(),
                    conversation_id=conversation_id_returned,
                    parent_message_id=parent_message_id,
                    message=prompt
                )
                user_message_obj.save()

                # insert the message ai returned
                ai_message_obj = Message(
                    id=message_id_returned,
                    conversation_id=conversation_id_returned,
                    parent_message_id=user_message_obj.id,
                    message=completion_text,
                    is_bot=True
                )
                ai_message_obj.save()
                yield sse_pack('done', {'messageId': message_id_returned, 'conversationId': conversation_id_returned})

            except Exception as e:
                print(">> Error when calling OpenAI API: " + str(e))
                return "400", None, None
        return StreamingHttpResponse(stream_content(), content_type='text/event-stream')


    def save_data(self):
        if self.options.track:
            try:
                with open(self.options.chat_log, "a") as f:
                    f.write("\n".join(self.__chat_history) + "\n")

                with open(self.options.id_log, "w") as f:
                    f.write(str(self.parent_message_id) + "\n")
                    f.write(str(self.conversation_id) + "\n")

            except Exception as ex:
                self.log(f"{Fore.RED}>> Failed to save chat and ids to chat log and id_log."
                         f"{ex}")
            finally:
                self.__chat_history = []

    def cli_chat(self, rep_queue: Queue or None = None):
        """
        Start a CLI chat session.
        :param rep_queue:  A queue to put the prompt and response in.
        :return:
        """
        if rep_queue is not None and not isinstance(rep_queue, Queue):
            self.log(f"{Fore.RED}>> Entered a non-queue object to hold responses for another thread.")
            raise Exceptions.PyChatGPTException("Cannot enter a non-queue object as the response queue for threads.")

        # Check if the access token is expired
        if OpenAI.token_expired():
            self.log(f"{Fore.RED}>> Your access token is expired. {Fore.GREEN}Attempting to recreate it...")
            did_create = self._create_access_token()
            if did_create:
                self.log(f"{Fore.GREEN}>> Successfully recreated access token.")
            else:
                self.log(f"{Fore.RED}>> Failed to recreate access token.")
                raise Exceptions.PyChatGPTException("Failed to recreate access token.")
        else:
            self.log(f"{Fore.GREEN}>> Access token is valid.")
            self.log(f"{Fore.GREEN}>> Starting CLI chat session...")
            self.log(f"{Fore.GREEN}>> Type 'exit' to exit the chat session.")

        # Get access token
        access_token = OpenAI.get_access_token()

        while True:
            try:
                prompt = input("You: ")
                if prompt.replace("You: ", "") == "exit":
                    self.save_data()
                    break

                spinner = Spinner.Spinner()
                spinner.start(Fore.YELLOW + "Chat GPT is typing...")
                answer, previous_convo, convo_id = ChatHandler.ask(auth_token=access_token, prompt=prompt,
                                                                   conversation_id=self.conversation_id,
                                                                   parent_message_id=self.parent_message_id,
                                                                   proxies=self.options.proxies,
                                                                   pass_moderation=self.options.pass_moderation)

                if rep_queue is not None:
                    rep_queue.put((prompt, answer))

                if answer == "400" or answer == "401":
                    self.log(f"{Fore.RED}>> Failed to get a response from the API.")
                    return None

                self.conversation_id = convo_id
                self.parent_message_id = previous_convo
                spinner.stop()
                print(f"Chat GPT: {answer}")

                if self.options.track:
                    self.__chat_history.append("You: " + prompt)
                    self.__chat_history.append("Chat GPT: " + answer)

            except KeyboardInterrupt:
                print(f"{Fore.RED}>> Exiting...")
                break
            finally:
                self.save_data()


def is_valid_uuid_v4(uid: str) :
    try:
        uuid.UUID(uid)
        return True
    except ValueError:
        return False