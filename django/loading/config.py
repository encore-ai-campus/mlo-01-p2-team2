"""Backward-compatible imports for the app-owned Bronze configuration."""

from second_project.service.bronze_config import LoaderConfig, default_config, project_root

__all__ = ["LoaderConfig", "default_config", "project_root"]
