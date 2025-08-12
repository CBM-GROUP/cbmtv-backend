from django.contrib import admin
from .models import User, RoleChangeLog

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
