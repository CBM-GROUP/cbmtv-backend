from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import User

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['email', 'name','phone', 'location', 'country', 'password']

    def create (self, validated_data):
        return User.objects.create_user(**validated_data, role='user')

class GoogleDirectAuthSerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=True, required=False)
    email = serializers.EmailField()
    google_id = serializers.CharField()

    def validate(self, attrs):
        if not attrs.get('google_id'):
            raise serializers.ValidationError({'google_id': 'This field is required.'})
        if not attrs.get('email'):
            raise serializers.ValidationError({'email': 'This field is required.'})
        return attrs

class UserProfileSerializer(serializers.ModelSerializer):
    image = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    class Meta:
        model =User
        fields = ['id', 'email', 'name', 'phone', 'location', 'country', 'image', 'role', 'auth_provider', 'is_staff', 'is_superuser']


class UserSerializer(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'name', 'phone', 'location', 'country', 'image', 'role', 'auth_provider', 'is_staff', 'is_superuser']

    def get_username(self, obj):
        # Map username to display name field in our model
        return getattr(obj, 'name', obj.email)


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        user: User = self.user

        data['user'] = {
            'id': user.id,
            'email': user.email,
            'name': getattr(user, 'name', ''),
            'phone': getattr(user, 'phone', ''),
            'location': getattr(user, 'location', ''),
            'country': getattr(user, 'country', ''),
            'image': getattr(user, 'image', None),
        }
        return data