from functools import wraps
from flask import session, redirect, url_for, flash, abort


ROLES = {
    'admin': 'Әкімші',
    'manager': 'Менеджер',
    'moderator': 'Модератор',
    'employee': 'Қызметкер',
    'client': 'Клиент',
}


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Жүйеге кіру қажет.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                flash('Жүйеге кіру қажет.', 'warning')
                return redirect(url_for('login'))
            if session.get('role') not in roles:
                flash('Бұл бөлімге рұқсатыңыз жоқ.', 'danger')
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator
