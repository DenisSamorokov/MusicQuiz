from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, make_response, Response
from flask_login import login_user, login_required, logout_user, current_user
from flask_socketio import emit
from models.models import User, Message, ScoreEvent, db
from utils.track_utils import select_track_and_options
from translations import TRANSLATIONS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import requests
import eventlet
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def init_routes(app: Flask, socketio=None):
    @app.context_processor
    def inject_user_ranks_and_translations():
        if current_user.is_authenticated:
            time_threshold = datetime.utcnow() - timedelta(hours=24)
            try:
                user_daily_rank = db.session.query(
                    db.func.count().label('rank')
                ).select_from(User).join(ScoreEvent, User.id == ScoreEvent.user_id, isouter=True).filter(
                    ScoreEvent.timestamp >= time_threshold,
                    User.score > current_user.score
                ).scalar()
                user_daily_rank = (user_daily_rank or 0) + 1
            except Exception as e:
                logger.error(f"Ошибка при вычислении user_daily_rank: {e}")
                user_daily_rank = None
            user_overall_rank = User.query.filter(User.score > current_user.score).count() + 1
        else:
            user_daily_rank = None
            user_overall_rank = None
        current_locale = session.get('lang', request.accept_languages.best_match(['ru', 'en', 'es', 'zh', 'ja', 'pt', 'fr', 'de']) or 'ru')
        translations = TRANSLATIONS.get(current_locale, TRANSLATIONS['ru'])
        return dict(
            user_daily_rank=user_daily_rank,
            user_overall_rank=user_overall_rank,
            t=translations,
            get_locale=lambda: current_locale,
            config={'SUPPORTED_LOCALES': ['ru', 'en', 'es', 'zh', 'ja', 'pt', 'fr', 'de']}
        )

    def check_deezer_api():
        try:
            response = requests.get("https://api.deezer.com/ping", timeout=5)
            return response.status_code == 200
        except requests.RequestException:
            return False

    @app.route('/')
    def index():
        leaders = User.query.order_by(User.score.desc()).limit(5).all()
        time_threshold = datetime.utcnow() - timedelta(hours=24)
        daily_leaders = db.session.query(
            User.username,
            db.func.coalesce(db.func.sum(ScoreEvent.score), 0).label('total_score')
        ).select_from(User).join(ScoreEvent, User.id == ScoreEvent.user_id, isouter=True).filter(
            (ScoreEvent.timestamp >= time_threshold) | (ScoreEvent.timestamp.is_(None))
        ).group_by(User.id, User.username).order_by(db.func.coalesce(db.func.sum(ScoreEvent.score), 0).desc(), User.username).limit(5).all()
        messages = Message.query.order_by(Message.timestamp.desc()).limit(3).all()
        if 'selected_style' not in session:
            session['selected_style'] = 'any'
        return render_template('index.html', leaders=leaders, daily_leaders=daily_leaders, messages=messages)

    @app.route('/play/<difficulty>', methods=['GET', 'POST'])
    @login_required
    def play(difficulty):
        valid_difficulties = ['easy', 'medium', 'hard']
        if difficulty not in valid_difficulties:
            logger.error(f"Invalid difficulty: {difficulty}")
            return render_template('play.html', error="Неверный уровень сложности. Выберите easy, medium или hard.")

        if 'used_artists' not in session or not isinstance(session['used_artists'], dict):
            session['used_artists'] = {'easy': [], 'medium': [], 'hard': []}
        if difficulty not in session['used_artists']:
            session['used_artists'][difficulty] = []
        session['used_artists'][difficulty] = session['used_artists'][difficulty][-100:]

        style = request.args.get('style', session.get('selected_style', 'any'))
        session['selected_style'] = style
        current_locale = session.get('lang', request.accept_languages.best_match(['ru', 'en', 'es', 'zh', 'ja', 'pt', 'fr', 'de']) or 'ru')
        logger.debug(f"Play route: difficulty={difficulty}, style={style}, locale={current_locale}")

        if 'used_track_ids' not in session:
            session['used_track_ids'] = []
        session['used_track_ids'] = session['used_track_ids'][-100:]

        if not check_deezer_api():
            logger.error("Deezer API unavailable")
            return render_template('play.html', error="Сервис Deezer недоступен. Попробуйте позже.")

        leaders = User.query.order_by(User.score.desc()).limit(5).all()
        time_threshold = datetime.utcnow() - timedelta(hours=24)
        daily_leaders = db.session.query(
            User.username,
            db.func.coalesce(db.func.sum(ScoreEvent.score), 0).label('total_score')
        ).select_from(User).join(ScoreEvent, User.id == ScoreEvent.user_id, isouter=True).filter(
            (ScoreEvent.timestamp >= time_threshold) | (ScoreEvent.timestamp.is_(None))
        ).group_by(User.id, User.username).order_by(db.func.coalesce(db.func.sum(ScoreEvent.score), 0).desc(), User.username).limit(5).all()
        messages = Message.query.order_by(Message.timestamp.desc()).limit(3).all()

        if request.method == 'POST':
            guess = request.form.get('guess')
            track_id = request.form.get('track_id')
            if guess and track_id:
                correct = str(guess) == str(track_id)
                if correct:
                    points = {'easy': 5, 'medium': 10, 'hard': 15}.get(difficulty, 5)
                    current_user.score += points
                    score_event = ScoreEvent(user_id=current_user.id, score=points)
                    db.session.add(score_event)
                    db.session.commit()
                    logger.debug(f"Correct guess by {current_user.username}, points added: {points}")
                return jsonify({'status': 'success'})
            logger.error("Invalid guess or track_id in POST request")
            return jsonify({'error': 'Неверные данные'}), 400

        try:
            session_data = dict(session)
            with app.app_context():
                track, options, updated_session_data = eventlet.spawn(
                    select_track_and_options, session_data, difficulty, style, current_locale
                ).wait()
                session.update(updated_session_data)
                session.modified = True
        except Exception as e:
            logger.error(f"Ошибка при загрузке трека: {e}")
            return render_template('play.html', error=f"Не удалось загрузить трек: {str(e)}. Попробуйте снова.")

        if not track or len(options) < 4:
            logger.error(f"Track or options invalid: track={track}, options_count={len(options)}")
            return render_template('play.html', error="Не удалось найти достаточно треков. Попробуйте другой уровень сложности или жанр.")

        duration = {'easy': 30, 'medium': 20, 'hard': 10}.get(difficulty, 30)
        track_for_template = {
            'id': track['id'],
            'title': track['title'],
            'artist': track['artist']['name'],
            'preview': track['preview']
        }
        options_for_template = [
            {
                'id': opt['id'],
                'title': opt['title'],
                'artist': opt['artist']['name'],
                'preview': opt.get('preview', None)
            }
            for opt in options
        ]

        logger.debug(f"Rendering play.html: track={track['title']}, options={len(options)}, duration={duration}")
        response = make_response(render_template('play.html', track=track_for_template, options=options_for_template,
                                                 difficulty=difficulty, duration=duration, style=style,
                                                 leaders=leaders, daily_leaders=daily_leaders, messages=messages))
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response

    @app.route('/proxy/<path:url>')
    def proxy(url):
        try:
            response = requests.get(url, headers={'Origin': 'http://127.0.0.1:5000'}, timeout=5)
            response.raise_for_status()
            return Response(response.content, content_type=response.headers.get('Content-Type'))
        except requests.RequestException as e:
            logger.error(f"Ошибка прокси: {e}")
            return Response("Ошибка загрузки аудио", status=500)

    @app.route('/preload/<difficulty>/<style>')
    def preload(difficulty, style):
        if not check_deezer_api():
            logger.error("Deezer API unavailable for preload")
            return jsonify({'error': 'Сервис Deezer недоступен'}), 503

        current_locale = session.get('lang', request.accept_languages.best_match(['ru', 'en', 'es', 'zh', 'ja', 'pt', 'fr', 'de']) or 'ru')
        try:
            session_data = dict(session)
            with app.app_context():
                track, options, updated_session_data = eventlet.spawn(
                    select_track_and_options, session_data, difficulty, style, current_locale
                ).wait()
                session.update(updated_session_data)
                session.modified = True
            if not track:
                logger.error("No track returned for preload")
                return jsonify({'error': 'Не удалось загрузить трек'}), 500
            return jsonify({
                'track': {
                    'id': track['id'],
                    'title': track['title'],
                    'artist': track['artist']['name'],
                    'preview_url': track['preview']
                },
                'options': [
                    {
                        'id': opt['id'],
                        'title': opt['title'],
                        'artist': opt['artist']['name'],
                        'preview_url': opt.get('preview', None)
                    } for opt in options
                ]
            })
        except Exception as e:
            logger.error(f"Ошибка при предзагрузке трека: {e}")
            return jsonify({'error': 'Не удалось загрузить трек'}), 500

    @app.route('/set_filter', methods=['POST'])
    @login_required
    def set_filter():
        data = request.get_json()
        style = data.get('style', 'any')
        session['selected_style'] = style
        session['game_state'] = 'new'
        logger.debug(f"Set filter: style={style}")
        return jsonify({'status': 'success', 'style': style})

    @app.route('/set_language', methods=['GET', 'POST'])
    def set_language():
        supported_locales = ['ru', 'en', 'es', 'zh', 'ja', 'pt', 'fr', 'de']
        if request.method == 'POST':
            data = request.get_json()
            lang = data.get('lang', 'ru')
            if lang in supported_locales:
                session['lang'] = lang
                logger.debug(f"Language set to: {lang} via POST")
                return jsonify({'status': 'success', 'lang': lang})
            logger.error(f"Invalid language: {lang}")
            return jsonify({'error': 'Invalid language'}), 400
        else:
            lang = request.args.get('lang', 'ru')
            if lang in supported_locales:
                session['lang'] = lang
                logger.debug(f"Language set to: {lang} via GET")
                return redirect(request.referrer or url_for('index'))
            logger.error(f"Invalid language: {lang}")
            return redirect(request.referrer or url_for('index'))

    @app.route('/reset-session', methods=['POST'])
    @login_required
    def reset_session():
        session.clear()
        session['used_track_ids'] = []
        session['used_artists'] = {'easy': [], 'medium': [], 'hard': []}
        session['last_artist_index'] = {'easy': 0, 'medium': 0, 'hard': 0}
        session['failed_artists'] = []
        session['selected_style'] = 'any'
        flash("История использованных треков и фильтры сброшены.", "success")
        return redirect(url_for('index'))

    @app.route('/leaderboard')
    def leaderboard():
        leaders = User.query.order_by(User.score.desc()).limit(5).all()
        time_threshold = datetime.utcnow() - timedelta(hours=24)
        daily_leaders = db.session.query(
            User.username,
            db.func.coalesce(db.func.sum(ScoreEvent.score), 0).label('total_score')
        ).select_from(User).join(ScoreEvent, User.id == ScoreEvent.user_id, isouter=True).filter(
            (ScoreEvent.timestamp >= time_threshold) | (ScoreEvent.timestamp.is_(None))
        ).group_by(User.id, User.username).order_by(db.func.coalesce(db.func.sum(ScoreEvent.score), 0).desc(), User.username).limit(5).all()
        if current_user.is_authenticated:
            try:
                user_daily_rank = db.session.query(
                    db.func.count().label('rank')
                ).select_from(User).join(ScoreEvent, User.id == ScoreEvent.user_id, isouter=True).filter(
                    ScoreEvent.timestamp >= time_threshold,
                    User.score > current_user.score
                ).scalar()
                user_daily_rank = (user_daily_rank or 0) + 1
            except Exception as e:
                logger.error(f"Ошибка при вычислении user_daily_rank: {e}")
                user_daily_rank = None
            user_overall_rank = User.query.filter(User.score > current_user.score).count() + 1
        else:
            user_daily_rank = None
            user_overall_rank = None
        return render_template('leaderboard.html', leaders=leaders, daily_leaders=daily_leaders,
                               user_daily_rank=user_daily_rank, user_overall_rank=user_overall_rank)

    @app.route('/chat')
    @login_required
    def chat():
        leaders = User.query.order_by(User.score.desc()).limit(5).all()
        time_threshold = datetime.utcnow() - timedelta(hours=24)
        daily_leaders = db.session.query(
            User.username,
            db.func.coalesce(db.func.sum(ScoreEvent.score), 0).label('total_score')
        ).select_from(User).join(ScoreEvent, User.id == ScoreEvent.user_id, isouter=True).filter(
            (ScoreEvent.timestamp >= time_threshold) | (ScoreEvent.timestamp.is_(None))
        ).group_by(User.id, User.username).order_by(db.func.coalesce(db.func.sum(ScoreEvent.score), 0).desc(), User.username).limit(5).all()
        messages = Message.query.order_by(Message.timestamp.desc()).all()
        logger.debug(f"Загружено сообщений для чата: {len(messages)}")
        return render_template('chat.html', messages=messages, leaders=leaders, daily_leaders=daily_leaders)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')
            user = User.query.filter_by(username=username).first()
            if user and check_password_hash(user.password, password):
                login_user(user)
                session['selected_style'] = 'any'
                logger.debug(f"Пользователь {username} вошёл в систему")
                return redirect(url_for('index'))
            else:
                flash('Неверное имя пользователя или пароль.', 'error')
                logger.warning(f"Неудачная попытка входа: {username}")
        return render_template('login.html')

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')
            if User.query.filter_by(username=username).first():
                flash('Имя пользователя уже занято.', 'error')
                logger.warning(f"Попытка регистрации с занятым именем: {username}")
            else:
                user = User(username=username, password=generate_password_hash(password))
                db.session.add(user)
                db.session.commit()
                login_user(user)
                session['selected_style'] = 'any'
                flash('Регистрация успешна! Добро пожаловать!', 'success')
                logger.debug(f"Пользователь {username} зарегистрирован")
                return redirect(url_for('index'))
        return render_template('register.html')

    @app.route('/logout')
    @login_required
    def logout():
        session['selected_style'] = 'any'
        logger.debug(f"Пользователь {current_user.username} вышел")
        logout_user()
        return redirect(url_for('index'))

    @socketio.on('connect')
    def handle_connect():
        messages = Message.query.order_by(Message.timestamp.desc()).all()
        logger.debug(f"Клиент подключился, отправлено сообщений: {len(messages)}")
        for message in messages:
            emit('chat_message', {
                'username': message.username,
                'message': message.message,
                'timestamp': message.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            })

    @socketio.on('send_message')
    def handle_message(data):
        if not current_user.is_authenticated:
            logger.warning("Попытка отправки сообщения неавторизованным пользователем")
            return
        message_text = data.get('message', '').strip()
        if not message_text:
            logger.warning("Пустое сообщение не сохранено")
            return
        message = Message(
            username=current_user.username,
            message=message_text,
            timestamp=datetime.utcnow()
        )
        db.session.add(message)
        db.session.commit()
        logger.debug(f"Сообщение сохранено: {message_text} от {current_user.username}")
        emit('chat_message', {
            'username': message.username,
            'message': message.message,
            'timestamp': message.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        }, broadcast=True)