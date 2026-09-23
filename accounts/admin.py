from django.contrib import admin
from .models import User, RoleChangeLog, PasswordResetCode

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('email', 'name', 'phone', 'location', 'country', 'role', 'is_active', 'is_staff')
    search_fields = ('email', 'name')
    list_filter = ('role', 'is_active', 'is_staff')

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
