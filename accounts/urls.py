from django.urls import path
from .views import RegisterView, UserProfileView, AssignAdminRoleView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

urlpatterns = [
    path ('register/', RegisterView.as_view(), name='register'),
    path ('login/', TokenObtainPairView.as_view(), name = 'login'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path ('profile/', UserProfileView.as_view(), name='profile'),
    path ('assign-admin/', AssignAdminRoleView.as_view(), name= 'assign_admin'),
]

