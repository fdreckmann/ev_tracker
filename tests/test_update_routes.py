"""
Tests for update API routes — including legacy compatibility and version metadata.
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

    def test_update_info_contains_metadata_fields(self, authed_client):
        """/api/update-info must include branch, channel, commit_short, image_tag."""
        rv = authed_client.get("/api/update-info")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "channel" in data
        assert "branch" in data
        assert "commit" in data
        assert "commit_short" in data
        assert "image_tag" in data

    def test_update_info_contains_remote_url(self, authed_client):
        """/api/update-info must expose the remote URL being checked."""
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        assert "remote_url" in data


class TestSystemStatus:
    def test_system_status_has_version_fields(self, authed_client):
        """/api/system/status must include app_version, channel, branch."""
        rv = authed_client.get("/api/system/status")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data.get("ok") is True
        assert "app_version" in data or "version" in data
        assert "channel" in data
        assert "branch" in data

    def test_system_status_has_compat_aliases(self, authed_client):
        """/api/system/status must have mobile-compat aliases: db_size_mb, session_count."""
        rv = authed_client.get("/api/system/status")
        data = rv.get_json()
        assert "db_size_mb" in data
        assert "session_count" in data

    def test_system_status_db_size_mb_is_float(self, authed_client):
        rv = authed_client.get("/api/system/status")
        data = rv.get_json()
        assert isinstance(data["db_size_mb"], (int, float))

    def test_system_status_preserves_original_fields(self, authed_client):
        """Original fields (db_size, sessions_count) must still be present."""
        rv = authed_client.get("/api/system/status")
        data = rv.get_json()
        assert "db_size" in data
        assert "sessions_count" in data

    def test_update_info_no_empty_version_fields(self, authed_client):
        """/api/update-info must not return empty strings for branch/commit/image_tag."""
        rv = authed_client.get("/api/update-info")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data.get("branch", "") != "", "branch must not be empty"
        assert data.get("commit", "") != "", "commit must not be empty"
        assert data.get("image_tag", "") != "", "image_tag must not be empty"

    def test_update_info_fallback_values(self, authed_client):
        """/api/update-info must show fallback strings when env vars are absent."""
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        branch = data.get("branch", "")
        commit = data.get("commit", "")
        image_tag = data.get("image_tag", "")
        # Must be either a real value or one of the known fallbacks
        assert branch in ("local/source",) or len(branch) > 0
        assert commit in ("unknown",) or len(commit) > 0
        assert image_tag in ("unknown",) or len(image_tag) > 0
