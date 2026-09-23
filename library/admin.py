from django.contrib import admin

from .models import SavedItem, WatchProgress


@admin.register(SavedItem)
class SavedItemAdmin(admin.ModelAdmin):
    list_display = ('user', 'content', 'created_at')
    search_fields = ('user__email', 'content__title')
    raw_id_fields = ('user', 'content')


@admin.register(WatchProgress)
class WatchProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'content', 'episode', 'miniseries', 'position_seconds', 'duration_seconds', 'completed', 'updated_at')
    search_fields = ('user__email', 'content__title')
    list_filter = ('completed',)
    raw_id_fields = ('user', 'content', 'episode', 'miniseries')
