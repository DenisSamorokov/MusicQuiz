from flask import Flask, request, session, redirect, url_for, render_template
from flask_socketio import SocketIO
from flask_login import LoginManager
from flask_cors import CORS
from translations import TRANSLATIONS
import os
from models.models import init_db, User, db
from routes.routes import init_routes
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'uxyn32814ex9d3_i23ur8iu2')
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///music_quiz.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SUPPORTED_LOCALES'] = ['ru', 'en', 'es', 'zh', 'ja', 'pt', 'fr', 'de']

socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*", ping_timeout=10, ping_interval=5, max_http_buffer_size=1000000)
CORS(app, resources={r"/*": {"origins": "*"}})

init_db(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def get_translations():
    lang = session.get('lang', request.accept_languages.best_match(app.config['SUPPORTED_LOCALES']) or 'ru')
    return TRANSLATIONS.get(lang, TRANSLATIONS['ru'])

@app.context_processor
def inject_translations():
    return dict(t=get_translations(), get_locale=lambda: session.get('lang', request.accept_languages.best_match(app.config['SUPPORTED_LOCALES']) or 'ru'))

init_routes(app, socketio)

if __name__ == '__main__':
    socketio.run(app, host='127.0.0.1', port=5000, debug=True)