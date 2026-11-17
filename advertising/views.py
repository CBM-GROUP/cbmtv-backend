import math
from django.conf import settings
from django.db import transaction, models
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, viewsets, serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView
from accounts.permissions import HasDashboardSection, dashboard_permissions
from accounts.throttles import TrustedProxyRateThrottle
from common.http import PrivateResponseMixin
from common.pagination import StandardPagination
from common.multipart_upload import _client
from content.models import MediaAsset
from content.models import Content, Genre
from content.publication import public_eligibility
from content.taxonomy import CATEGORY_CHOICES
from channel.models import Channel
from homepage.models import HomepageConfiguration
from .constants import PLACEMENTS
from .models import Advertiser, Campaign, Creative, Placement, Audit, Decision, DeliveryAllocation, AdvertisingOperation
from .serializers import AdvertiserSerializer, CampaignSerializer, CreativeSerializer, PlacementSerializer, DeliveryAllocationSerializer, creative_ready, media_duration
from .delivery import context_session, receipt, authorized_session, decide, ingest, identifier, placement_allowed, advertising_available
from .media import upload_image, StorageUnavailable
from .reports import report


class HasAdvertisingAccess(permissions.BasePermission):
    """New scoped grants coexist with the existing adverts grant."""
    def has_permission(self, request, view):
        granted = set(dashboard_permissions(request.user))
        resource = getattr(view, 'resource_permission', 'advertising_view')
        if getattr(view, 'action', None) == 'create' and isinstance(view, CreativeViewSet):
            resource = 'advertising_upload_creatives'
        if getattr(view, 'action', None) in ('list', 'retrieve') or request.method in ('GET', 'HEAD', 'OPTIONS'):
            return bool({'adverts', getattr(view, 'read_permission', 'advertising_view'), resource} & granted)
        if getattr(view, 'action', None) == 'workflow':
            return bool({'adverts', resource, 'advertising_approval', 'advertising_approve_creatives', 'advertising_activate'} & granted)
        return bool({'adverts', resource} & granted)


class AdAnonThrottle(AnonRateThrottle):
    rate = '60/min'
    get_ident = TrustedProxyRateThrottle.get_ident


class AdUserThrottle(UserRateThrottle):
    rate = '240/min'
    get_ident = TrustedProxyRateThrottle.get_ident


class StaffViewSet(PrivateResponseMixin, viewsets.ModelViewSet):
    permission_classes = [HasAdvertisingAccess]
    required_section = 'adverts'
    pagination_class = StandardPagination
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def audit(self, instance, action_name):
        Audit.objects.create(actor=self.request.user, resource=instance._meta.model_name, resource_id=str(instance.pk), action=action_name)

    def perform_create(self, serializer):
        with transaction.atomic():
            instance = serializer.save()
            if isinstance(instance, Creative):
                MediaAsset.objects.filter(pk=instance.media_id, purpose='protected', state='uploaded').update(state='queued')
            self.audit(instance, 'created')

    def update(self, request, *args, **kwargs):
        with transaction.atomic():
            instance = self.get_object()
            if isinstance(instance, (Campaign, Creative)) and (isinstance(instance, Campaign) or instance.campaign_id):
                Campaign.objects.select_for_update(of=('self',)).get(pk=instance.pk if isinstance(instance, Campaign) else instance.campaign_id)
            instance = self.get_queryset().select_for_update(of=('self',)).get(pk=instance.pk)
            serializer = self.get_serializer(instance, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            old_default = getattr(instance, 'default_destination_url', None)
            if isinstance(instance, Campaign):
                serializer.save(approval='draft', status='paused')
                Decision.objects.filter(campaign=instance, terminal_at__isnull=True).update(terminal_at=timezone.now(), reason='configuration_changed')
                if old_default != instance.default_destination_url:
                    Creative.objects.filter(campaign=instance, destination_url='').update(approval='draft', updated_at=timezone.now())
            elif isinstance(instance, Creative):
                serializer.save(approval='draft')
                Decision.objects.filter(creative=instance, terminal_at__isnull=True).update(terminal_at=timezone.now(), reason='configuration_changed')
                MediaAsset.objects.filter(pk=instance.media_id, purpose='protected', state='uploaded').update(state='queued')
            else:
                serializer.save()
                if isinstance(instance, Advertiser) and old_default != instance.default_destination_url:
                    inherited = Creative.objects.filter(advertiser=instance, destination_url='').filter(
                        models.Q(campaign__isnull=True) | models.Q(campaign__default_destination_url=''))
                    Decision.objects.filter(creative__in=inherited, terminal_at__isnull=True).update(
                        terminal_at=timezone.now(), reason='configuration_changed')
                    inherited.update(approval='draft', updated_at=timezone.now())
            self.audit(instance, 'updated')
            return Response(serializer.data)


class AdvertiserViewSet(StaffViewSet):
    resource_permission = 'advertising_manage_clients'
    queryset = Advertiser.objects.order_by('-pk')
    serializer_class = AdvertiserSerializer


class WorkflowViewSet(StaffViewSet):
    @action(detail=True, methods=['post'])
    def workflow(self, request, pk=None):
        if not isinstance(request.data, dict):
            raise ValidationError('Provide an object containing the workflow action.')
        operation = request.data.get('action')
        if operation in ('resume', 'pause', 'complete') and not ({'adverts', 'advertising_activate'} & set(dashboard_permissions(request.user))):
            raise PermissionDenied('Advertising activation permission is required.')
        with transaction.atomic():
            current = self.get_object()
            granted = set(dashboard_permissions(request.user))
            if operation == 'submit':
                required = 'advertising_create_campaigns' if isinstance(current, Campaign) else 'advertising_edit_creatives'
                if not ({'adverts', required} & granted):
                    raise PermissionDenied('Advertising editing permission is required.')
            if operation in ('approve', 'reject'):
                required = {'advertising_approval'} if isinstance(current, Campaign) else {'advertising_approval', 'advertising_approve_creatives'}
                if not required & granted:
                    raise PermissionDenied('Advertising approval permission is required.')
            if isinstance(current, Campaign) or current.campaign_id:
                Campaign.objects.select_for_update(of=('self',)).get(pk=current.pk if isinstance(current, Campaign) else current.campaign_id)
            current = self.get_queryset().select_for_update(of=('self',)).get(pk=current.pk)
            if operation == 'submit' and current.approval in ('draft', 'rejected'):
                current.approval = 'review'
            elif operation in ('approve', 'reject') and current.approval == 'review':
                if operation == 'approve':
                    if isinstance(current, Creative) and not creative_ready(current):
                        raise ValidationError('Preview ready media before approving. Video must be no longer than five minutes.')
                    if isinstance(current, Creative) and not Audit.objects.filter(resource='creative', resource_id=str(current.pk),
                            action='previewed', created_at__gte=current.updated_at).exists():
                        raise ValidationError('Preview this creative after its latest changes before approving.')
                    if isinstance(current, Creative) and current.kind == 'video':
                        current.duration_seconds = math.ceil(media_duration(current.media))
                    # Revalidate targets, destinations, publication and asset relationships.
                    validation = self.get_serializer(current, data=self.get_serializer(current).data, partial=True)
                    validation.is_valid(raise_exception=True)
                current.approval = 'approved' if operation == 'approve' else 'rejected'
            elif isinstance(current, Campaign) and operation == 'pause' and current.status != 'completed':
                current.status = 'paused'
            elif isinstance(current, Campaign) and operation == 'resume' and current.approval == 'approved' and current.status != 'completed':
                if (not current.advertiser.active or not current.starts_at or not current.ends_at
                        or current.ends_at <= timezone.now() or not current.placements
                        or not current.creatives.filter(approval='approved').exists()):
                    raise ValidationError('Resumption requires an active advertiser, eligible schedule and approved creative.')
                current.status = 'active'
            elif isinstance(current, Campaign) and operation == 'complete':
                current.status = 'completed'
            else:
                raise ValidationError('This workflow transition is unavailable.')
            current.save()
            self.audit(current, operation)
            return Response(self.get_serializer(current).data)


class CampaignViewSet(WorkflowViewSet):
    resource_permission = 'advertising_create_campaigns'
    queryset = Campaign.objects.select_related('advertiser').order_by('-pk')
    serializer_class = CampaignSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get('advertiser'):
            value = serializers.IntegerField(min_value=1).run_validation(self.request.query_params['advertiser'])
            queryset = queryset.filter(advertiser_id=value)
        return queryset


class CreativeViewSet(WorkflowViewSet):
    resource_permission = 'advertising_edit_creatives'
    queryset = Creative.objects.select_related('advertiser', 'campaign', 'campaign__advertiser', 'media', 'image').order_by('-pk')
    serializer_class = CreativeSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get('campaign'):
            value = serializers.IntegerField(min_value=1).run_validation(self.request.query_params['campaign'])
            queryset = queryset.filter(campaign_id=value)
        if self.request.query_params.get('advertiser'):
            value = serializers.IntegerField(min_value=1).run_validation(self.request.query_params['advertiser'])
            queryset = queryset.filter(advertiser_id=value)
        return queryset

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def preview(self, request, pk=None):
        creative = self.get_object()
        if creative.campaign_id:
            Campaign.objects.select_for_update(of=('self',)).get(pk=creative.campaign_id)
        creative = self.get_queryset().select_for_update(of=('self',)).get(pk=creative.pk)
        if creative.kind == 'image':
            self.audit(creative, 'previewed')
            return Response({'kind': 'image', 'url': creative.image.desktop_url, 'mobile_url': creative.image.mobile_url})
        if creative.media.state in ('uploading', 'canceled'):
            raise ValidationError('Complete the upload before previewing.')
        try:
            url = _client(settings.MEDIA_STORAGE).generate_presigned_url('get_object', Params={
                'Bucket': settings.MEDIA_STORAGE['S3_BUCKET_NAME'], 'Key': creative.media.source_key}, ExpiresIn=300)
        except Exception:
            raise StorageUnavailable() from None
        self.audit(creative, 'previewed')
        return Response({'kind': 'video', 'url': url, 'expires_in': 300})


class PlacementViewSet(StaffViewSet):
    resource_permission = 'advertising_manage_delivery'
    queryset = Placement.objects.order_by('code')
    serializer_class = PlacementSerializer
    http_method_names = ['get', 'patch', 'head', 'options']


class DeliveryAllocationViewSet(StaffViewSet):
    resource_permission = 'advertising_manage_delivery'
    queryset = DeliveryAllocation.objects.select_related('creative', 'creative__campaign', 'placement').order_by('-pk')
    serializer_class = DeliveryAllocationSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        if not isinstance(request.data, dict):
            raise ValidationError('Provide an allocation object.')
        creative_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('creative'))
        get_object_or_404(Creative.objects.select_for_update(), pk=creative_id)
        return super().create(request, *args, **kwargs)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        allocation = self.get_object()
        Creative.objects.select_for_update().get(pk=allocation.creative_id)
        return super().update(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get('creative'):
            value = serializers.IntegerField(min_value=1).run_validation(self.request.query_params['creative'])
            queryset = queryset.filter(creative_id=value)
        return queryset

    @action(detail=True, methods=['post'])
    def workflow(self, request, pk=None):
        if not ({'adverts', 'advertising_activate'} & set(dashboard_permissions(request.user))):
            raise PermissionDenied('Advertising activation permission is required.')
        if not isinstance(request.data, dict):
            raise ValidationError('Provide an object containing the workflow action.')
        operation = request.data.get('action')
        with transaction.atomic():
            allocation = self.get_queryset().select_for_update(of=('self',)).get(pk=self.get_object().pk)
            if operation == 'activate':
                if allocation.creative.approval != 'approved' or not creative_ready(allocation.creative):
                    raise ValidationError('Approve a ready creative before activation.')
                if not allocation.creative.advertiser.active:
                    raise ValidationError('The client is inactive.')
                campaign = allocation.creative.campaign
                if campaign and (campaign.approval != 'approved' or allocation.placement_id not in campaign.placements):
                    raise ValidationError('Approve the campaign and include this placement before activation.')
                if allocation.ends_at and allocation.ends_at <= timezone.now():
                    raise ValidationError('The allocation has expired.')
                allocation.status = 'active'
            elif operation == 'pause':
                allocation.status = 'paused'
            elif operation == 'complete':
                allocation.status = 'completed'
            else:
                raise ValidationError('This workflow transition is unavailable.')
            allocation.save(update_fields=['status', 'updated_at'])
            self.audit(allocation, operation)
        return Response(self.get_serializer(allocation).data)


class AdvertisingOperationView(PrivateResponseMixin, APIView):
    permission_classes = [HasDashboardSection]
    required_section = ('adverts', 'advertising_view', 'advertising_settings')

    def get(self, request):
        operation = AdvertisingOperation.objects.filter(pk=1).first()
        return Response({'backend_enabled': settings.ADVERTISING_ENABLED,
                         'operational_enabled': operation.enabled if operation else True,
                         'effective_enabled': advertising_available()})

    def patch(self, request):
        if 'advertising_settings' not in dashboard_permissions(request.user):
            raise PermissionDenied('Advertising settings permission is required.')
        if not isinstance(request.data, dict):
            raise ValidationError('Provide an object containing enabled.')
        enabled = request.data.get('enabled')
        if type(enabled) is not bool:
            raise ValidationError({'enabled': 'Provide a boolean.'})
        with transaction.atomic():
            operation, _ = AdvertisingOperation.objects.select_for_update().get_or_create(pk=1)
            operation.enabled = enabled
            operation.save(update_fields=['enabled', 'updated_at'])
            Audit.objects.create(actor=request.user, resource='advertisingoperation', resource_id='1',
                                 action='enabled' if enabled else 'paused')
        return self.get(request)


class ImageUploadView(PrivateResponseMixin, APIView):
    permission_classes = [HasAdvertisingAccess]
    resource_permission = 'advertising_upload_creatives'
    required_section = 'adverts'
    throttle_classes = [AdUserThrottle]

    def post(self, request):
        asset = upload_image(request.FILES.get('file'), request.user)
        Audit.objects.create(actor=request.user, resource='imageasset', resource_id=str(asset.pk), action='uploaded')
        return Response({'id': str(asset.pk), 'desktop_url': asset.desktop_url, 'mobile_url': asset.mobile_url,
                         'width': asset.width, 'height': asset.height}, status=201)


class PublicAdView(PrivateResponseMixin, APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AdAnonThrottle, AdUserThrottle]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method == 'POST' and not isinstance(request.data, dict):
            raise ValidationError('Provide a JSON object.')


class SessionView(PublicAdView):
    def post(self, request):
        if not advertising_available():
            return Response({'enabled': False, 'session': None, 'placements': []})
        session = context_session(request.data, request.user)
        placements = Placement.objects.filter(enabled=True)
        rows = [{'code': p.code, 'breakpoints': p.breakpoints} for p in placements
                if (placement_allowed(session, p, str(p.breakpoints[0]) if p.code == 'midroll' and p.breakpoints else 'once')
                    or session.context == 'homepage' and p.code == 'homepage_featured_sponsor'
                    and placement_allowed(session, p, 'featured-' + str((HomepageConfiguration.objects.filter(pk=1).first() or HomepageConfiguration()).advert_interval)))]
        return Response({'enabled': True, 'session': receipt(session), 'placements': rows})


class DecisionView(PublicAdView):
    def post(self, request):
        if not advertising_available():
            return Response({'ad': None, 'reason': 'disabled'})
        session = authorized_session(request.data.get('session'), request.user)
        code = request.data.get('placement')
        if not isinstance(code, str) or code not in PLACEMENTS:
            raise ValidationError('Select a supported placement.')
        placement = get_object_or_404(Placement, pk=code)
        key = request.data.get('break_key', 'once')
        if not isinstance(key, str) or len(key) > 32 or not placement_allowed(session, placement, key):
            raise ValidationError('Placement or breakpoint does not belong to this context.')
        # Geography is honored only behind explicitly trusted ingress. Never
        # accept a region supplied in the request JSON or an arbitrary header.
        region = ''
        if request.META.get('REMOTE_ADDR') in settings.ADVERTISING_GEO_TRUSTED_PROXIES:
            region = request.META.get('HTTP_X_CBM_COUNTRY', '')
        response = Response()
        response.data = decide(session, placement, key, identifier(request.data.get('request_id')), response, region)
        return response


class EventView(PublicAdView):
    def post(self, request):
        session = authorized_session(request.data.get('session'), request.user)
        return Response(ingest(session, identifier(request.data.get('decision_id')), request.data.get('kind'),
                               request.data.get('position_seconds', 0)))


class ReportView(PrivateResponseMixin, APIView):
    permission_classes = [HasAdvertisingAccess]
    resource_permission = 'advertising_reports'
    read_permission = 'advertising_reports'
    required_section = 'adverts'

    def get(self, request):
        return Response({'enabled': advertising_available(), **report(request.query_params)})


class TargetOptionsView(PrivateResponseMixin, APIView):
    permission_classes = [HasAdvertisingAccess]
    required_section = 'adverts'

    def get(self, request):
        query = serializers.CharField(max_length=100, allow_blank=True).run_validation(request.query_params.get('q', ''))
        return Response({'categories': [{'id': key, 'name': label} for key, label in CATEGORY_CHOICES],
            'genres': list(Genre.objects.filter(is_active=True, name__icontains=query).order_by('name').values('id', 'name')[:100]),
            'channels': list(Channel.objects.filter(is_active=True, name__icontains=query).order_by('name').values('id', 'name')[:100]),
            'contents': [{'id': row.pk, 'name': row.title} for row in Content.objects.filter(title__icontains=query, **public_eligibility()).order_by('title')[:100]]})
