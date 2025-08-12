from rest_framework import viewsets
from .models import Channel
from .serializers import ChannelSerializer

# Create your views here.
class ChannelViewSet(viewsets.ModelViewSet):
    queryset = Channel.objects.all()
    serializer_class = ChannelSerializer