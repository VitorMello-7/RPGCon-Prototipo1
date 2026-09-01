from django.urls import path

from . import combat, views

urlpatterns = [
    path("", views.home, name="home"),

    # modo simples (0.2)
    path("<slug:slug>/", views.study, name="study"),
    path("<slug:slug>/revelar/", views.reveal, name="reveal"),
    path("<slug:slug>/responder/", views.answer, name="answer"),

    # expedicao (3.5)
    path("<slug:slug>/mapa/", combat.game_map, name="map"),
    path("<slug:slug>/expedicao/", combat.expedition, name="expedition"),
    path("<slug:slug>/expedicao/lutar/", combat.fight, name="fight"),
    path("<slug:slug>/expedicao/revelar/", combat.reveal, name="combat_reveal"),
    path("<slug:slug>/expedicao/golpe/", combat.strike, name="strike"),
    path("<slug:slug>/expedicao/sair/", combat.abandon, name="abandon"),
    path("<slug:slug>/expedicao/<int:pk>/fim/", combat.summary, name="summary"),
]
