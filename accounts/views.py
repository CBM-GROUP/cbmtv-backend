from django.shortcuts import render
from .serializers import RegisterSerializer, UserProfileSerializer, UserSerializer
from .models import User, RoleChangeLog 
from rest_framework import generics, permissions 
from rest_framework .views import APIView
from rest_framework .response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser

# Create your views here.
class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def profile(request):
    serializer = UserSerializer(request.user)
    return Response(serializer.data)


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