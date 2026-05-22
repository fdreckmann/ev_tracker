"""
Email service — extracted from server.py.
Provides email sending and HTML helpers.
"""
from __future__ import annotations


def _email_html(title: str, *paragraphs: str) -> str:
    paras = "".join(f"<p style='margin:0 0 14px;line-height:1.6'>{p}</p>" for p in paragraphs)
    return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'></head>
<body style='margin:0;padding:0;background:#0f0f0f;font-family:system-ui,sans-serif'>
  <table width='100%' cellpadding='0' cellspacing='0'>
    <tr><td align='center' style='padding:40px 20px'>
      <table width='560' cellpadding='0' cellspacing='0' style='background:#1a1a1a;border-radius:12px;overflow:hidden;border:1px solid #2a2a2a'>
        <tr><td style='background:#1e3a5f;padding:24px 32px'>
          <div style='font-size:1.1rem;font-weight:700;color:#7eb8f7;letter-spacing:.05em'>⚡ EV Tracker</div>
        </td></tr>
        <tr><td style='padding:32px'>
          <h2 style='margin:0 0 20px;color:#e0e0e0;font-size:1.1rem;font-weight:600'>{title}</h2>
          <div style='color:#b0b0b0;font-size:.875rem'>{paras}</div>
        </td></tr>
        <tr><td style='padding:16px 32px;border-top:1px solid #2a2a2a'>
          <div style='color:#555;font-size:.75rem'>Diese E-Mail wurde automatisch von EV Tracker generiert.</div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def _email_btn(url: str, label: str) -> str:
    return f"<a href='{url}' style='display:inline-block;background:#1e6fb5;color:#fff;text-decoration:none;padding:12px 28px;border-radius:8px;font-size:.875rem;font-weight:600;margin:8px 0'>{label}</a>"


def _send_email(to_addr: str, subject: str, body_html: str, body_text: str = None) -> tuple:
    from server import _send_email as _srv_send_email
    return _srv_send_email(to_addr, subject, body_html, body_text)


def _send_email_with_attachments(to_addr, subject, body_html, attachments=None):
    from server import _send_email_with_attachments as _srv_send
    return _srv_send(to_addr, subject, body_html, attachments)
