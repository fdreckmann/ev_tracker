# EV Tracker — blueprint registration
# Call register_blueprints(app) from server.py after creating the Flask app.

from routes.health import health_bp
from routes.mqtt import mqtt_bp
from routes.notifications import notifications_bp
from routes.tokens import tokens_bp
from routes.admin import admin_bp
from routes.billing import billing_bp
from routes.sessions import sessions_bp
from routes.users import users_bp
from routes.backup import backup_bp
from routes.tariff import tariff_bp
from routes.signatures import signatures_bp
from routes.export_templates import export_templates_bp
from routes.audit_routes import audit_routes_bp
from routes.smtp import smtp_bp
from routes.connections import connections_bp
from routes.api_v1 import api_v1_bp
from routes.vehicles import vehicles_bp
from routes.reports import reports_bp
from routes.templates_routes import templates_routes_bp
from routes.pdf_export import pdf_export_bp
from routes.export import export_bp
from routes.email_reports import email_reports_bp
from routes.main_routes import main_routes_bp
from routes.auth import auth_bp
from routes.missing_charges import missing_charges_bp


def register_blueprints(app):
    """Register all route blueprints on the Flask app."""
    app.register_blueprint(auth_bp)
    app.register_blueprint(missing_charges_bp)
    app.register_blueprint(main_routes_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(vehicles_bp)
    app.register_blueprint(sessions_bp)
    app.register_blueprint(connections_bp)
    app.register_blueprint(templates_routes_bp)
    app.register_blueprint(signatures_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(export_templates_bp)
    app.register_blueprint(pdf_export_bp)
    app.register_blueprint(backup_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(smtp_bp)
    app.register_blueprint(audit_routes_bp)
    app.register_blueprint(email_reports_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(tariff_bp)
    app.register_blueprint(mqtt_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(tokens_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(api_v1_bp)
