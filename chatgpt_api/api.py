import os
import json

import openai
import tiktoken
from django.http import StreamingHttpResponse, HttpResponse, JsonResponse
from requests import Response
from rest_framework import status

from chat.models import Message, Conversation, Setting
from chatgpt_ui_server import settings
from .classes.utils import sse_pack


class ChatGptApi:
    api_base_url = "https://api.openai.com/v1"
    api_key = ''
    debug = True
    temperature = 0.8
    top_p = 1.0
    presence_penalty = 1.0

    # Creates a new client wrapper around OpenAI's chat completion API, mimicing the official ChatGPT webapp's
    # functionality as closely as possible.
    #
    # @param api_key - OpenAI API key (required).
    # @param api_base_url - Optional override for the OpenAI API base URL.
    # @param debug - Optional enables logging debugging info to stdout.
    # @param model - ID of the model to use. Currently, only `gpt-3.5-turbo` and `gpt-3.5-turbo-0301` are supported.
    # @param temperature - What sampling temperature to use, between 0 and 2. Higher values like 0.8 will
    #   make the output more random, while lower values like 0.2 will make it more focused and deterministic.
    #   We generally recommend altering this or `top_p` but not both.
    # @param top_p - An alternative to sampling with temperature, called nucleus sampling, where the model considers
    #   the results of the tokens with top_p probability mass. So 0.1 means only the tokens comprising the
    #   top 10% probability mass are considered.  We generally recommend altering this or `temperature` but not both.
    # @param presence_penalty - Number between -2.0 and 2.0. Positive values penalize new tokens based on
    #   whether they appear in the text so far, increasing the model\'s likelihood to talk about new topics.
    #   [See more information about frequency and presence penalties.](/docs/api-reference/parameter-details)
    def __init__(self, api_key=None, api_base_url=None, debug=True, temperature=None, top_p=None,
                 presence_penalty=None):
        if api_base_url is not None:
            self.api_base_url = api_base_url
        if api_key is not None:
            self.api_key = api_key
        else:
            self.api_key = get_openai_api_key()
        if debug is not None:
            self.debug = debug
        if temperature is not None:
            self.temperature = temperature
        if top_p is not None:
            self.top_p = top_p
        if presence_penalty is not None:
            self.presence_penalty = presence_penalty

    def send_message(self, message, conversation_id, parent_message_id, user, max_tokens=None,
                     temperature=None, top_p=None, frequency_penalty=None, presence_penalty=None, stream=True):
        model = get_current_model()
        if conversation_id:
            # get the conversation
            conversation_obj = Conversation.objects.get(id=conversation_id)
        else:
            # create a new conversation
            conversation_obj = Conversation(user=user)
            conversation_obj.save()
        # insert a new message
        message_obj = Message(
            conversation_id=conversation_obj.id,
            parent_message_id=parent_message_id,
            message=message
        )
        message_obj.save()

        try:
            messages = build_messages(conversation_obj)

            if settings.DEBUG:
                print(messages)
        except ValueError as e:
            return Response(
                {
                    'error': e
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        # print(prompt)

        num_tokens = num_tokens_from_messages(messages)
        max_tokens = min(model['max_tokens'] - num_tokens, model['max_response_tokens'])

        def normal_content():
            my_openai = self.get_openai()

            openai_response = my_openai.ChatCompletion.create(
                model=model['name'],
                messages=messages,
                max_tokens=max_tokens,
                temperature=self.temperature,
                top_p=self.top_p,
                frequency_penalty=0,
                presence_penalty=self.presence_penalty,
                stream=True,
            )
            completion_text = ''
            # iterate through the stream of events
            for event in openai_response:
                # print(event)
                if event['choices'][0]['finish_reason'] is not None:
                    break
                # if debug
                if settings.DEBUG:
                    print(event)
                if 'content' in event['choices'][0]['delta']:
                    event_text = event['choices'][0]['delta']['content']
                    completion_text += event_text  # append the text

            ai_message_obj = Message(
                conversation_id=conversation_obj.id,
                parent_message_id=message_obj.id,
                message=completion_text,
                is_bot=True
            )
            ai_message_obj.save()
            return {'messageId': ai_message_obj.id, 'conversationId': conversation_obj.id, 'content': completion_text}


        def stream_content():
            my_openai = self.get_openai()

            openai_response = my_openai.ChatCompletion.create(
                model=model['name'],
                messages=messages,
                max_tokens=max_tokens,
                temperature=self.temperature,
                top_p=self.top_p,
                frequency_penalty=0,
                presence_penalty=self.presence_penalty,
                stream=stream,
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

        if stream:
            return StreamingHttpResponse(stream_content(), content_type='text/event-stream')
        else:
            return JsonResponse(normal_content())

    def get_openai(self):
        openai.api_key = self.api_key
        proxy = os.getenv('OPENAI_API_PROXY')
        if proxy:
            openai.api_base = proxy
        return openai


def get_openai_api_key():
    row = Setting.objects.filter(name='openai_api_key').first()
    if row:
        return row.value
    return None


def get_current_model():
    model = {
        'name': 'gpt-3.5-turbo',
        'max_tokens': 4096,
        'max_prompt_tokens': 3096,
        'max_response_tokens': 1000
    }
    return model


def num_tokens_from_messages(messages, model="gpt-3.5-turbo"):
    """Returns the number of tokens used by a list of messages."""
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    if model == "gpt-3.5-turbo":  # note: future models may deviate from this
        num_tokens = 0
        for message in messages:
            num_tokens += 4  # every message follows <im_start>{role/name}\n{content}<im_end>\n
            for key, value in message.items():
                num_tokens += len(encoding.encode(value))
                if key == "name":  # if there's a name, the role is omitted
                    num_tokens += -1  # role is always required and always 1 token
        num_tokens += 2  # every reply is primed with <im_start>assistant
        return num_tokens
    else:
        raise NotImplementedError(f"""num_tokens_from_messages() is not presently implemented for model {model}. See 
        https://github.com/openai/openai-python/blob/main/chatml.md for information on how messages are converted to 
        tokens.""")


def build_messages(conversation_obj):
    model = get_current_model()

    ordered_messages = Message.objects.filter(conversation=conversation_obj).order_by('created_at')
    ordered_messages_list = list(ordered_messages)

    system_messages = [{"role": "system", "content": "You are a helpful assistant."}]

    current_token_count = num_tokens_from_messages(system_messages, model['name'])

    max_token_count = model['max_prompt_tokens']

    messages = []

    while current_token_count < max_token_count and len(ordered_messages_list) > 0:
        message = ordered_messages_list.pop()
        role = "assistant" if message.is_bot else "user"
        new_message = {"role": role, "content": message.message}
        new_token_count = num_tokens_from_messages(system_messages + messages + [new_message])
        if new_token_count > max_token_count:
            if len(messages) > 0:
                break
            raise ValueError(
                f"Prompt is too long. Max token count is {max_token_count}, but prompt is {new_token_count} tokens long.")
        messages.insert(0, new_message)
        current_token_count = new_token_count

    return system_messages + messages

