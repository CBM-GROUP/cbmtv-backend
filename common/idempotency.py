"""Optional durable creation identity for JSON management endpoints."""
import hashlib
import json
from uuid import UUID

from django.db import transaction
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response

from .models import CreationOperation


class OperationConflict(APIException):
    status_code = 409
    default_detail = 'This creation key was already used for a different request.'


class OperationGone(APIException):
    status_code = 410
    default_detail = 'The original record was deleted. This creation cannot be replayed.'


class IdempotentCreateMixin:
    def options(self, request, *args, **kwargs):
        response = super().options(request, *args, **kwargs)
        response['Idempotency-Supported'] = 'v1'
        return response

    def create(self, request, *args, **kwargs):
        raw_key = request.headers.get('Idempotency-Key')
        if raw_key is None:
            return super().create(request, *args, **kwargs)
        try:
            key = UUID(raw_key)
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({'Idempotency-Key': 'A UUID is required.'})
        if not isinstance(request.data, dict):
            raise ValidationError('A JSON object is required for keyed creation.')
        digest = hashlib.sha256(json.dumps(request.data, sort_keys=True, separators=(',', ':'),
                                           ensure_ascii=True, allow_nan=False).encode()).hexdigest()
        endpoint = request.path
        with transaction.atomic():
            operation, _ = CreationOperation.objects.get_or_create(
                actor=request.user, key=key,
                defaults={'endpoint': endpoint, 'payload_digest': digest},
            )
            operation = CreationOperation.objects.select_for_update().get(pk=operation.pk)
            if operation.endpoint != endpoint or operation.payload_digest != digest:
                raise OperationConflict()
            replayed = operation.record_id is not None
            if replayed:
                model = self.get_serializer_class().Meta.model
                instance = model._default_manager.filter(pk=operation.record_id).first()
                if instance is None:
                    raise OperationGone()
                self.check_object_permissions(request, instance)
            else:
                serializer = self.get_serializer(data=request.data)
                serializer.is_valid(raise_exception=True)
                self.perform_create(serializer)
                instance = serializer.instance
                operation.record_id = instance.pk
                operation.save(update_fields=['record_id'])
        # A serialization/delivery/post-commit callback failure must not erase
        # the association. Retrying then resolves to this same instance.
        data = self.get_serializer(instance).data
        return Response(data, status=201, headers={
            'Idempotency-Key': str(key), 'Idempotency-Replayed': str(replayed).lower(),
            **self.get_success_headers(data),
        })
