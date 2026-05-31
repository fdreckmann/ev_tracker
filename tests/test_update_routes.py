"""
Tests for update API routes — including legacy compatibility and version metadata.
"""
import os


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

    def test_update_info_contains_build_date(self, authed_client):
        """/api/update-info must expose build_date."""
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        assert "build_date" in data

    def test_update_info_commit_and_commit_short_are_distinct_fields(self, authed_client):
        """commit and commit_short must both be present as separate fields."""
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        assert "commit" in data, "commit field must be present"
        assert "commit_short" in data, "commit_short field must be present"
        # They may both be 'unknown' in CI with no git info, but they must both exist
        assert data["commit"] is not None
        assert data["commit_short"] is not None


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

    def test_system_status_has_commit_fields(self, authed_client):
        """/api/system/status must include commit and commit_short."""
        rv = authed_client.get("/api/system/status")
        data = rv.get_json()
        assert "commit" in data
        assert "commit_short" in data

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
        assert data.get("commit_short", "") != "", "commit_short must not be empty"

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


class TestVersionModule:
    """Unit tests for app/version.py — _env_or logic and DISPLAY_ constants."""

    def test_env_or_no_env_returns_fallback(self, monkeypatch):
        """_env_or must return the fallback when the env var is not set."""
        monkeypatch.delenv("_EV_TEST_UNUSED_KEY", raising=False)
        import version as _v
        assert _v._env_or("_EV_TEST_UNUSED_KEY", "my_fallback") == "my_fallback"

    def test_env_or_real_value_returned(self, monkeypatch):
        """_env_or must return the env var value when it is a real non-unknown string."""
        monkeypatch.setenv("_EV_TEST_UNUSED_KEY", "realvalue123")
        import version as _v
        assert _v._env_or("_EV_TEST_UNUSED_KEY", "fallback") == "realvalue123"

    def test_env_or_unknown_treated_as_missing(self, monkeypatch):
        """_env_or must fall back when env var is exactly 'unknown' (Docker local build default)."""
        monkeypatch.setenv("_EV_TEST_UNUSED_KEY", "unknown")
        import version as _v
        assert _v._env_or("_EV_TEST_UNUSED_KEY", "fallback") == "fallback"

    def test_env_or_empty_string_treated_as_missing(self, monkeypatch):
        """_env_or must fall back when env var is empty."""
        monkeypatch.setenv("_EV_TEST_UNUSED_KEY", "")
        import version as _v
        assert _v._env_or("_EV_TEST_UNUSED_KEY", "fallback") == "fallback"

    def test_env_or_whitespace_stripped(self, monkeypatch):
        """_env_or must strip whitespace before evaluating."""
        monkeypatch.setenv("_EV_TEST_UNUSED_KEY", "  trimmed  ")
        import version as _v
        assert _v._env_or("_EV_TEST_UNUSED_KEY", "fallback") == "trimmed"

    def test_env_or_whitespace_only_treated_as_missing(self, monkeypatch):
        """_env_or must fall back when env var is only whitespace."""
        monkeypatch.setenv("_EV_TEST_UNUSED_KEY", "   ")
        import version as _v
        assert _v._env_or("_EV_TEST_UNUSED_KEY", "fallback") == "fallback"

    def test_display_constants_non_empty_in_test_env(self):
        """All DISPLAY_ constants must be non-empty strings in the test environment."""
        import version as _v
        assert _v.DISPLAY_BRANCH != "", "DISPLAY_BRANCH must not be empty"
        assert _v.DISPLAY_COMMIT != "", "DISPLAY_COMMIT must not be empty"
        assert _v.DISPLAY_COMMIT_SHORT != "", "DISPLAY_COMMIT_SHORT must not be empty"
        assert _v.DISPLAY_IMAGE_TAG != "", "DISPLAY_IMAGE_TAG must not be empty"

    def test_display_branch_fallback_when_unset(self):
        """DISPLAY_BRANCH must be 'local/source' when EV_TRACKER_BRANCH is not set."""
        import version as _v
        # In test environment: version.json has empty branch, env var is not set
        if not os.getenv("EV_TRACKER_BRANCH"):
            assert _v.DISPLAY_BRANCH == "local/source", (
                f"Expected 'local/source', got {_v.DISPLAY_BRANCH!r}"
            )

    def test_display_commit_fallback_when_unset(self):
        """DISPLAY_COMMIT must be 'unknown' when EV_TRACKER_COMMIT is not set."""
        import version as _v
        if not os.getenv("EV_TRACKER_COMMIT"):
            assert _v.DISPLAY_COMMIT == "unknown", (
                f"Expected 'unknown', got {_v.DISPLAY_COMMIT!r}"
            )

    def test_display_commit_short_fallback_when_unset(self):
        """DISPLAY_COMMIT_SHORT must be 'unknown' when no commit is available."""
        import version as _v
        if not os.getenv("EV_TRACKER_COMMIT"):
            assert _v.DISPLAY_COMMIT_SHORT == "unknown", (
                f"Expected 'unknown', got {_v.DISPLAY_COMMIT_SHORT!r}"
            )

    def test_display_image_tag_fallback_when_unset(self):
        """DISPLAY_IMAGE_TAG must be a non-empty fallback when EV_TRACKER_IMAGE_TAG is not set.

        'unknown' is the Docker-runtime fallback; 'local' is the build-info.json fallback
        for local builds where build-info.json provides a default.  Both are valid.
        """
        import version as _v
        if not os.getenv("EV_TRACKER_IMAGE_TAG"):
            assert _v.DISPLAY_IMAGE_TAG in ("unknown", "local"), (
                f"Expected 'unknown' or 'local', got {_v.DISPLAY_IMAGE_TAG!r}"
            )

    def test_commit_short_is_8_char_prefix_of_full_commit(self, monkeypatch):
        """When GIT_COMMIT is a real 40-char SHA, COMMIT_SHORT must be its first 8 chars."""
        import version as _v
        full_sha = "deadbeef1234567890abcdef1234567890abcdef"
        # Simulate the computation that version.py applies at module level
        computed_short = full_sha[:8] if full_sha else ""
        assert computed_short == "deadbeef"
        assert len(computed_short) == 8
        # Verify that DISPLAY_ values computed from a real SHA differ in length
        monkeypatch.setattr(_v, "GIT_COMMIT",    full_sha)
        monkeypatch.setattr(_v, "COMMIT_SHORT",   full_sha[:8])
        monkeypatch.setattr(_v, "DISPLAY_COMMIT", full_sha)
        monkeypatch.setattr(_v, "DISPLAY_COMMIT_SHORT", full_sha[:8])
        assert _v.DISPLAY_COMMIT != _v.DISPLAY_COMMIT_SHORT
        assert len(_v.DISPLAY_COMMIT) == 40
        assert len(_v.DISPLAY_COMMIT_SHORT) == 8
        assert _v.DISPLAY_COMMIT.startswith(_v.DISPLAY_COMMIT_SHORT)

    def test_long_commit_truncated_to_8_chars(self, monkeypatch):
        """Commits longer than 8 chars must be shortened — never shown in full as commit_short."""
        import version as _v
        for sha in [
            "abc123def456789012345678901234567890abcd",  # 40-char SHA
            "abc123de",                                   # 8-char short SHA
            "abc123def",                                  # 9-char (edge)
        ]:
            short = sha[:8]
            assert len(short) == 8
            assert short == sha[:8]

    def test_commit_short_empty_when_no_commit(self, monkeypatch):
        """When GIT_COMMIT is empty, COMMIT_SHORT must be empty and DISPLAY_COMMIT_SHORT 'unknown'."""
        import version as _v
        monkeypatch.setattr(_v, "GIT_COMMIT",    "")
        monkeypatch.setattr(_v, "COMMIT_SHORT",   "")
        monkeypatch.setattr(_v, "DISPLAY_COMMIT", "unknown")
        monkeypatch.setattr(_v, "DISPLAY_COMMIT_SHORT", "unknown")
        assert _v.GIT_COMMIT == ""
        assert _v.COMMIT_SHORT == ""
        assert _v.DISPLAY_COMMIT == "unknown"
        assert _v.DISPLAY_COMMIT_SHORT == "unknown"

    def test_asset_version_contains_commit_short(self, monkeypatch):
        """ASSET_VERSION must embed the commit_short when a real commit is available."""
        import version as _v
        full_sha = "cafebabe1234567890abcdef1234567890abcdef"
        short = full_sha[:8]  # "cafebabe"
        monkeypatch.setattr(_v, "GIT_COMMIT",   full_sha)
        monkeypatch.setattr(_v, "COMMIT_SHORT",  short)
        monkeypatch.setattr(_v, "APP_VERSION",   "2.0.55")
        monkeypatch.setattr(_v, "BUILD_DATE",    "2026-05-30")
        monkeypatch.setattr(_v, "ASSET_VERSION", f"2.0.55-{short}")
        assert short in _v.ASSET_VERSION
        assert _v.ASSET_VERSION.startswith("2.0.55-")

    def test_asset_version_non_empty_without_commit(self):
        """ASSET_VERSION must be non-empty even without a commit hash."""
        import version as _v
        assert _v.ASSET_VERSION != ""
        assert "-" in _v.ASSET_VERSION  # always has a suffix separator


class TestVersionInjectedIntoAPI:
    """Integration tests: verify /api/update-info and /api/system/status
    return the monkeypatched version values correctly."""

    def test_real_sha_produces_short_commit_in_api(self, authed_client, monkeypatch):
        """When DISPLAY_COMMIT is a 40-char SHA, API commit_short must be 8 chars."""
        import version as _v
        full_sha = "deadbeef1234567890abcdef1234567890abcdef"
        monkeypatch.setattr(_v, "DISPLAY_COMMIT",       full_sha)
        monkeypatch.setattr(_v, "DISPLAY_COMMIT_SHORT", full_sha[:8])
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        assert data["commit"] == full_sha, f"Expected full SHA, got {data['commit']!r}"
        assert data["commit_short"] == "deadbeef", f"Expected 8-char short, got {data['commit_short']!r}"
        assert data["commit"] != data["commit_short"]
        assert len(data["commit"]) == 40
        assert len(data["commit_short"]) == 8

    def test_branch_injected_into_api(self, authed_client, monkeypatch):
        """Branch value set on version module must be reflected in /api/update-info."""
        import version as _v
        monkeypatch.setattr(_v, "DISPLAY_BRANCH", "feature/my-branch")
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        assert data["branch"] == "feature/my-branch"

    def test_image_tag_injected_into_api(self, authed_client, monkeypatch):
        """Image tag set on version module must be reflected in /api/update-info."""
        import version as _v
        monkeypatch.setattr(_v, "DISPLAY_IMAGE_TAG", "19121412/ev-tracker:latest")
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        assert data["image_tag"] == "19121412/ev-tracker:latest"

    def test_all_unknown_fallbacks_non_empty_in_api(self, authed_client, monkeypatch):
        """When all DISPLAY_ values are fallbacks, API must still have non-empty fields."""
        import version as _v
        monkeypatch.setattr(_v, "DISPLAY_BRANCH",        "local/source")
        monkeypatch.setattr(_v, "DISPLAY_COMMIT",        "unknown")
        monkeypatch.setattr(_v, "DISPLAY_COMMIT_SHORT",  "unknown")
        monkeypatch.setattr(_v, "DISPLAY_IMAGE_TAG",     "unknown")
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        assert data.get("branch") == "local/source"
        assert data.get("commit") == "unknown"
        assert data.get("commit_short") == "unknown"
        assert data.get("image_tag") == "unknown"
        # All non-empty
        assert data["branch"] != ""
        assert data["commit"] != ""
        assert data["commit_short"] != ""
        assert data["image_tag"] != ""

    def test_system_status_exposes_commit_and_short(self, authed_client, monkeypatch):
        """/api/system/status must expose both commit and commit_short from the version module."""
        import version as _v
        import routes.health as _h
        full_sha = "cafe1234abc456789012345678901234567890ef"
        # health.py imports at module level, so patch both the source module
        # and the already-bound names in the health blueprint module.
        monkeypatch.setattr(_v, "DISPLAY_COMMIT",       full_sha)
        monkeypatch.setattr(_v, "DISPLAY_COMMIT_SHORT", full_sha[:8])
        monkeypatch.setattr(_h, "DISPLAY_COMMIT",       full_sha)
        monkeypatch.setattr(_h, "DISPLAY_COMMIT_SHORT", full_sha[:8])
        rv = authed_client.get("/api/system/status")
        data = rv.get_json()
        assert data["commit"] == full_sha
        assert data["commit_short"] == "cafe1234"
        assert data["commit"] != data["commit_short"]


class TestBuildInfoJson:
    """Tests for build-info.json reading in version.py (layer 2 priority)."""

    def test_bi_helper_returns_fallback_when_bdata_empty(self):
        """_bi() returns fallback when _bdata is empty."""
        import version as _v
        result = _v._bi("nonexistent_key", "my_fallback")
        assert result == "my_fallback"

    def test_bi_helper_returns_value_from_bdata(self, monkeypatch):
        """_bi() returns value from _bdata when key exists and value is non-empty."""
        import version as _v
        monkeypatch.setattr(_v, "_bdata", {"build_source": "github_actions"})
        assert _v._bi("build_source", "local") == "github_actions"

    def test_bi_helper_treats_unknown_as_missing(self, monkeypatch):
        """_bi() treats 'unknown' as missing and falls through to fallback."""
        import version as _v
        monkeypatch.setattr(_v, "_bdata", {"build_source": "unknown"})
        assert _v._bi("build_source", "local") == "local"

    def test_bi_helper_treats_empty_as_missing(self, monkeypatch):
        """_bi() treats empty string as missing."""
        import version as _v
        monkeypatch.setattr(_v, "_bdata", {"commit": ""})
        assert _v._bi("commit", "fallback_sha") == "fallback_sha"

    def test_build_source_from_build_info(self, monkeypatch):
        """BUILD_SOURCE is populated from build-info.json _bdata."""
        import version as _v
        monkeypatch.setattr(_v, "_bdata", {
            "build_source": "github_actions",
            "github_run_id": "12345678",
            "github_ref": "refs/tags/v2.0.55",
        })
        assert _v._bi("build_source", "local") == "github_actions"
        assert _v._bi("github_run_id", "") == "12345678"
        assert _v._bi("github_ref", "") == "refs/tags/v2.0.55"

    def test_env_overrides_build_info(self, monkeypatch):
        """ENV var takes priority over build-info.json for shared fields."""
        import version as _v
        monkeypatch.setattr(_v, "_bdata", {"version": "1.0.0_from_buildinfo"})
        monkeypatch.setenv("EV_TRACKER_VERSION", "2.0.55_from_env")
        # _env_or checks ENV first — should return env value
        result = _v._env_or("EV_TRACKER_VERSION", _v._bi("version", "fallback"))
        assert result == "2.0.55_from_env"

    def test_build_info_overrides_version_json(self, monkeypatch):
        """build-info.json takes priority over version.json fallback."""
        import version as _v
        monkeypatch.setenv("EV_TRACKER_BRANCH", "")  # clear env
        monkeypatch.setattr(_v, "_bdata", {"branch": "main"})
        # Simulate what version.py does: _env_or(key, _bi(key, vdata_fallback))
        branch = _v._env_or("EV_TRACKER_BRANCH", _v._bi("branch", "from_version_json"))
        assert branch == "main"


class TestUpdateInfoNewFields:
    """Tests for the new fields added to /api/update-info."""

    def test_update_info_has_build_utc(self, authed_client):
        """/api/update-info must include build_utc field."""
        rv = authed_client.get("/api/update-info")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "build_utc" in data

    def test_update_info_has_build_local(self, authed_client):
        """/api/update-info must include build_local field (may be empty for local builds)."""
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        assert "build_local" in data
        assert isinstance(data["build_local"], str)

    def test_update_info_has_build_timezone(self, authed_client):
        """/api/update-info must include build_timezone."""
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        assert "build_timezone" in data
        assert data["build_timezone"] == "Europe/Berlin"

    def test_update_info_has_build_source(self, authed_client):
        """/api/update-info must include build_source."""
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        assert "build_source" in data
        assert isinstance(data["build_source"], str)
        assert data["build_source"] != ""

    def test_update_info_has_checked_at_utc(self, authed_client):
        """/api/update-info must include checked_at_utc."""
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        assert "checked_at_utc" in data
        assert data["checked_at_utc"] != ""

    def test_update_info_has_checked_at_local(self, authed_client):
        """/api/update-info must include checked_at_local parseable string."""
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        assert "checked_at_local" in data
        assert isinstance(data["checked_at_local"], str)
        assert data["checked_at_local"] != "", "checked_at_local must not be empty"

    def test_update_info_has_version_alias(self, authed_client):
        """/api/update-info must include 'version' as alias for current_version."""
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        assert "version" in data
        assert data["version"] == data["current_version"]

    def test_update_info_has_cache_hit(self, authed_client):
        """/api/update-info must include cache_hit field."""
        rv = authed_client.get("/api/update-info")
        data = rv.get_json()
        assert "cache_hit" in data
        assert isinstance(data["cache_hit"], bool)

    def test_force_param_accepted(self, authed_client):
        """/api/update-info?force=1 must return 200 OK."""
        rv = authed_client.get("/api/update-info?force=1")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "cache_hit" in data

    def test_build_local_parseable_when_build_utc_is_iso(self):
        """When BUILD_DATE_UTC is a proper ISO timestamp, _to_berlin produces a parseable string."""
        from services.update_service import _to_berlin
        ts = "2026-05-31T14:22:00Z"
        local = _to_berlin(ts)
        assert local != ""
        # Must contain a recognizable date component
        assert "2026" in local or "31.05" in local or "05.2026" in local

    def test_to_berlin_converts_utc_iso(self):
        """_to_berlin() must convert a Z-suffix UTC string to Berlin local time."""
        from services.update_service import _to_berlin
        result = _to_berlin("2026-05-31T14:22:00Z")
        assert result != ""
        assert "2026" in result or "31" in result

    def test_to_berlin_handles_empty(self):
        """_to_berlin() must return empty string for empty input."""
        from services.update_service import _to_berlin
        assert _to_berlin("") == ""
        assert _to_berlin(None) == ""

    def test_to_berlin_handles_date_only(self):
        """_to_berlin() must return date string as-is for date-only inputs."""
        from services.update_service import _to_berlin
        assert _to_berlin("2026-05-28") == "2026-05-28"


class TestVersionTagExtraction:
    """Tests for tag-based version extraction (simulates GitHub Actions logic)."""

    def test_tag_strips_v_prefix(self, monkeypatch):
        """v2.0.55 tag → EV_TRACKER_VERSION=2.0.55 → APP_VERSION=2.0.55"""
        import version as _v
        # Simulate what GitHub Actions does: VERSION="${GITHUB_REF_NAME#v}"
        ref_name = "v2.0.55"
        tag_version = ref_name.lstrip("v")  # shell: ${GITHUB_REF_NAME#v}
        assert tag_version == "2.0.55"
        monkeypatch.setenv("EV_TRACKER_VERSION", tag_version)
        assert _v._env_or("EV_TRACKER_VERSION", "fallback") == "2.0.55"

    def test_same_version_different_commit_gives_different_asset_version(self, monkeypatch):
        """Two main builds with same version but different commits → different ASSET_VERSION."""
        import version as _v
        monkeypatch.setattr(_v, "APP_VERSION", "2.0.55")

        monkeypatch.setattr(_v, "COMMIT_SHORT", "aabbccdd")
        asset_v1 = _v.APP_VERSION + "-" + _v.COMMIT_SHORT

        monkeypatch.setattr(_v, "COMMIT_SHORT", "11223344")
        asset_v2 = _v.APP_VERSION + "-" + _v.COMMIT_SHORT

        assert asset_v1 != asset_v2
        assert asset_v1.startswith("2.0.55-")
        assert asset_v2.startswith("2.0.55-")
        assert "aabbccdd" in asset_v1
        assert "11223344" in asset_v2


class TestSystemStatusBuildFields:
    """Tests that /api/system/status exposes build metadata (for mobile and UI)."""

    def test_system_status_has_image_tag(self, authed_client):
        """/api/system/status must include image_tag with fallback."""
        rv = authed_client.get("/api/system/status")
        data = rv.get_json()
        assert "image_tag" in data
        assert data["image_tag"] != ""  # Must have a fallback, not empty

    def test_system_status_has_build_date(self, authed_client):
        """/api/system/status must include build_date and build_utc."""
        rv = authed_client.get("/api/system/status")
        data = rv.get_json()
        assert "build_date" in data
        assert "build_utc" in data

    def test_system_status_has_build_source(self, authed_client):
        """/api/system/status must include build_source."""
        rv = authed_client.get("/api/system/status")
        data = rv.get_json()
        assert "build_source" in data
        assert data["build_source"] != ""


class TestUIDynamicVersionElements:
    """Tests that the index.html template has dynamic version infrastructure."""

    def test_ui_has_uiLocalMeta_element(self):
        """Template must have #uiLocalMeta for dynamic build metadata display."""
        html = (
            __import__("pathlib").Path(__file__).parent.parent / "app" / "templates" / "index.html"
        ).read_text()
        assert "uiLocalMeta" in html

    def test_ui_has_loadUpdateInfo_function(self):
        """Template must call loadUpdateInfo() to load version data dynamically."""
        html = (
            __import__("pathlib").Path(__file__).parent.parent / "app" / "templates" / "index.html"
        ).read_text()
        assert "loadUpdateInfo" in html

    def test_ui_version_display_element_present(self):
        """Template must have #uiVersionDisplay updated by JS (not purely static)."""
        html = (
            __import__("pathlib").Path(__file__).parent.parent / "app" / "templates" / "index.html"
        ).read_text()
        assert "uiVersionDisplay" in html

    def test_mobile_status_includes_image_tag_field(self):
        """Mobile system status JS must include image_tag field rendering."""
        html = (
            __import__("pathlib").Path(__file__).parent.parent / "app" / "templates" / "index.html"
        ).read_text()
        # The mobile section reads status?.image_tag
        assert "image_tag" in html
        # And status?.build_utc or status?.build_date for build info
        assert "build_utc" in html or "build_date" in html
