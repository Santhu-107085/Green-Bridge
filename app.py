import os
import logging

from flask import Flask, session, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_babel import Babel
from flask_login import LoginManager
from werkzeug.middleware.proxy_fix import ProxyFix

# We assume you have a DeclarativeBase in models.py, so SQLAlchemy is configured to use it.
from sqlalchemy.orm import DeclarativeBase

# Import our translation helper (string‐replace on rendered HTML)
from translations import translate_html

# ----------------------------
# Configure logging
# ----------------------------
logging.basicConfig(level=logging.DEBUG)

# ----------------------------
# SQLAlchemy Base & Objects
# ----------------------------
class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

# ----------------------------
# Babel & LoginManager
# ----------------------------
babel = Babel()
login_manager = LoginManager()


# ----------------------------
# Application Factory
# ----------------------------
def create_app():
    """Application factory: configure Flask + extensions + blueprints."""
    app = Flask(__name__)

    # ----------------------------
    # Core Configuration
    # ----------------------------
    app.secret_key = os.environ.get(
        "SESSION_SECRET",
        "dev-secret-key-change-in-production"
    )
    # If your app is behind a proxy/load‐balancer, fix headers
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    # ----------------------------
    # Database Configuration
    # ----------------------------
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL",
        "sqlite:///greenbridge.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_recycle": 300,
        "pool_pre_ping": True,
    }

    # Initialize SQLAlchemy
    db.init_app(app)

    # ----------------------------
    # Babel (i18n) Configuration
    # ----------------------------
    # We keep a dict of supported languages so both Babel and the after_request hook can reference it
    # The keys are the language codes, the values are human-readable names:
    #   'en' → 'English', 'hi' → 'हिंदी', 'te' → 'తెలుగు'
    app.config['LANGUAGES'] = {
        'en': 'English',
        'hi': 'हिंदी',
        'te': 'తెలుగు'
    }
    # Default locale & timezone
    app.config['BABEL_DEFAULT_LOCALE'] = 'en'
    app.config['BABEL_DEFAULT_TIMEZONE'] = 'UTC'

    # Initialize Babel
    babel.init_app(app)

    @babel.localeselector
    def get_locale():
        """
        1) If the user has explicitly chosen a language (in session), use that.
        2) Otherwise, fall back to the browser’s Accept-Language header.
        3) If no acceptable match found, use 'en'.
        """
        # Check session first
        if 'language' in session:
            return session['language']
        # Otherwise, auto‐detect from Accept‐Language
        return request.accept_languages.best_match(app.config['LANGUAGES'].keys()) or 'en'

    # ----------------------------
    # Login Manager Configuration
    # ----------------------------
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        # Import here to avoid circular import
        from models import User
        return User.query.get(int(user_id))

    # ----------------------------
    # Make get_locale (and LANGUAGES) available to all templates
    # ----------------------------
    @app.context_processor
    def inject_config():
        """
        Makes LANGUAGES available in all templates (for language dropdowns, etc.),
        and also exposes get_locale() so you can do {{ get_locale() }} in base.html.
        """
        return {
            'LANGUAGES': app.config['LANGUAGES'],
            'get_locale': get_locale
        }

    # ----------------------------
    # Create Database Tables & Sample Data
    # ----------------------------
    with app.app_context():
        import models  # ensure all model classes are registered
        db.create_all()
        logging.info("Database tables created successfully")
        # If you have a helper to seed sample data, call it here:
        try:
            models.create_sample_data()
            logging.info("Sample data created successfully")
        except AttributeError:
            # If you don’t have create_sample_data, just ignore
            pass

    # ----------------------------
    # Register Blueprints (Routes)
    # ----------------------------
    with app.app_context():
        from routes import main_bp, auth_bp, buyer_bp, seller_bp, ai_bp

        # main_bp has '/', '/set-language/<lang_code>', etc.
        app.register_blueprint(main_bp)

        # auth_bp has '/register', '/login', '/logout'
        app.register_blueprint(auth_bp, url_prefix='/auth')

        # buyer_bp has '/dashboard', '/search', '/api/find-farmers', '/api/contact-farmer'
        app.register_blueprint(buyer_bp, url_prefix='/buyer')

        # seller_bp has '/dashboard', '/new-listing', '/edit-listing/...'
        app.register_blueprint(seller_bp, url_prefix='/seller')

        # ai_bp has '/chat', '/api/chat', '/market-analysis', '/api/price-prediction'
        app.register_blueprint(ai_bp, url_prefix='/ai')

    # ----------------------------
    # Context Processors & Globals
    # ----------------------------
    # (inject_config already defined above)

    # ----------------------------
    # After‐Request Hook for On‐the‐Fly Translation
    # ----------------------------
    @app.after_request
    def apply_translations(response):
        """
        After any HTML page is rendered, replace literal English keys
        with their Hindi/Telugu translations (via translate_html).
        
        This means you do NOT need to wrap any text in your templates
        with gettext or modify them at all. Plain English remains in templates.
        """
        content_type = response.headers.get('Content-Type', '')
        if 'text/html' in content_type.lower():
            # Determine which language to use:
            lang = session.get('language', None)
            if not lang or lang not in app.config['LANGUAGES']:
                # If user never clicked /set-language, auto‐detect via Babel’s logic
                lang = get_locale() or 'en'
            # Get the rendered HTML as a string
            rendered_html = response.get_data(as_text=True)
            # Perform literal string‐replace of each English key
            new_html = translate_html(rendered_html, lang)
            # Swap in the translated HTML
            response.set_data(new_html)
        return response

    return app


# ----------------------------
# Create & Run the Application
# ----------------------------
if __name__ == '__main__':
    app = create_app()
    # Debug=True is fine for local development
    app.run(debug=True)
