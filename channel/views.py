from rest_framework import viewsets
from rest_framework import permissions
from .models import Channel
from .serializers import ChannelSerializer

# Create your views here.
class ChannelViewSet(viewsets.ModelViewSet):
    queryset = Channel.objects.all()
    serializer_class = ChannelSerializer

    def get_permissions(self):
        if self.request.method in ('GET', 'POST',   'HEAD', 'OPTIONS'):
            return [permissions.IsAuthenticated()]
        return [permissions.IsAdminUser()]