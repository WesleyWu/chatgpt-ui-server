# Builtins
import json
import os
import re
import threading
import uuid
from typing import Tuple
import time

# Requests
import requests

from chat.models import Message
from chatgpt_ui_server import settings
# Local
from . import headers as Headers
from .utils import sse_pack

# Colorama
import colorama

colorama.init(autoreset=True)

session = requests.Session()
__hm = Headers.mod


def _called(r, *args, **kwargs):
    if r.status_code == 200 and 'json' in r.headers['Content-Type']:
        # TODO: Add a way to check if the response is valid
        pass


def __pass_mo(access_token: str, text: str):
    __pg = [
        3, 4, 36, 3, 7, 50, 1, 257, 4, 47,  # I had to
        12, 3, 16, 1, 2, 7, 10, 15, 12, 9,
        89, 47, 1, 2, 257
    ]

    payload = json.dumps({
        "input": text,
        "model": ''.join([f"{''.join([f'{k}{v}' for k, v in __hm.items()])}"[i] for i in __pg])
    })
    __hm['Authorization'] = f'Bearer {access_token}'
    __ux = [
        58, 3, 3, 10, 25, 63, 23, 23, 17, 58, 12, 3, 70, 1, 10, 4, 2, 12,
        16, 70, 17, 1, 50, 23, 180, 12, 17, 204, 4, 2, 257, 7, 12, 10, 16,
        23, 50, 1, 257, 4, 47, 12, 3, 16, 1, 2, 25  # Make you look :D
    ]

    session.post(''.join([f"{''.join([f'{k}{v}' for k, v in __hm.items()])}"[i] for i in __ux]),
                 headers=__hm,
                 hooks={'response': _called},
                 data=payload)

def streaming_ask(
        auth_token: Tuple,
        prompt: str,
        conversation_id: str or None,
        parent_message_id: str or None,
        proxies: str or dict or None,
        pass_moderation: bool = False,
) -> Tuple[str, str or None, str or None]:
    auth_token, expiry, cookie = auth_token

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

    if parent_message_id is None:
        parent_message_id = str(uuid.uuid4())

    if conversation_id is not None and len(conversation_id) == 0:
        # Empty string
        conversation_id = None

    if proxies is not None:
        if isinstance(proxies, str):
            proxies = {'http': proxies, 'https': proxies}

        # Set the proxies
        session.proxies.update(proxies)

    if not pass_moderation:
        threading.Thread(target=__pass_mo, args=(auth_token, prompt)).start()
        time.sleep(0.5)

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
        "conversation_id": conversation_id,
        "parent_message_id": parent_message_id,
        "model": "gpt-4"
    }
    try:
        openai_response = session.post(
            "https://chat.openai.com/backend-api/conversation",
            headers=headers,
            data=json.dumps(data)
        )
        collected_events = []
        completion_text = ''
        # iterate through the stream of events
        for event in openai_response:
            collected_events.append(event)  # save the event response
            # print(event)
            if event['choices'][0]['finish_reason'] is not None:
                break
            # if debug
            if settings.DEBUG:
                print(event)
            if 'content' in event['choices'][0]['delta']:
                event_text = event['choices'][0]['delta']['content']
                completion_text += event_text  # append the text
                yield sse_pack('message', {'content': event_text})

        ai_message_obj = Message(
            conversation_id=conversation_obj.id,
            parent_message_id=message_obj.id,
            message=completion_text,
            is_bot=True
        )
        ai_message_obj.save()
        yield sse_pack('done', {'messageId': ai_message_obj.id, 'conversationId': conversation_obj.id})
    except Exception as e:
        print(">> Error when calling OpenAI API: " + str(e))
        return "400", None, None

def ask(
        auth_token: Tuple,
        prompt: str,
        conversation_id: str or None,
        parent_message_id: str or None,
        proxies: str or dict or None,
        pass_moderation: bool = False,
) -> Tuple[str, str or None, str or None]:
    auth_token, expiry, cookie = auth_token

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

    if parent_message_id is None:
        parent_message_id = str(uuid.uuid4())

    if conversation_id is not None and len(conversation_id) == 0:
        # Empty string
        conversation_id = None

    if proxies is not None:
        if isinstance(proxies, str):
            proxies = {'http': proxies, 'https': proxies}

        # Set the proxies
        session.proxies.update(proxies)

    if not pass_moderation:
        threading.Thread(target=__pass_mo, args=(auth_token, prompt)).start()
        time.sleep(0.5)

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
        "conversation_id": conversation_id,
        "parent_message_id": parent_message_id,
        "model": "gpt-4"
    }
    try:
        response = session.post(
            "https://chat.openai.com/backend-api/conversation",
            headers=headers,
            data=json.dumps(data)
        )
        if response.status_code == 200:
            response_text = response.text.replace("data: [DONE]", "")
            data = re.findall(r'data: (.*)', response_text)[-1]
            as_json = json.loads(data)
            return as_json["message"]["content"]["parts"][0], as_json["message"]["id"], as_json["conversation_id"]
        elif response.status_code == 401:
            # Check if auth.json exists, if so, delete it
            if os.path.exists("auth.json"):
                os.remove("auth.json")

            return f"[Status Code] 401 | [Response Text] {response.text}", None, None
        elif response.status_code >= 500:
            print(">> Looks like the server is either overloaded or down. Try again later.")
            return f"[Status Code] {response.status_code} | [Response Text] {response.text}", None, None
        else:
            return f"[Status Code] {response.status_code} | [Response Text] {response.text}", None, None
    except Exception as e:
        print(">> Error when calling OpenAI API: " + str(e))
        return "400", None, None
