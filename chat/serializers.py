from rest_framework import serializers
from .models import Conversation, Message, Prompt


class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ['id', 'topic', 'created_at']


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ['parent_message', 'message', 'is_bot', 'created_at']


class PromptSerializer(serializers.ModelSerializer):
    class Meta:
        model = Prompt
        fields = ['id', 'prompt', 'created_at', 'updated_at']
