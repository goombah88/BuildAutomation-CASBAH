from django.contrib import admin

from .models import BuildRequest


@admin.register(BuildRequest)
class BuildRequestAdmin(admin.ModelAdmin):
    list_display = ("vm_name", "site", "tenant", "status", "requested_by", "created_at")
    list_filter = ("site", "tenant", "status")
    search_fields = ("vm_name", "ipv4_address", "requested_by")
    readonly_fields = ("created_at", "updated_at")
