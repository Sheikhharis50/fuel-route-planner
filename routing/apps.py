from django.apps import AppConfig


class RoutingConfig(AppConfig):
    name = 'routing'

    def ready(self):
        from .services.geocoding import get_gazetteer
        from .services.stations import get_station_index

        get_gazetteer()
        get_station_index()
