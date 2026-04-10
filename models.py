from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'admin' or 'lab'
    lab_id = db.Column(db.Integer, db.ForeignKey('labs.id', ondelete='SET NULL'), nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == 'admin'

class Lab(db.Model):
    __tablename__ = 'labs'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    manager_name = db.Column(db.String(100))
    email = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    users = db.relationship('User', backref='lab', lazy=True)
    commitments = db.relationship('Commitment', backref='lab', lazy=True, cascade='all, delete-orphan')

class Commitment(db.Model):
    __tablename__ = 'commitments'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    lab_id = db.Column(db.Integer, db.ForeignKey('labs.id'), nullable=False)
    assigned_to = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # User được giao
    start_date = db.Column(db.Date, nullable=False)
    deadline = db.Column(db.Date, nullable=False)
    progress = db.Column(db.Integer, default=0)  # 0-100
    status = db.Column(db.String(20), default='Mới')  # Mới, Đang thực hiện, Hoàn thành, Quá hạn
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = db.relationship('User', foreign_keys=[created_by], backref='commitments_created')
    assignee = db.relationship('User', foreign_keys=[assigned_to], backref='tasks_assigned')
    progress_updates = db.relationship('ProgressUpdate', backref='commitment', lazy=True, cascade='all, delete-orphan')

    def update_status(self):
        today = datetime.utcnow().date()
        if self.progress >= 100:
            self.status = 'Hoàn thành'
        elif self.deadline < today and self.progress < 100:
            self.status = 'Quá hạn'
        elif self.progress > 0:
            self.status = 'Đang thực hiện'
        else:
            self.status = 'Mới'

class ProgressUpdate(db.Model):
    __tablename__ = 'progress_updates'

    id = db.Column(db.Integer, primary_key=True)
    commitment_id = db.Column(db.Integer, db.ForeignKey('commitments.id'), nullable=False)
    progress = db.Column(db.Integer, nullable=False)
    notes = db.Column(db.Text)
    attachment = db.Column(db.String(200))
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    creator = db.relationship('User', backref='progress_updates')