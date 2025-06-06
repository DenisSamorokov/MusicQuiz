from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, make_response, Response
from flask_login import login_user, login_required, logout_user, current_user
from flask_socketio import emit
from models.models import User, Message, db
from utils.track_utils import select_track_and_options
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import requests
import eventlet


def init_routes(app: Flask, socketio=None):
    def check_deezer_api():
        try:
            response = requests.get("https://api.deezer.com/ping", timeout=5)
            return response.status_code == 200
        except requests.RequestException:
            return False

    @app.route('/')
    def index():
        leaders = User.query.order_by(User.score.desc()).limit(5).all()
        messages = Message.query.order_by(Message.timestamp.desc()).limit(3).all()
        if 'selected_style' not in session:
            session['selected_style'] = 'any'
        return render_template('index.html', leaders=leaders, messages=messages)

    @app.route('/play/<difficulty>', methods=['GET', 'POST'])
    @login_required
    def play(difficulty):
        valid_difficulties = ['easy', 'medium', 'hard']
        if difficulty not in valid_difficulties:
            flash("Неверный уровень сложности. Выберите easy, medium или hard.", "error")
            return redirect(url_for('index'))

        if 'used_artists' not in session or not isinstance(session['used_artists'], dict):
            session['used_artists'] = {'easy': [], 'medium': [], 'hard': []}
        if difficulty not in session['used_artists']:
            session['used_artists'][difficulty] = []
        session['used_artists'][difficulty] = session['used_artists'][difficulty][-100:]

        style = request.args.get('style', 'any')
        session['selected_style'] = style

        if 'used_track_ids' not in session:
            session['used_track_ids'] = []
        session['used_track_ids'] = session['used_track_ids'][-100:]

        if not check_deezer_api():
            flash("Сервис Deezer недоступен. Попробуйте позже.", "error")
            return redirect(url_for('index'))

        leaders = User.query.order_by(User.score.desc()).limit(5).all()
        messages = Message.query.order_by(Message.timestamp.desc()).limit(3).all()

        try:
            session_data = dict(session)
            with app.app_context():
                track, options, updated_session_data = eventlet.spawn(
                    select_track_and_options, session_data, difficulty, style=style
                ).wait()
                session.update(updated_session_data)
                session.modified = True
        except Exception as e:
            flash("Не удалось загрузить трек. Попробуйте снова.", "error")
            return render_template('index.html', leaders=leaders, messages=messages)

        if not track or len(options) < 4:
            flash("Не удалось найти достаточно треков. Попробуйте другой уровень сложности или жанр.", "error")
            return render_template('index.html', leaders=leaders, messages=messages)

        duration = {'easy': 30, 'medium': 20, 'hard': 10}.get(difficulty, 30)
        if request.method == 'POST':
            guess = request.form.get('guess')
            track_id = request.form.get('track_id')
            track_title = request.form.get('track_title')
            track_artist = request.form.get('track_artist')
            correct = str(guess) == str(track_id)
            if correct:
                points = {'easy': 5, 'medium': 10, 'hard': 15}.get(difficulty, 5)
                current_user.score += points
                db.session.commit()
            return jsonify({
                'correct': correct,
                'track': {'title': track_title, 'artist': track_artist}
            })

        track_for_template = {
            'id': track['id'],
            'title': track['title'],
            'artist': track['artist']['name'],
            'preview_url': track['preview']
        }
        options_for_template = [
            {
                'id': opt['id'],
                'title': opt['title'],
                'artist': opt['artist']['name'],
                'preview_url': opt.get('preview', None)
            }
            for opt in options
        ]

        response = make_response(render_template('play.html', track=track_for_template, options=options_for_template,
                                                 difficulty=difficulty, duration=duration, style=style,
                                                 leaders=leaders, messages=messages))
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response

    @app.route('/proxy/<path:url>')
    def proxy(url):
        try:
            response = requests.get(url, headers={'Origin': 'http://127.0.0.1:5000'}, timeout=5)
            response.raise_for_status()
            return Response(response.content, content_type=response.headers.get('Content-Type'))
        except requests.RequestException as e:
            return Response("Ошибка загрузки аудио", status=500)

    @app.route('/preload/<difficulty>/<style>')
    def preload(difficulty, style):
        if not check_deezer_api():
            return jsonify({'error': 'Сервис Deezer недоступен'}), 503

        try:
            session_data = dict(session)
            with app.app_context():
                correct_track, options, updated_session_data = eventlet.spawn(
                    select_track_and_options, session_data, difficulty, style
                ).wait()
                session.update(updated_session_data)
                session.modified = True
            if not correct_track:
                return jsonify({'error': 'Не удалось загрузить трек'}), 500
            return jsonify({
                'track': {
                    'id': correct_track['id'],
                    'title': correct_track['title'],
                    'artist': correct_track['artist']['name'],
                    'preview_url': correct_track['preview']
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
            return jsonify({'error': 'Не удалось загрузить трек'}), 500

    @app.route('/set_filter', methods=['POST'])
    @login_required
    def set_filter():
        data = request.get_json()
        style = data.get('style', 'any')
        session['selected_style'] = style
        session['game_state'] = 'new'
        return jsonify({'status': 'success', 'style': style})

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
        leaders = User.query.order_by(User.score.desc()).limit(10).all()
        messages = Message.query.order_by(Message.timestamp.desc()).limit(3).all()
        return render_template('leaderboard.html', leaders=leaders, messages=messages)

    @app.route('/chat')
    @login_required
    def chat():
        leaders = User.query.order_by(User.score.desc()).limit(5).all()
        messages = Message.query.order_by(Message.timestamp.desc()).all()
        return render_template('chat.html', messages=messages, leaders=leaders)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')
            user = User.query.filter_by(username=username).first()
            if user and check_password_hash(user.password, password):
                login_user(user)
                session['selected_style'] = 'any'
                return redirect(url_for('index'))
            else:
                flash('Неверное имя пользователя или пароль.', 'error')
        return render_template('login.html')

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')
            if User.query.filter_by(username=username).first():
                flash('Имя пользователя уже занято.', 'error')
            else:
                user = User(username=username, password=generate_password_hash(password))
                db.session.add(user)
                db.session.commit()
                login_user(user)
                session['selected_style'] = 'any'
                flash('Регистрация успешна! Добро пожаловать!', 'success')
                return redirect(url_for('index'))
        return render_template('register.html')

    @app.route('/logout')
    @login_required
    def logout():
        session['selected_style'] = 'any'
        logout_user()
        return redirect(url_for('index'))

    @socketio.on('connect')
    def handle_connect():
        messages = Message.query.order_by(Message.timestamp.desc()).all()
        for message in messages:
            emit('chat_message', {
                'username': message.username,
                'message': message.message,
                'timestamp': message.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            })

    @socketio.on('send_message')
    def handle_message(data):
        message = Message(
            username=current_user.username,
            message=data['message'],
            timestamp=datetime.utcnow()
        )
        db.session.add(message)
        db.session.commit()
        emit('chat_message', {
            'username': message.username,
            'message': message.message,
            'timestamp': message.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        }, broadcast=True)
