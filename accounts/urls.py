from django.urls import path
from .views import RegisterView, profile, AssignAdminRoleView, ListUsersView, GoogleDirectLoginView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

urlpatterns = [
    path ('register/', RegisterView.as_view(), name='register'),
    path ('login/', TokenObtainPairView.as_view(), name = 'login'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path ('profile/', profile, name='profile'),
    path ('assign-admin/', AssignAdminRoleView.as_view(), name= 'assign_admin'),
    path ('users/', ListUsersView, name='list_users'),
    # path('login/google/', GoogleLoginView.as_view(), name='google_login'),
    path('login/google/direct/', GoogleDirectLoginView.as_view(), name='google_login_direct'),
]

