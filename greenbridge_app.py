import os
import logging
from io import StringIO
from flask import (
    Flask, render_template, request,Response, redirect, url_for,
    flash, session, jsonify, g
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin,
    login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime, timezone, date
import math
from models import User, RiceListing, ChatMessage, MarketAnalysis
# Import your translation helper
from translations import translate_html

# ----------------------------
# Configure logging
# ----------------------------
logging.basicConfig(level=logging.DEBUG)

# ----------------------------
# SQLAlchemy Declarative Base
# ----------------------------
class Base(DeclarativeBase):
    pass

# ----------------------------
# Initialize Flask app
# ----------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# ----------------------------
# Database configuration
# ----------------------------
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///greenbridge.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}

# ----------------------------
# Language (i18n) configuration
# ----------------------------
app.config['LANGUAGES'] = {
    'en': 'English',
    'hi': 'हिंदी',
    'te': 'తెలుగు'
}

# ----------------------------
# Initialize extensions
# ----------------------------
db = SQLAlchemy(app, model_class=Base)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


# ----------------------------
# Context processor: expose get_locale and LANGUAGES to Jinja
# ----------------------------
@app.context_processor
def inject_locale_and_languages():
    """
    Make get_locale() and LANGUAGES available in every template.
    get_locale() returns session['language'] or 'en' if not set.
    LANGUAGES is the app.config['LANGUAGES'] dict.
    """
    def get_locale():
        return session.get('language', 'en')
    return {
        'get_locale': get_locale,
        'LANGUAGES': app.config['LANGUAGES']
    }


# ----------------------------
# Models
# ----------------------------
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    mobile_number = db.Column(db.String(15), unique=True, nullable=False)
    location = db.Column(db.String(200), nullable=False)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    user_type = db.Column(db.String(20), default='buyer')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    rice_listings = db.relationship(
        'RiceListing', backref='seller', lazy=True,
        cascade='all, delete-orphan'
    )
    chat_messages = db.relationship(
        'ChatMessage', backref='user', lazy=True,
        cascade='all, delete-orphan'
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_distance_to(self, other_lat, other_lng):
        if not self.latitude or not self.longitude:
            return float('inf')
        return calculate_distance(
            self.latitude, self.longitude, other_lat, other_lng
        )


class RiceListing(db.Model):
    __tablename__ = 'rice_listings'
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(
        db.Integer, db.ForeignKey('users.id'), nullable=False
    )
    rice_type = db.Column(db.String(50), nullable=False)
    variety = db.Column(db.String(100), nullable=True)
    quantity = db.Column(db.Float, nullable=False)
    price_per_kg = db.Column(db.Float, nullable=False)
    quality_grade = db.Column(db.String(20), default='A')
    harvest_date = db.Column(db.Date, nullable=True)
    processing_type = db.Column(db.String(50), default='Raw')
    organic = db.Column(db.Boolean, default=False)
    description = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(200), nullable=True)
    is_available = db.Column(db.Boolean, default=True)
    minimum_order = db.Column(db.Float, default=10.0)
    storage_location = db.Column(db.String(200), nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    def total_value(self):
        return self.quantity * self.price_per_kg


class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('users.id'), nullable=False
    )
    message = db.Column(db.Text, nullable=False)
    response = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(50), default='general')
    context_data = db.Column(db.JSON, nullable=True)
    satisfaction_rating = db.Column(db.Integer, nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

class PriceAlert(db.Model):
    __tablename__ = 'price_alerts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    rice_type = db.Column(db.String(50), nullable=False)
    target_price = db.Column(db.Float, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
   )

import csv

# ────────────────────────────────────────────────────────────────────────────────
# ENDPOINT: /set-price-alert  (POST)
# ────────────────────────────────────────────────────────────────────────────────
@app.route('/set-price-alert', methods=['POST'])
@login_required
def set_price_alert():
    """
    Expects JSON: { "rice_type": <str>, "target_price": <float> }
    Saves a new PriceAlert row for current_user.
    Returns { success: True/False, message: str, error: str? }.
    """
    data = request.get_json() or {}
    rice_type = data.get('rice_type', '').strip()
    target_price = data.get('target_price')

    if not rice_type or target_price is None:
        return jsonify({
            'success': False,
            'error': 'Both rice_type and target_price are required.'
        }), 400

    try:
        target_price = float(target_price)
    except ValueError:
        return jsonify({
            'success': False,
            'error': 'Invalid target_price (must be a number).'
        }), 400

    # Save the alert
    alert = PriceAlert(
        user_id=current_user.id,
        rice_type=rice_type,
        target_price=target_price
    )
    db.session.add(alert)
    db.session.commit()

    return jsonify({
        'success': True,
        'message': f'Price alert set for {rice_type} at ₹{target_price:.2f}.'
    })

# ────────────────────────────────────────────────────────────────────────────────
# ENDPOINT: /export-data  (GET)
# ────────────────────────────────────────────────────────────────────────────────
@app.route('/export-data')
@login_required
def export_data():
    """
    Streams all MarketAnalysis rows as a CSV download.
    Columns: Rice Type, Region, Avg Price, Trend, Demand, Supply,
             Sentiment, Insights, Analysis Date
    """
    si = StringIO()
    cw = csv.writer(si)
    # Header row
    cw.writerow([
        'Rice Type', 'Region', 'Average Price', 'Price Trend',
        'Demand Level', 'Supply Level', 'Market Sentiment',
        'Insights', 'Analysis Date'
    ])

    # Fetch and write all MarketAnalysis records
    all_rows = MarketAnalysis.query.all()
    for m in all_rows:
        cw.writerow([
            m.rice_type,
            m.region,
            m.average_price,
            m.price_trend,
            m.demand_level,
            m.supply_level,
            m.market_sentiment,
            m.insights,
            m.analysis_date.strftime('%Y-%m-%d')
        ])

    # Return CSV as a downloadable response
    return Response(
        si.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition': 'attachment; filename=market_data.csv'
        }
    )

class MarketAnalysis(db.Model):
    __tablename__ = 'market_analysis'
    id = db.Column(db.Integer, primary_key=True)
    rice_type = db.Column(db.String(50), nullable=False)
    region = db.Column(db.String(100), nullable=False)
    average_price = db.Column(db.Float, nullable=False)
    price_trend = db.Column(db.String(20), nullable=False)
    demand_level = db.Column(db.String(20), nullable=False)
    supply_level = db.Column(db.String(20), nullable=False)
    market_sentiment = db.Column(db.String(20), default='neutral')
    insights = db.Column(db.Text, nullable=True)
    data_source = db.Column(db.String(100), default='AI Analysis')
    confidence_score = db.Column(db.Float, default=0.8)
    analysis_date = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )


# ----------------------------
# Flask-Login user loader
# ----------------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ----------------------------
# Utility functions
# ----------------------------
def calculate_distance(lat1, lon1, lat2, lon2):
    """Haversine formula (returns kilometers)."""
    R = 6371  # Earth’s radius in km

    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))

    return R * c

import google.generativeai as genai
import sqlite3
from datetime import datetime

#genai.configure(api_key="AIzaSyBiOCbcv1eqK0eKFQdqYH3EUMBGQdYNWdY")  # Replace with your API key
genai.configure(api_key="AIzaSyCrpnSDD6pa2G8IZ-psY8h7v3IQkD27DmU")
model = genai.GenerativeModel("gemini-1.5-flash")

def generate_generativeai_response(input_text):
    """Generate a response using Google Generative AI."""
    try:
        response = model.generate_content(input_text)
        return response  # Extract generated text
    except Exception as e:
        return f"An error occurred while generating the response: {e}"




def get_ai_response(message, user):
    """
    Generate a dynamic AI response for all queries and log to the database.
    :param message: User input message (str)
    :param user: User object with attributes like location
    :return: AI-generated response (str)
    """
    input_text = f"User Location: {user.location}. Message: {message}"
    response = generate_generativeai_response(input_text)
    print(response)
    # Log the interaction
   # log_query_to_db(user.id, user.location, message, response)
    
    return response
    """
def get_ai_response(message, user):
#Simple AI-style canned response for demonstration.
    responses = {
        'price': (
            f"Based on current market trends, rice prices are stable. "
            f"For {user.location}, expect prices around ₹40–60/kg depending on variety."
        ),
        'market': (
            "The rice market is currently experiencing steady demand with seasonal variations. "
            "Basmati and premium varieties show strong performance."
        ),
        'weather': (
            "Weather conditions are favorable for rice cultivation this season. "
            "Monitor moisture levels and consider organic farming practices."
        ),
        'default': "I’m here to help with rice trading, market analysis, and farming advice. What do you need?"
    }

    msg = message.lower()
    if any(w in msg for w in ['price', 'cost', 'rate']):
        return responses['price']
    elif any(w in msg for w in ['market', 'demand', 'supply']):
        return responses['market']
    elif any(w in msg for w in ['weather', 'climate', 'season']):
        return responses['weather']
    else:
        return responses['default']

    """
def create_sample_data():
    """Populate the database with sample users, listings, and analyses."""
    try:
        # If any user already exists, skip
        if User.query.first():
            return

        # ===== Sample Users =====
        farmer1 = User(
            full_name="Ravi Kumar",
            mobile_number="9876543210",
            location="Guntur, Andhra Pradesh",
            latitude=16.2931,
            longitude=80.4374,
            user_type="seller"
        )
        farmer1.set_password("password123")

        buyer1 = User(
            full_name="Priya Sharma",
            mobile_number="9876543211",
            location="Hyderabad, Telangana",
            latitude=17.3850,
            longitude=78.4867,
            user_type="buyer"
        )
        buyer1.set_password("password123")

        db.session.add(farmer1)
        db.session.add(buyer1)
        db.session.commit()

        # ===== Sample Rice Listings =====
        listing1 = RiceListing(
            seller_id=farmer1.id,
            rice_type="Basmati",
            variety="1121 Golden Sella",
            quantity=1000.0,
            price_per_kg=55.0,
            quality_grade="A",
            harvest_date=date(2024, 11, 15),
            processing_type="Steamed",
            organic=False,
            description="Premium quality Basmati rice, aged for 2 years",
            minimum_order=50.0,
            storage_location="Climate controlled warehouse"
        )

        listing2 = RiceListing(
            seller_id=farmer1.id,
            rice_type="Sona Masoori",
            variety="HMT",
            quantity=500.0,
            price_per_kg=42.0,
            quality_grade="A",
            harvest_date=date(2024, 10, 20),
            processing_type="Raw",
            organic=True,
            description="Organic Sona Masoori rice from sustainable farming",
            minimum_order=25.0,
            storage_location="Traditional storage"
        )

        db.session.add(listing1)
        db.session.add(listing2)
        db.session.commit()

        # ===== Sample Market Analysis =====
        analysis1 = MarketAnalysis(
            rice_type="Basmati",
            region="Andhra Pradesh",
            average_price=55.0,
            price_trend="stable",
            demand_level="high",
            supply_level="medium",
            market_sentiment="bullish",
            insights=(
                "Strong export demand driving prices upward. "
                "Premium varieties performing exceptionally."
            )
        )

        analysis2 = MarketAnalysis(
            rice_type="Sona Masoori",
            region="Telangana",
            average_price=42.0,
            price_trend="increasing",
            demand_level="medium",
            supply_level="high",
            market_sentiment="neutral",
            insights=(
                "Local demand is steady with a good harvest this season. "
                "Organic varieties command premium prices."
            )
        )

        db.session.add(analysis1)
        db.session.add(analysis2)
        db.session.commit()

        logging.info("Sample data created successfully.")

    except Exception as e:
        logging.error(f"Error creating sample data: {e}")
        db.session.rollback()


# ----------------------------
# Before request: set g.locale (optional)
# ----------------------------
@app.before_request
def before_request():
    g.locale = session.get('language', 'en')


# ----------------------------
# After-request hook: translate rendered HTML
# ----------------------------
@app.after_request
def apply_translations(response):
    """
    After rendering any HTML page, replace literal English keys with
    their translations (via translate_html) using the session's language.
    """
    content_type = response.headers.get('Content-Type', '')
    if 'text/html' in content_type.lower():
        # Which language to translate to?
        lang = session.get('language', None)
        if not lang or lang not in app.config['LANGUAGES']:
            lang = 'en'

        rendered_html = response.get_data(as_text=True)
        new_html = translate_html(rendered_html, lang)
        response.set_data(new_html)

    return response


# ----------------------------
# Routes
# ----------------------------
@app.route('/')
def index():
    total_farmers = User.query.filter_by(user_type='seller').count()
    total_listings = RiceListing.query.filter_by(is_available=True).count()
    rice_types = db.session.query(RiceListing.rice_type).distinct().count()

    return render_template(
        'index.html',
        total_farmers=total_farmers,
        total_listings=total_listings,
        rice_types=rice_types
    )


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name = request.form.get('full_name')
        mobile_number = request.form.get('mobile_number')
        location = request.form.get('location')
        password = request.form.get('password')
        user_type = request.form.get('user_type', 'buyer')

        # Check if user already exists
        if User.query.filter_by(mobile_number=mobile_number).first():
            flash('Mobile number already registered', 'warning')
            return redirect(url_for('register'))

        # Create and save new user
        user = User(
            full_name=full_name,
            mobile_number=mobile_number,
            location=location,
            user_type=user_type
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash('Registration successful. Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('auth/register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        mobile_number = request.form.get('mobile_number')
        password = request.form.get('password')

        user = User.query.filter_by(mobile_number=mobile_number).first()
        if user and user.check_password(password):
            login_user(user)
            next_page = request.args.get('next')
            if user.user_type == 'seller':
                return redirect(next_page or url_for('seller_dashboard'))
            else:
                return redirect(next_page or url_for('buyer_dashboard'))
        else:
            flash('Invalid mobile number or password', 'danger')

    return render_template('auth/login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/buyer/dashboard')
@login_required
def buyer_dashboard():
    # Show up to 10 available listings
    listings = RiceListing.query.filter_by(is_available=True).limit(10).all()

    # Pull market analysis for certain rice types
    analysis = {}
    for rice_type in ['Basmati', 'Sona Masoori', 'Ponni', 'Brown Rice']:
        market_data = MarketAnalysis.query.filter_by(
            rice_type=rice_type
        ).first()
        if market_data:
            analysis[rice_type] = market_data

    return render_template(
        'buyer/dashboard.html',
        listings=listings,
        analysis=analysis
    )


@app.route('/seller/dashboard')
@login_required
def seller_dashboard():
    listings = RiceListing.query.filter_by(seller_id=current_user.id).all()
    total_revenue = sum(
        listing.total_value() for listing in listings if listing.is_available
    )

    return render_template(
        'seller/dashboard.html',
        listings=listings,
        total_revenue=total_revenue
    )


@app.route('/seller/new-listing', methods=['GET', 'POST'])
@login_required
def new_listing():
    if request.method == 'POST':
        listing = RiceListing(
            seller_id=current_user.id,
            rice_type=request.form.get('rice_type'),
            variety=request.form.get('variety'),
            quantity=float(request.form.get('quantity', 0)),
            price_per_kg=float(request.form.get('price_per_kg', 0)),
            quality_grade=request.form.get('quality_grade', 'A'),
            harvest_date=(
                datetime.strptime(
                    request.form.get('harvest_date'), '%Y-%m-%d'
                ).date()
                if request.form.get('harvest_date')
                else None
            ),
            processing_type=request.form.get('processing_type', 'Raw'),
            organic=request.form.get('organic') == 'on',
            description=request.form.get('description'),
            minimum_order=float(request.form.get('minimum_order', 10)),
            storage_location=request.form.get('storage_location')
        )
        db.session.add(listing)
        db.session.commit()

        flash('Listing created successfully', 'success')
        return redirect(url_for('seller_dashboard'))

    return render_template('seller/new_listing.html')


@app.route('/ai/chat')
@login_required
def chat():
    messages = ChatMessage.query.filter_by(
        user_id=current_user.id
    ).order_by(
        ChatMessage.created_at.desc()
    ).limit(10).all()
    return render_template('ai/chat.html', messages=messages)


@app.route('/ai/chat', methods=['POST'])
@login_required
def chat_message():
    try:
        data = request.get_json()
        message = data.get('message', '').strip()
        
        if not message:
            return jsonify({'error': _('Message cannot be empty')}), 400
        
        # Generate AI response using Gemini
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
            
            # Create context-aware prompt
            prompt = f"""
            You are an AI assistant for GreenBridge, a rice trading marketplace in India. 
            The user is asking: "{message}"
            
            Provide helpful information about:
            - Rice market prices and trends
            - Rice varieties and their characteristics
            - Trading advice for rice farmers and buyers
            - Quality assessment of rice
            - Storage and handling best practices
            
            Keep responses concise, practical, and relevant to rice trading in India.
            If you don't have specific current market data, provide general guidance and suggest checking current market rates.
            """
            
            response = model.generate_content(prompt)
            ai_response = response.text
            
        except Exception as e:
            logging.error(f"Gemini API error: {e}")
            ai_response = _("I'm sorry, I'm having trouble processing your request right now. Please try again later.")
        
        # Save chat message to database
        chat_msg = ChatMessage(
            user_id=current_user.id,
            message=message,
            response=ai_response
        )
        
        db.session.add(chat_msg)
        db.session.commit()
        
        return jsonify({
            'response': ai_response,
            'timestamp': chat_msg.created_at.isoformat()
        })
        
    except Exception as e:
        logging.error(f"Chat error: {e}")
        return jsonify({'error': _('An error occurred while processing your message')}), 500
@app.route('/ai/market-analysis')
@login_required
def market_analysis():
    analysis = {}
    rice_types = ['Basmati', 'Sona Masoori', 'Ponni', 'Brown Rice']
    for rice_type in rice_types:
        market_data = MarketAnalysis.query.filter_by(rice_type=rice_type).first()
        if market_data:
            analysis[rice_type] = market_data

    return render_template('ai/market_analysis.html', analysis=analysis)


@app.route('/search')
@login_required
def search():
    query = request.args.get('q', '')
    rice_type = request.args.get('rice_type', '')
    max_price = request.args.get('max_price', type=float)

    listings_query = RiceListing.query.filter_by(is_available=True)
    if rice_type:
        listings_query = listings_query.filter(
            RiceListing.rice_type == rice_type
        )
    if max_price:
        listings_query = listings_query.filter(
            RiceListing.price_per_kg <= max_price
        )
    if query:
        listings_query = listings_query.filter(
            RiceListing.description.contains(query) |
            RiceListing.variety.contains(query)
        )

    listings = listings_query.all()
    return render_template(
        'buyer/search.html',
        listings=listings,
        query=query,
        rice_type=rice_type,
        max_price=max_price
    )


@app.route('/set-language/<language>')
def set_language(language):
    """
    Store the selected language code in session and redirect back.
    """
    if language in app.config['LANGUAGES']:
        session['language'] = language
    return redirect(request.referrer or url_for('index'))


# ----------------------------
# Simple “translate” function for templates (stub)
# ----------------------------
@app.template_global()
def _(text):
    """
    Stub for template-level gettext calls. We do literal string replacement
    via translate_html, so templates can simply write English keys.
    """
    return text


# ----------------------------6. And in farmer dashboard when he adds a new listing there are three listings
    #1.edit2.view3.delete they shoud have to work properly




@app.route('/seller/edit-listing/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_listing(id):
    listing =  RiceListing.query.get_or_404(id)
    if request.method == 'POST':
        listing.rice_type = request.form['rice_type']
        listing.variety = request.form['variety']
        listing.quantity = request.form['quantity']
        listing.price_per_kg = request.form['price_per_kg']
        listing.quality_grade = request.form['quality_grade']
        db.session.commit()
        flash('Listing updated successfully!', 'success')
        return redirect(url_for('seller_dashboard'))
    return render_template('seller/edit_listing.html', listing=listing)

@app.route('/seller/view-listing/<int:id>')
@login_required
def view_listing(id):
    listing =  RiceListing.query.get_or_404(id)
    return render_template('view_listing.html', listing=listing)

@app.route('/seller/delete-listing/<int:id>')
@login_required
def delete_listing(id):
    listing =  RiceListing.query.get_or_404(id)
    db.session.delete(listing)
    db.session.commit()
    flash('Listing deleted successfully!', 'success')
    return redirect(url_for('seller.seller_dashboard'))

@app.route('/seller/toggle-listing/<int:id>/<status>')
@login_required
def toggle_listing(id, status):
    listing =  RiceListing.query.get_or_404(id)
    listing.is_available = status == 'true'
    db.session.commit()
    flash(f'Listing {"activated" if listing.is_available else "deactivated"} successfully!', 'info')
    return redirect(url_for('seller.seller_dashboard'))


# Create tables and sample data
# ----------------------------
with app.app_context():
    db.create_all()
    create_sample_data()
    logging.info("Database initialized successfully.")


# ----------------------------
# Run the server
# ----------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
