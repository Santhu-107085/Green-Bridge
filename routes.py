# routes.py

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, session, jsonify, g, current_app
)
from flask_login import login_user, logout_user, login_required, current_user
from flask_babel import _, get_locale
from models import User, RiceListing, ChatMessage, MarketAnalysis
from application import db
from ai_service import get_ai_response, get_market_analysis, get_price_prediction
from utils import geocode_location, calculate_distance
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime

# ----------------------------
# Blueprint Definitions
# ----------------------------

main_bp = Blueprint('main', __name__)
auth_bp = Blueprint('auth', __name__)
buyer_bp = Blueprint('buyer', __name__)
seller_bp = Blueprint('seller', __name__)
ai_bp = Blueprint('ai', __name__)

# ----------------------------
# Main Blueprint Routes
# ----------------------------

@main_bp.before_request
def before_request():
    """Set global locale for each request"""
    g.locale = str(get_locale())

@main_bp.route('/')
def index():
    """Landing page with basic statistics"""
    total_farmers = User.query.filter_by(user_type='seller').count()
    total_listings = RiceListing.query.filter_by(is_available=True).count()
    rice_types = db.session.query(RiceListing.rice_type).distinct().count()

    return render_template(
        'index.html',
        total_farmers=total_farmers,
        total_listings=total_listings,
        rice_types=rice_types
    )

@main_bp.route('/set-language/<lang_code>')
def set_language(lang_code):
    """
    Switch the user's language preference. Saves into session and redirects back.
    """
    if lang_code in current_app.config.get('LANGUAGES', []):
        session['language'] = lang_code
    return redirect(request.referrer or url_for('main.index'))


# ----------------------------
# Authentication Blueprint Routes
# ----------------------------

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """User registration"""
    if request.method == 'POST':
        full_name = request.form.get('full_name')
        mobile_number = request.form.get('mobile_number')
        location = request.form.get('location')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        user_type = request.form.get('user_type', 'buyer')
        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')

        # Validation
        if not all([full_name, mobile_number, location, password, confirm_password]):
            flash(_('All fields are required'), 'error')
            return render_template('auth/register.html')

        if password != confirm_password:
            flash(_('Passwords do not match'), 'error')
            return render_template('auth/register.html')

        if User.query.filter_by(mobile_number=mobile_number).first():
            flash(_('Mobile number already registered'), 'error')
            return render_template('auth/register.html')

        # Create and save new user
        user = User(
            full_name=full_name,
            mobile_number=mobile_number,
            location=location,
            user_type=user_type,
            latitude=float(latitude) if latitude else None,
            longitude=float(longitude) if longitude else None
        )
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        flash(_('Registration successful! Please login.'), 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """User login"""
    if request.method == 'POST':
        mobile_number = request.form.get('mobile_number')
        password = request.form.get('password')

        user = User.query.filter_by(mobile_number=mobile_number).first()

        if user and user.check_password(password):
            login_user(user)
            flash(_('Login successful!'), 'success')

            # Redirect based on user type
            if user.user_type == 'seller':
                return redirect(url_for('seller.dashboard'))
            else:
                return redirect(url_for('buyer.dashboard'))

        flash(_('Invalid mobile number or password'), 'error')

    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    """User logout"""
    logout_user()
    flash(_('You have been logged out'), 'success')
    return redirect(url_for('main.index'))


# ----------------------------
# Buyer Blueprint Routes
# ----------------------------

@buyer_bp.route('/dashboard')
@login_required
def dashboard():
    """Buyer dashboard"""
    return render_template('buyer/dashboard.html')


@buyer_bp.route('/search')
@login_required
def search():
    """Search rice listings with optional filtering by rice type and distance"""
    rice_type = request.args.get('rice_type')
    max_distance = request.args.get('max_distance', 50, type=int)

    # Base query: only available listings
    query = RiceListing.query.filter_by(is_available=True)

    if rice_type:
        query = query.filter_by(rice_type=rice_type)

    listings = query.all()

    # If buyer has location, filter by distance
    if current_user.latitude and current_user.longitude:
        filtered_listings = []
        for listing in listings:
            if listing.seller.latitude and listing.seller.longitude:
                distance = current_user.get_distance_to(
                    listing.seller.latitude,
                    listing.seller.longitude
                )
                if distance is not None and distance <= max_distance:
                    listing.distance = distance
                    filtered_listings.append(listing)

        # Sort by computed distance
        listings = sorted(filtered_listings, key=lambda x: x.distance)

    # Prepare map data points
    map_data = []
    for listing in listings:
        if listing.seller.latitude and listing.seller.longitude:
            map_data.append({
                'id': listing.id,
                'lat': listing.seller.latitude,
                'lng': listing.seller.longitude,
                'title': f"{listing.rice_type} - ₹{listing.price_per_kg}/kg",
                'seller_name': listing.seller.full_name,
                'quantity': listing.quantity,
                'price': listing.price_per_kg
            })

    return render_template(
        'buyer/search.html',
        listings=listings,
        map_data=map_data,
        selected_rice_type=rice_type
    )


@buyer_bp.route('/api/find-farmers', methods=['POST'])
@login_required
def find_farmers():
    """API endpoint: find nearby farmers matching rice type and quantity"""
    data = request.get_json() or {}
    rice_type = data.get('riceType')
    quantity = float(data.get('quantity', 0))
    unit = data.get('unit')
    location_text = data.get('location')

    # Convert units to kilograms
    if unit == 'quintal':
        quantity *= 100
    elif unit == 'ton':
        quantity *= 1000

    query = RiceListing.query.filter_by(is_available=True)
    if rice_type:
        query = query.filter_by(rice_type=rice_type)

    # Filter by minimum quantity
    query = query.filter(RiceListing.quantity >= quantity)

    listings = query.all()

    # Build list of farmers with distances
    farmers = []
    for listing in listings:
        if (listing.seller.latitude and listing.seller.longitude and
                current_user.latitude and current_user.longitude):
            distance = current_user.get_distance_to(
                listing.seller.latitude,
                listing.seller.longitude
            )
            farmers.append({
                'id': listing.seller_id,
                'name': listing.seller.full_name,
                'location': listing.seller.location,
                'distance': round(distance, 1) if distance is not None else 0,
                'available_quantity': listing.quantity,
                'price_per_kg': listing.price_per_kg,
                'listing_id': listing.id
            })

    # Sort results by distance and return top 10
    farmers.sort(key=lambda x: x['distance'])
    return jsonify({'farmers': farmers[:10]})


@buyer_bp.route('/api/contact-farmer/<int:farmer_id>', methods=['POST'])
@login_required
def contact_farmer(farmer_id):
    """API endpoint: contact a farmer (dummy implementation)"""
    farmer = User.query.get_or_404(farmer_id)

    # In a real app, you'd send a message/notification here. For demo, just return success.
    return jsonify({
        'success': True,
        'message': _('Contact request sent to %(farmer_name)s', farmer_name=farmer.full_name),
        'farmer_mobile': farmer.mobile_number
    })


# ----------------------------
# Seller Blueprint Routes
# ----------------------------

@seller_bp.route('/dashboard')
@login_required
def dashboard():
    """Seller dashboard showing their listings and stats"""
    listings = RiceListing.query.filter_by(seller_id=current_user.id).all()
    total_listings = len(listings)
    active_listings = len([l for l in listings if l.is_available])
    total_quantity = sum(l.quantity for l in listings if l.is_available)

    return render_template(
        'seller/dashboard.html',
        listings=listings,
        total_listings=total_listings,
        active_listings=active_listings,
        total_quantity=total_quantity
    )


@seller_bp.route('/new-listing', methods=['GET', 'POST'])
@login_required
def new_listing():
    """Create a new rice listing"""
    if request.method == 'POST':
        rice_type = request.form.get('rice_type')
        quantity = request.form.get('quantity')
        price_per_kg = request.form.get('price_per_kg')
        quality_grade = request.form.get('quality_grade', 'A')
        description = request.form.get('description')

        if not all([rice_type, quantity, price_per_kg]):
            flash(_('Required fields are missing'), 'error')
            return render_template('seller/new_listing.html')

        listing = RiceListing(
            seller_id=current_user.id,
            rice_type=rice_type,
            quantity=float(quantity),
            price_per_kg=float(price_per_kg),
            quality_grade=quality_grade,
            description=description
        )

        db.session.add(listing)
        db.session.commit()

        flash(_('Listing created successfully!'), 'success')
        return redirect(url_for('seller.dashboard'))

    return render_template('seller/new_listing.html')


@seller_bp.route('/edit-listing/<int:listing_id>', methods=['POST'])
@login_required
def edit_listing(listing_id):
    """Edit or toggle availability of an existing listing"""
    listing = RiceListing.query.get_or_404(listing_id)

    if listing.seller_id != current_user.id:
        flash(_('Unauthorized access'), 'error')
        return redirect(url_for('seller.dashboard'))

    data = request.get_json() or {}

    if 'is_available' in data:
        listing.is_available = data['is_available']
        db.session.commit()
        status = _('activated') if listing.is_available else _('deactivated')
        return jsonify({
            'success': True,
            'message': _('Listing %(status)s successfully', status=status)
        })

    return jsonify({'success': False, 'message': _('Invalid request')})


# ----------------------------
# AI Blueprint Routes
# ----------------------------

@ai_bp.route('/chat')
@login_required
def chat():
    """Render AI chat interface with recent history"""
    chat_history = (
        ChatMessage.query
        .filter_by(user_id=current_user.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(10)
        .all()
    )
    chat_history.reverse()  # Show oldest first
    return render_template('ai/chat.html', chat_history=chat_history)


@ai_bp.route('/api/chat', methods=['POST'])
@login_required
def api_chat():
    """API endpoint for AI chat"""
    data = request.get_json() or {}
    message = data.get('message', '').strip()

    if not message:
        return jsonify({'error': _('Message cannot be empty')}), 400

    try:
        # Call your AI service to get a response
        response = get_ai_response(message, current_user)

        # Save the chat message to the database
        chat_message = ChatMessage(
            user_id=current_user.id,
            message=message,
            response=response,
            created_at=datetime.utcnow()
        )
        db.session.add(chat_message)
        db.session.commit()

        return jsonify({'response': response})

    except Exception:
        return jsonify({'error': _('Sorry, I encountered an error. Please try again.')}), 500


@ai_bp.route('/market-analysis')
@login_required
def market_analysis():
    """Render market analysis page with data from AI service"""
    analysis = get_market_analysis()
    return render_template('ai/market_analysis.html', analysis=analysis)


@ai_bp.route('/api/price-prediction', methods=['POST'])
@login_required
def api_price_prediction():
    """API endpoint for price prediction"""
    data = request.get_json() or {}
    rice_type = data.get('rice_type')
    quantity = data.get('quantity', 1)

    if not rice_type:
        return jsonify({'error': _('Rice type is required')}), 400

    try:
        prediction = get_price_prediction(rice_type, quantity)
        return jsonify(prediction)
    except Exception:
        return jsonify({'error': _('Unable to predict price at this time')}), 500
@main_bp.route('/debug-lang')
def debug_lang():
    # Simply return the current session language (or “None”)
    lang = session.get('language', None)
    return f"Current session['language'] = {lang}"
