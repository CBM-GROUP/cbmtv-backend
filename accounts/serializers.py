from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User
from .models import DashboardRole
from .permissions import DASHBOARD_SECTIONS, dashboard_permissions

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    image = serializers.URLField(required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = User
        fields = ['email', 'name', 'phone', 'location', 'country', 'password', 'image']

    def create(self, validated_data):
        image = validated_data.pop('image', None)
        user = User.objects.create_user(
            **validated_data,
            image=image,
            role='user'
        )
        return user

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
    dashboard_role_name = serializers.CharField(source='dashboard_role.name', read_only=True, default=None)
    dashboard_permissions = serializers.SerializerMethodField()
    dashboard_status = serializers.SerializerMethodField()

    def get_dashboard_permissions(self, obj):
        return dashboard_permissions(obj)

    def get_dashboard_status(self, obj):
        from django.utils import timezone
        if not obj.is_active:
            return 'inactive'
        if obj.must_change_password:
            if obj.temporary_pin_expires_at and obj.temporary_pin_expires_at > timezone.now():
                return 'invited'
            return 'pending_activation'
        return 'active'

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value.strip()).exclude(pk=getattr(self.instance, 'pk', None)).exists():
            raise serializers.ValidationError('An account with this email already exists.')
        return value.strip()

    class Meta:
        model =User
        fields = ['id', 'email', 'name', 'phone', 'location', 'country', 'image', 'role', 'auth_provider', 'is_staff', 'is_superuser', 'is_active', 'dashboard_role', 'dashboard_role_name', 'dashboard_permissions', 'dashboard_status', 'must_change_password']
        read_only_fields = ['role', 'auth_provider', 'is_staff', 'is_superuser', 'is_active', 'dashboard_role', 'must_change_password']


class UserSerializer(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()
    dashboard_role_name = serializers.CharField(source='dashboard_role.name', read_only=True, default=None)
    dashboard_permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'name', 'phone', 'location', 'country', 'image', 'role', 'auth_provider', 'is_staff', 'is_superuser', 'is_active', 'dashboard_role', 'dashboard_role_name', 'dashboard_permissions', 'must_change_password']

    def get_username(self, obj):
        # Map username to display name field in our model
        return getattr(obj, 'name', obj.email)

    def get_dashboard_permissions(self, obj):
        return dashboard_permissions(obj)


class DashboardRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = DashboardRole
        fields = ['id', 'name', 'permissions']

    def validate_permissions(self, value):
        if not isinstance(value, list) or any(not isinstance(item, str) or item not in DASHBOARD_SECTIONS for item in value):
            raise serializers.ValidationError('Choose valid dashboard sections.')
        if len(value) != len(set(value)):
            raise serializers.ValidationError('Each permission may be selected once.')
        return value


class DashboardInviteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    email = serializers.EmailField()
    dashboard_role = serializers.PrimaryKeyRelatedField(queryset=DashboardRole.objects.all())

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value.strip()).exists():
            raise serializers.ValidationError('An account with this email already exists.')
        return value.strip()


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        user: User = self.user

        if user.must_change_password:
            raise serializers.ValidationError({'detail': 'Activate your dashboard account with the temporary PIN before signing in.', 'code': 'activation_required'})

        data['user'] = {
            'id': user.id,
            'email': user.email,
            'name': getattr(user, 'name', ''),
            'phone': getattr(user, 'phone', ''),
            'location': getattr(user, 'location', ''),
            'country': getattr(user, 'country', ''),
            'image': getattr(user, 'image', None),
            'role': user.role,
            'is_staff': user.is_staff,
            'is_superuser': user.is_superuser,
            'is_active': user.is_active,
            'dashboard_permissions': dashboard_permissions(user),
            'dashboard_role': user.dashboard_role_id,
        }
        return data


class ActiveTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        token = RefreshToken(attrs['refresh'])
        user = User.objects.filter(pk=token.get('user_id'), is_active=True, must_change_password=False).first()
        if user is None:
            raise serializers.ValidationError({'detail': 'Account is inactive or requires activation.'})
        return super().validate(attrs)
