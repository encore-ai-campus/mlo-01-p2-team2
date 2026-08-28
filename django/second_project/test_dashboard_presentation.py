from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse


class DashboardPresentationTests(SimpleTestCase):
    @patch("second_project.presentation.views.DashboardRepository")
    def test_all_dashboard_layers_render_with_namespaced_navigation(
        self,
        repository_class,
    ) -> None:
        repository_class.return_value.snapshot.return_value = {}

        for route_name in (
            "second_project:dashboard",
            "second_project:bronze-dashboard",
            "second_project:silver-dashboard",
        ):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, reverse("second_project:dashboard"))
                self.assertContains(response, reverse("second_project:bronze-dashboard"))
                self.assertContains(response, reverse("second_project:silver-dashboard"))
                self.assertContains(response, reverse("second_project:dashboard-api"))
