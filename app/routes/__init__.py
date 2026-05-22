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
from routes.update import update_bp
from routes.tariff import tariff_bp


def register_blueprints(app):
    """Register all route blueprints on the Flask app."""
    app.register_blueprint(health_bp)
    app.register_blueprint(mqtt_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(tokens_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(sessions_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(backup_bp)
    app.register_blueprint(update_bp)
    app.register_blueprint(tariff_bp)
