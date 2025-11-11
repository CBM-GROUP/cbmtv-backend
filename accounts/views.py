from django.shortcuts import render
from .serializers import (
    RegisterSerializer,
    UserProfileSerializer,
    UserSerializer,
    GoogleDirectAuthSerializer,
    CustomTokenObtainPairSerializer,
)
from .models import User, RoleChangeLog 
from rest_framework import generics, permissions 
from rest_framework .views import APIView
from rest_framework .response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework import parsers

# Create your views here.
class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def profile(request):
    serializer = UserSerializer(request.user)
    return Response(serializer.data)


class UserUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [parsers.JSONParser, parsers.FormParser, parsers.MultiPartParser]

    def patch(self, request, pk):
        return self._update(request, pk, partial=True)

    def put(self, request, pk):
        return self._update(request, pk, partial=False)

    def _update(self, request, pk, partial: bool):
        # Fetch target user
        try:
            target_user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        # Allow any authenticated user to edit profile fields by user id
        data = request.data.copy()
        allowed_fields = {"name", "phone", "location", "country", "image", "email"}
        data = {k: v for k, v in data.items() if k in allowed_fields}

        serializer = UserProfileSerializer(target_user, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)


class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        return self._change_password(request, pk)

    def patch(self, request, pk):
        return self._change_password(request, pk)

    def _change_password(self, request, pk):
        try:
            target_user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        current_password = request.data.get('current_password', '')
        new_password = request.data.get('new_password', '')
        confirm_password = request.data.get('confirm_password', '')

        if not current_password or not new_password or not confirm_password:
            return Response({"detail": "current_password, new_password and confirm_password are required"}, status=status.HTTP_400_BAD_REQUEST)

        if not target_user.check_password(current_password):
            return Response({"detail": "Current password is incorrect"}, status=status.HTTP_400_BAD_REQUEST)

        if new_password != confirm_password:
            return Response({"detail": "New password and confirm password do not match"}, status=status.HTTP_400_BAD_REQUEST)

        if current_password == new_password:
            return Response({"detail": "New password must be different from current password"}, status=status.HTTP_400_BAD_REQUEST)

        target_user.set_password(new_password)
        target_user.save(update_fields=['password'])

        return Response({"detail": "Password updated successfully"}, status=status.HTTP_200_OK)


class AssignAdminRoleView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):

        user_id = request.data.get("user_id")
        role = request.data.get("role")

        # Validate requested role value
        if role not in ["admin", "internal_admin"]:
            return Response({"detail": "Invalid role"}, status=status.HTTP_400_BAD_REQUEST)

        # Role hierarchy permissions
        if request.user.is_superuser:
            allowed_roles = ["admin", "internal_admin"]
        elif getattr(request.user, "role", None) == "admin":
            allowed_roles = ["internal_admin"]
        else:
            return Response({"detail": "You do not have permission"}, status=status.HTTP_403_FORBIDDEN)

        # Ensure requested role is within the caller's allowance
        if role not in allowed_roles:
            return Response({"detail": f"Admins cannot assign role '{role}'"}, status=status.HTTP_403_FORBIDDEN)

        # Fetch target user
        try:
            target_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        # Apply role change
        old_role = target_user.role
        target_user.role = role
        # Ensure staff privileges for elevated roles
        target_user.is_staff = True
        target_user.save()

        # Log the change
        RoleChangeLog.objects.create(
            changed_by=request.user,
            changed_user=target_user,
            old_role=old_role,
            new_role=role
        )

        return Response({"detail": f"{target_user.email} role updated to {role}"}, status=status.HTTP_200_OK)


# adding new function listing users
@api_view(['GET'])
@permission_classes([permissions.IsAdminUser])
def ListUsersView(request):
    """
    List all users, including their roles and basic info.
    Accessible omly by superusers or admins.

    """
    users = User.objects.all()
    serializer = UserProfileSerializer(users, many=True)
    return Response(serializer.data)


class GoogleDirectLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = GoogleDirectAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data.get('name') or ''
        email = serializer.validated_data['email']
        google_id = serializer.validated_data['google_id']

        # Find or create user by email
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                'name': name or email.split('@')[0],
                'phone': '',
                'location': '',
                'country': '',
                'role': 'user',
                'auth_provider': 'google',
            }
        )

        # Ensure provider is google
        if user.auth_provider != 'google':
            user.auth_provider = 'google'
            user.save(update_fields=['auth_provider'])

        # Issue JWT tokens using SimpleJWT
        refresh = RefreshToken.for_user(user)
        
        # Image is stored as a plain link. Return it as-is.
        
        return Response({
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'login': 'register' if created else 'login',
            'user': {
                'id': user.id,
                'email': user.email,
                'name': getattr(user, 'name', ''),
                'phone': getattr(user, 'phone', ''),
                'location': getattr(user, 'location', ''),
                'country': getattr(user, 'country', ''),
                'image': getattr(user, 'image', None),
            },
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer