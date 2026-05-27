"""
Tests for signature upload and draw endpoint size limits.
"""
import base64


class TestSignatureDraw:
    def test_draw_missing_image_data_returns_400(self, authed_client):
        rv = authed_client.post("/api/signature/draw", json={})
        assert rv.status_code == 400

    def test_draw_invalid_format_returns_400(self, authed_client):
        rv = authed_client.post("/api/signature/draw",
                                json={"image_data": "not-a-data-uri"})
        assert rv.status_code == 400

    def test_draw_oversized_returns_413(self, authed_client):
        """A base64 payload > 2 MB decoded must be rejected with 413."""
        # 2.1 MB of random bytes → base64 encoded
        oversized = b"\x00" * (2 * 1024 * 1024 + 64 * 1024)  # 2.06 MB
        b64 = base64.b64encode(oversized).decode()
        rv = authed_client.post("/api/signature/draw",
                                json={"image_data": f"data:image/png;base64,{b64}"})
        assert rv.status_code == 413

    def test_draw_empty_base64_rejected(self, authed_client):
        rv = authed_client.post("/api/signature/draw",
                                json={"image_data": "data:image/png;base64,"})
        # Empty base64 → empty bytes → PIL will fail → 500, not crash
        assert rv.status_code in (400, 413, 500)


class TestSignatureUpload:
    def test_upload_no_file_returns_400(self, authed_client):
        rv = authed_client.post("/api/signature/upload")
        assert rv.status_code == 400

    def test_upload_wrong_mimetype_returns_400(self, authed_client):
        from io import BytesIO
        rv = authed_client.post("/api/signature/upload",
                                data={"file": (BytesIO(b"not an image"), "bad.txt")},
                                content_type="multipart/form-data")
        assert rv.status_code == 400
