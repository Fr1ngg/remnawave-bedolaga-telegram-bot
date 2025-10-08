"""Пакет административного веб-API."""
from .app import create_web_api_app
from .server import WebAPIServer

__all__ = ["create_web_api_app", "WebAPIServer"]
