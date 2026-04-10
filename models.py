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
        elif self.deadline < today:
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


class Notification(db.Model):
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    type = db.Column(db.String(20), default='info')  # info, warning, success, danger
    is_read = db.Column(db.Boolean, default=False)
    link = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='notifications')

    @staticmethod
    def create(user_id, title, message, type='info', link=None):
        notif = Notification(user_id=user_id, title=title, message=message, type=type, link=link)
        db.session.add(notif)
        return notif

    @staticmethod
    def notify_assignment(commitment, user_id):
        """Notify when a commitment is assigned to a user"""
        from flask import url_for
        title = f"Công việc mới được giao: {commitment.title}"
        message = f"Bạn được giao phụ trách cam kết '{commitment.title}' với deadline {commitment.deadline.strftime('%d/%m/%Y')}"
        link = url_for('commitments_detail', commitment_id=commitment.id)
        return Notification.create(user_id, title, message, 'info', link)

    @staticmethod
    def notify_deadline_approaching(commitment, days_left, user_id):
        """Notify when deadline is approaching (3, 7 days)"""
        from flask import url_for
        if days_left <= 0:
            return None
        title = f"Cảnh báo: {commitment.title} sắp hết hạn"
        message = f"Cam kết '{commitment.title}' còn {days_left} ngày đến deadline ({commitment.deadline.strftime('%d/%m/%Y')})"
        link = url_for('commitments_detail', commitment_id=commitment.id)
        return Notification.create(user_id, title, message, 'warning', link)

    @staticmethod
    def notify_overdue(commitment, user_id):
        """Notify when a commitment becomes overdue"""
        from flask import url_for
        title = f"Quá hạn: {commitment.title}"
        message = f"Cam kết '{commitment.title}' đã quá hạn (deadline: {commitment.deadline.strftime('%d/%m/%Y')})"
        link = url_for('commitments_detail', commitment_id=commitment.id)
        return Notification.create(user_id, title, message, 'danger', link)

    @staticmethod
    def notify_completion(commitment, user_id):
        """Notify when a commitment is completed"""
        from flask import url_for
        assignee_name = commitment.assignee.username if commitment.assignee else 'Người dùng'
        title = f"Cam kết hoàn thành: {commitment.title}"
        message = f"{assignee_name} đã hoàn thành cam kết '{commitment.title}' vào deadline {commitment.deadline.strftime('%d/%m/%Y')}"
        link = url_for('commitments_detail', commitment_id=commitment.id)
        return Notification.create(user_id, title, message, 'success', link)


class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    action = db.Column(db.String(50), nullable=False)  # CREATE, UPDATE, DELETE, LOGIN, LOGOUT
    entity_type = db.Column(db.String(50))  # Commitment, Lab, User, ProgressUpdate
    entity_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='activity_logs')

    @staticmethod
    def log(user_id, action, entity_type=None, entity_id=None, details=None, ip_address=None):
        log = ActivityLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            ip_address=ip_address
        )
        db.session.add(log)
        return log