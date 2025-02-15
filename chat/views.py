import os
import json
import openai
import datetime
import tiktoken

from chatgpt_api import Chat, Options
from chatgpt_api.api import ChatGptApi
from .models import Conversation, Message, Setting, Prompt
from django.conf import settings
from django.http import StreamingHttpResponse
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes, action
from .serializers import ConversationSerializer, MessageSerializer, PromptSerializer


class ConversationViewSet(viewsets.ModelViewSet):
    serializer_class = ConversationSerializer
    # authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user).order_by('-created_at')

    @action(detail=False, methods=['delete'])
    def delete_all(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        queryset.delete()
        return Response(status=204)


class MessageViewSet(viewsets.ModelViewSet):
    serializer_class = MessageSerializer
    # authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Message.objects.filter(conversation_id=self.request.query_params.get('conversationId')).order_by(
            'created_at')


class PromptViewSet(viewsets.ModelViewSet):
    serializer_class = PromptSerializer
    # authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Prompt.objects.filter(user=self.request.user).order_by('-created_at')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        serializer.validated_data['user'] = request.user

        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=False, methods=['delete'])
    def delete_all(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        queryset.delete()
        return Response(status=204)


@api_view(['POST'])
# @authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def gen_title(request):
    conversation_id = request.data.get('conversationId')
    conversation_obj = Conversation.objects.get(id=conversation_id)
    message = Message.objects.filter(conversation_id=conversation_id).order_by('created_at').first()

    messages = [
        {"role": "user",
         "content": 'Generate a short title for the following content, no more than 10 words: \n\n "%s"' % message.message},
    ]

    model = get_current_model()

    my_openai = get_openai()
    try:
        openai_response = my_openai.ChatCompletion.create(
            model=model['name'],
            messages=messages,
            max_tokens=256,
            temperature=0.5,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0,
        )
        completion_text = openai_response['choices'][0]['message']['content']
        title = completion_text.strip().replace('"', '')
    except Exception as e:
        print(e)
        title = 'Untitled Conversation'
    # update the conversation title
    conversation_obj.topic = title
    conversation_obj.save()
    return Response({
        'title': title
    })


@api_view(['POST'])
# @authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def conversation(request):
    message = request.data.get('message')
    conversation_id = request.data.get('conversationId')
    parent_message_id = request.data.get('parentMessageId')

    def api_send_message():
        api = ChatGptApi(get_openai_api_key())
        return api.send_message(message=message, conversation_id=conversation_id, parent_message_id=parent_message_id,
                                user=request.user, stream=True)

    def api_unofficial_send_message():
        options = Options()

        # [New] Pass Moderation. https://github.com/rawandahmad698/PyChatGPT/discussions/103
        # options.pass_moderation = False

        # [New] Enable, Disable logs
        options.log = True

        # Track conversation
        options.track = True

        # Use a proxy
        # options.proxies = 'http://localhost:1087'

        # Optionally, you can pass a file path to save the conversation
        # They're created if they don't exist

        # options.chat_log = "chat_log.txt"
        # options.id_log = "id_log.txt"

        # Create a Chat object
        chat = Chat(email="audiofirst2019@gmail.com", password="TryitNow!!", options=options)
        return chat.ask(prompt=message, conversation_id=conversation_id, parent_message_id=parent_message_id,
                        user=request.user)

    return api_unofficial_send_message()


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


def get_current_model():
    model = {
        'name': 'gpt-3.5-turbo',
        'max_tokens': 4096,
        'max_prompt_tokens': 3096,
        'max_response_tokens': 1000
    }
    return model


def get_openai_api_key():
    row = Setting.objects.filter(name='openai_api_key').first()
    if row:
        return row.value
    return None


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


def get_openai():
    openai.api_key = get_openai_api_key()
    proxy = os.getenv('OPENAI_API_PROXY')
    if proxy:
        openai.api_base = proxy
    return openai
