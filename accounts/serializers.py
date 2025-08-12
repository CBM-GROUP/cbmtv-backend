from rest_framework import serializers
from .models import User

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['email', 'name','phone', 'location', 'country', 'password']

    def create (self, validated_data):
        return User.objects.create_user(**validated_data, role='user')

class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model =User
        fields = ['id', 'email', 'name', 'phone', 'location', 'country', 'role']
