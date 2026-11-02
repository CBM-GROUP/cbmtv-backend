from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from .forms import AccountChangeForm, AccountCreationForm, AccountPasswordChangeForm
from django.db import transaction
from .sessions import revoke_sessions
from .models import User, RoleChangeLog, PasswordResetCode

@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = AccountChangeForm
    add_form = AccountCreationForm
    change_password_form = AccountPasswordChangeForm
    ordering = ('email',)
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Profile', {'fields': ('name', 'phone', 'location', 'country', 'image')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'role', 'dashboard_role', 'groups', 'user_permissions')}),
        ('Account state', {'fields': ('auth_provider', 'google_subject', 'must_change_password', 'last_login')}),
    )
    add_fieldsets = ((None, {'classes': ('wide',), 'fields': ('email', 'name', 'password1', 'password2')}),)
    readonly_fields = ('auth_provider', 'google_subject', 'must_change_password', 'last_login')
    list_display = ('email', 'name', 'phone', 'location', 'country', 'role', 'is_active', 'is_staff')
    search_fields = ('email', 'name')
    list_filter = ('role', 'is_active', 'is_staff')

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        deactivated = change and not obj.is_active and User.objects.filter(pk=obj.pk, is_active=True).exists()
        super().save_model(request, obj, form, change)
        if deactivated:
            revoke_sessions(obj)

@admin.register(RoleChangeLog)
class RoleChangeLogAdmin(admin.ModelAdmin):
    list_display = ('changed_by', 'changed_user', 'old_role', 'new_role', 'timestamp')
    search_fields = ('changed_by__email', 'changed_user__email')
    list_filter = ('old_role', 'new_role', 'timestamp')


@admin.register(PasswordResetCode)
class PasswordResetCodeAdmin(admin.ModelAdmin):
    list_display = ('user', 'created_at', 'expires_at', 'attempts', 'used')
    search_fields = ('user__email',)
    list_filter = ('used',)
    # The hash is useless to an admin and should not be editable.
    exclude = ('code_hash',)
