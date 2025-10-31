from django.urls import path
from .views import RegisterView, profile, AssignAdminRoleView, ListUsersView, GoogleDirectLoginView, CustomTokenObtainPairView, UserUpdateView, ChangePasswordView
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    path ('register/', RegisterView.as_view(), name='register'),
    path ('login/', CustomTokenObtainPairView.as_view(), name = 'login'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path ('profile/', profile, name='profile'),
    path ('assign-admin/', AssignAdminRoleView.as_view(), name= 'assign_admin'),
    path ('users/', ListUsersView, name='list_users'),
    path ('users/<int:pk>/', UserUpdateView.as_view(), name='user_update'),
    path ('users/<int:pk>/password/', ChangePasswordView.as_view(), name='user_change_password'),
    # path('login/google/', GoogleLoginView.as_view(), name='google_login'),
    path('login/google/direct/', GoogleDirectLoginView.as_view(), name='google_login_direct'),
]

