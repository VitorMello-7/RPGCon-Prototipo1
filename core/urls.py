from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("<slug:slug>/", views.study, name="study"),
    path("<slug:slug>/revelar/", views.reveal, name="reveal"),
    path("<slug:slug>/responder/", views.answer, name="answer"),
]
