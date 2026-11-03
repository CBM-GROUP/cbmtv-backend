from django.urls import path
from .views import HomepageView, ManagementView, PreviewView

urlpatterns = [
    path('', HomepageView.as_view(), name='homepage'),
    path('manage/', ManagementView.as_view(), name='homepage-management'),
    path('preview/', PreviewView.as_view(), name='homepage-preview'),
]
