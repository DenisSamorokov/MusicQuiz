from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    score = db.Column(db.Integer, default=0)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()
        if not Message.query.first():
            welcome_message = Message(
                username='Система',
                message='Добро пожаловать в чат Music Quiz!',
                timestamp=datetime.utcnow()
            )
            db.session.add(welcome_message)
            db.session.commit()
