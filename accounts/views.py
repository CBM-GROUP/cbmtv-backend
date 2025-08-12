from django.shortcuts import render
from .serializers import RegisterSerializer, UserProfileSerializer
from .models import User, RoleChangeLog 
from rest_framework import generics, permissions 
from rest_framework .views import APIView
from rest_framework .response import Response
from rest_framework import status

# Create your views here.
class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer

class UserProfileView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserProfileSerializer

    def get_object(self):
        return self.request.user


class AssignAdminRoleView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):

        if not request.user.is_superuser:
            return Response(
                {"error": "Only superadmin can assign admin role."}, 
            status=status.HTTP_403_FORBIDDEN
            )

        user_id = request.data.get("user_id")
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response ({"error ": "User not found"}, status = status.HTTP_404_NOT_FOUND)


        old_role = user.role
        user.role = "admin"
        user.is_staff = True
        user.save()
            
        
        RoleChangeLog.objects.create(
            changed_by = request.user,
            changed_user=user, 
            old_role= old_role,
            new_role="admin"
        )

        return Response({"message": "User promoted to admin"}, status = status.HTTP_200_OK)