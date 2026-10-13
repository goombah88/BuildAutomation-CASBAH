from django.urls import path

from . import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path(
        "build/rcd09/staging/",
        views.build_form,
        {"site": "rcd09", "staging": True},
        name="build_form_staging",
    ),
    path("build/<str:site>/preview/", views.build_preview, name="build_preview"),
    path("build/<str:site>/", views.build_form, name="build_form"),
    path(
        "build/rcd09/staging/options/",
        views.build_form_options,
        {"site": "rcd09", "staging": True},
        name="build_form_staging_options",
    ),
    path(
        "build/<str:site>/options/",
        views.build_form_options,
        name="build_form_options",
    ),
    path(
        "netbox/prefetch/",
        views.netbox_options_prefetch,
        name="netbox_options_prefetch",
    ),
    path(
        "build/<str:site>/validate/vm-name/",
        views.validate_vm_name,
        name="validate_vm_name",
    ),
    path("build/<str:site>/validate/ip/", views.validate_ip, name="validate_ip"),
    path("build/submitted/<int:pk>/", views.build_submitted, name="build_submitted"),
    path(
        "build/status/<int:pk>/pipeline/",
        views.build_pipeline_status,
        name="build_pipeline_status",
    ),
    path("builds/", views.build_list, name="build_list"),
    path("builds/export.csv", views.build_list_csv, name="build_list_csv"),
]
