from django.contrib import admin

from .models import Content


@admin.register(Content)
class ContentAdmin(admin.ModelAdmin):
    # is_featured / is_trending are editable straight from the list so an admin
    # can curate the mobile hero and trending rail without opening each record.
    list_display = ('title', 'content_type', 'channel', 'status', 'is_featured', 'is_trending', 'created_at')
    list_editable = ('is_featured', 'is_trending')
    list_filter = ('content_type', 'status', 'is_featured', 'is_trending', 'channel')
    search_fields = ('title', 'genre')
