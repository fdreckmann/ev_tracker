"""
Tests for update API routes — including legacy compatibility.
"""


class TestUpdateRoutes:
    def test_update_info_returns_version(self, authed_client):
        rv = authed_client.get("/api/update-info")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "current_version" in data or "version" in data or "latest_version" in data

    def test_update_check_alias_returns_200(self, authed_client):
        """Legacy /api/update/check must return 200, not 404."""
        rv = authed_client.get("/api/update/check")
        assert rv.status_code == 200

    def test_update_log_returns_200(self, authed_client):
        """Legacy /api/update/log must return 200 with empty log list."""
        rv = authed_client.get("/api/update/log")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "log" in data
        assert isinstance(data["log"], list)

    def test_update_pull_returns_410(self, authed_client):
        """Legacy /api/update/pull must return 410 Gone (in-app update removed)."""
        rv = authed_client.post("/api/update/pull")
        assert rv.status_code == 410
        data = rv.get_json()
        assert data.get("ok") is False
        assert "docker" in data.get("error", "").lower() or "entfernt" in data.get("error", "").lower()

    def test_update_pull_not_404(self, authed_client):
        """Legacy route must not return 404 — old installations may call it."""
        rv = authed_client.post("/api/update/pull")
        assert rv.status_code != 404
