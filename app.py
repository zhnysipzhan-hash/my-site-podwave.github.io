import os
import sqlite3
import hashlib
import secrets
from datetime import datetime

import requests
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, g, jsonify, abort
)
from werkzeug.utils import secure_filename

from decorators import login_required, role_required, ROLES

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'podwave-secret-key-2024')
app.config['DATABASE'] = os.path.join(app.root_path, 'database.db')
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 350 * 1024 * 1024
app.config['ALLOWED_EXTENSIONS'] = {'mp3', 'wav', 'ogg', 'm4a'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


# ── Database helpers ──────────────────────────────────────────────

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def hash_password(password):
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return f'{salt}${hashed}'


def verify_password(stored, password):
    salt, hashed = stored.split('$')
    return hashlib.sha256((salt + password).encode()).hexdigest() == hashed


def init_db():
    db = get_db()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'client',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT
        );

        CREATE TABLE IF NOT EXISTS podcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            category_id INTEGER,
            author_id INTEGER NOT NULL,
            cover_image TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL,
            FOREIGN KEY (author_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            podcast_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            audio_file TEXT,
            duration TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            views INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            episode_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    ''')

    admin = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
    if not admin:
        now = datetime.now().isoformat()
        db.execute(
            'INSERT INTO users (username, email, password, role, created_at) VALUES (?,?,?,?,?)',
            ('admin', 'admin@podwave.kz', hash_password('admin123'), 'admin', now)
        )
        db.execute(
            'INSERT INTO users (username, email, password, role, created_at) VALUES (?,?,?,?,?)',
            ('manager', 'manager@podwave.kz', hash_password('manager123'), 'manager', now)
        )
        db.execute(
            'INSERT INTO users (username, email, password, role, created_at) VALUES (?,?,?,?,?)',
            ('moderator', 'moderator@podwave.kz', hash_password('mod123'), 'moderator', now)
        )
        db.execute(
            'INSERT INTO users (username, email, password, role, created_at) VALUES (?,?,?,?,?)',
            ('speaker', 'speaker@podwave.kz', hash_password('speaker123'), 'employee', now)
        )

        categories = [
            ('Технология', 'IT, инновациялар және гаджеттер'),
            ('Бизнес', 'Кәсіпкерлік және қаржы'),
            ('Денсаулық', 'Медицина және спорт'),
            ('Музыка', 'Ән, концерт және музыка талқылауы'),
            ('Білім', 'Оқу және ғылым'),
        ]
        for name, desc in categories:
            db.execute('INSERT INTO categories (name, description) VALUES (?,?)', (name, desc))

    db.commit()


with app.app_context():
    init_db()


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


# ── iTunes API ────────────────────────────────────────────────────

def fetch_itunes_trending(limit=12):
    try:
        resp = requests.get(
            'https://itunes.apple.com/search',
            params={'term': 'podcast', 'media': 'podcast', 'limit': limit},
            timeout=8
        )
        resp.raise_for_status()
        results = resp.json().get('results', [])
        trending = []
        for item in results:
            trending.append({
                'title': item.get('collectionName', 'Белгісіз'),
                'artist': item.get('artistName', ''),
                'genre': item.get('primaryGenreName', ''),
                'image': item.get('artworkUrl600') or item.get('artworkUrl100', ''),
                'url': item.get('collectionViewUrl', '#'),
            })
        return trending
    except requests.RequestException:
        return []


# ── Public routes ───────────────────────────────────────────────

@app.route('/')
def index():
    db = get_db()
    podcasts = db.execute('''
        SELECT p.*, c.name as category_name, u.username as author_name,
               (SELECT COUNT(*) FROM episodes e WHERE e.podcast_id = p.id AND e.status = 'approved') as episode_count
        FROM podcasts p
        LEFT JOIN categories c ON p.category_id = c.id
        LEFT JOIN users u ON p.author_id = u.id
        ORDER BY p.created_at DESC LIMIT 8
    ''').fetchall()

    categories = db.execute('SELECT * FROM categories ORDER BY name').fetchall()
    trending = fetch_itunes_trending()
    return render_template('index.html', podcasts=podcasts, categories=categories, trending=trending)


@app.route('/search')
def search():
    db = get_db()
    query = request.args.get('q', '').strip()
    category_id = request.args.get('category', '')
    sort = request.args.get('sort', 'newest')

    sql = '''
        SELECT p.*, c.name as category_name, u.username as author_name,
               (SELECT COUNT(*) FROM episodes e WHERE e.podcast_id = p.id AND e.status = 'approved') as episode_count
        FROM podcasts p
        LEFT JOIN categories c ON p.category_id = c.id
        LEFT JOIN users u ON p.author_id = u.id
        WHERE 1=1
    '''
    params = []

    if query:
        sql += ' AND (p.title LIKE ? OR p.description LIKE ?)'
        params.extend([f'%{query}%', f'%{query}%'])

    if category_id:
        sql += ' AND p.category_id = ?'
        params.append(category_id)

    order_map = {
        'newest': 'p.created_at DESC',
        'oldest': 'p.created_at ASC',
        'title': 'p.title ASC',
    }
    sql += f' ORDER BY {order_map.get(sort, "p.created_at DESC")}'

    podcasts = db.execute(sql, params).fetchall()
    categories = db.execute('SELECT * FROM categories ORDER BY name').fetchall()
    return render_template('search.html', podcasts=podcasts, categories=categories,
                           query=query, category_id=category_id, sort=sort)


@app.route('/podcast/<int:podcast_id>')
def podcast_detail(podcast_id):
    db = get_db()
    podcast = db.execute('''
        SELECT p.*, c.name as category_name, u.username as author_name
        FROM podcasts p
        LEFT JOIN categories c ON p.category_id = c.id
        LEFT JOIN users u ON p.author_id = u.id
        WHERE p.id = ?
    ''', (podcast_id,)).fetchone()
    if not podcast:
        abort(404)

    episodes = db.execute('''
        SELECT * FROM episodes WHERE podcast_id = ? AND status = 'approved'
        ORDER BY created_at DESC
    ''', (podcast_id,)).fetchall()
    return render_template('podcasts/detail.html', podcast=podcast, episodes=episodes)


@app.route('/episode/<int:episode_id>')
def episode_detail(episode_id):
    db = get_db()
    episode = db.execute('''
        SELECT e.*, p.title as podcast_title, p.id as podcast_id
        FROM episodes e JOIN podcasts p ON e.podcast_id = p.id
        WHERE e.id = ? AND e.status = 'approved'
    ''', (episode_id,)).fetchone()
    if not episode:
        abort(404)

    db.execute('UPDATE episodes SET views = views + 1 WHERE id = ?', (episode_id,))
    db.commit()

    comments = db.execute('''
        SELECT c.*, u.username FROM comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.episode_id = ? ORDER BY c.created_at DESC
    ''', (episode_id,)).fetchall()
    return render_template('episodes/detail.html', episode=episode, comments=comments)


@app.route('/episode/<int:episode_id>/comment', methods=['POST'])
@login_required
def add_comment(episode_id):
    content = request.form.get('content', '').strip()
    if not content:
        flash('Пікір бос болмауы тиіс.', 'warning')
        return redirect(url_for('episode_detail', episode_id=episode_id))

    db = get_db()
    db.execute(
        'INSERT INTO comments (episode_id, user_id, content, created_at) VALUES (?,?,?,?)',
        (episode_id, session['user_id'], content, datetime.now().isoformat())
    )
    db.commit()
    flash('Пікіріңіз қосылды!', 'success')
    return redirect(url_for('episode_detail', episode_id=episode_id))


# ── Auth routes ─────────────────────────────────────────────────

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if not username or not email or not password:
            flash('Барлық өрістерді толтырыңыз.', 'danger')
            return render_template('register.html')

        if password != confirm:
            flash('Құпия сөздер сәйкес келмейді.', 'danger')
            return render_template('register.html')

        if len(password) < 6:
            flash('Құпия сөз кемінде 6 таңбадан тұруы керек.', 'danger')
            return render_template('register.html')

        db = get_db()
        existing = db.execute(
            'SELECT id FROM users WHERE username = ? OR email = ?', (username, email)
        ).fetchone()
        if existing:
            flash('Бұл пайдаланушы аты немесе email тіркелген.', 'danger')
            return render_template('register.html')

        db.execute(
            'INSERT INTO users (username, email, password, role, created_at) VALUES (?,?,?,?,?)',
            (username, email, hash_password(password), 'client', datetime.now().isoformat())
        )
        db.commit()
        flash('Тіркелу сәтті! Енді жүйеге кіре аласыз.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()

        if user and verify_password(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            flash(f'Қош келдіңіз, {user["username"]}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Пайдаланушы аты немесе құпия сөз қате.', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Жүйеден шықтыңыз.', 'info')
    return redirect(url_for('index'))


@app.route('/dashboard')
@login_required
def dashboard():
    role = session.get('role')
    redirects = {
        'admin': 'admin_users',
        'manager': 'manager_podcasts',
        'moderator': 'moderator_pending',
        'employee': 'employee_dashboard',
        'client': 'index',
    }
    return redirect(url_for(redirects.get(role, 'index')))


# ── Admin: User CRUD ────────────────────────────────────────────

@app.route('/admin/users')
@role_required('admin')
def admin_users():
    db = get_db()
    users = db.execute('SELECT * FROM users ORDER BY created_at DESC').fetchall()
    return render_template('admin/users.html', users=users, roles=ROLES)


@app.route('/admin/users/add', methods=['GET', 'POST'])
@role_required('admin')
def admin_user_add():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'client')

        if role not in ROLES:
            flash('Жарамсыз рөл.', 'danger')
            return render_template('admin/user_form.html', user=None, roles=ROLES)

        db = get_db()
        try:
            db.execute(
                'INSERT INTO users (username, email, password, role, created_at) VALUES (?,?,?,?,?)',
                (username, email, hash_password(password), role, datetime.now().isoformat())
            )
            db.commit()
            flash('Пайдаланушы қосылды.', 'success')
            return redirect(url_for('admin_users'))
        except sqlite3.IntegrityError:
            flash('Бұл аты немесе email бар.', 'danger')

    return render_template('admin/user_form.html', user=None, roles=ROLES)


@app.route('/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
@role_required('admin')
def admin_user_edit(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        abort(404)

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        role = request.form.get('role', 'client')
        password = request.form.get('password', '')

        if role not in ROLES:
            flash('Жарамсыз рөл.', 'danger')
            return render_template('admin/user_form.html', user=user, roles=ROLES)

        try:
            if password:
                db.execute(
                    'UPDATE users SET username=?, email=?, role=?, password=? WHERE id=?',
                    (username, email, role, hash_password(password), user_id)
                )
            else:
                db.execute(
                    'UPDATE users SET username=?, email=?, role=? WHERE id=?',
                    (username, email, role, user_id)
                )
            db.commit()
            flash('Пайдаланушы жаңартылды.', 'success')
            return redirect(url_for('admin_users'))
        except sqlite3.IntegrityError:
            flash('Бұл аты немесе email бар.', 'danger')

    return render_template('admin/user_form.html', user=user, roles=ROLES)


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@role_required('admin')
def admin_user_delete(user_id):
    if user_id == session['user_id']:
        flash('Өз аккаунтыңызды өшіре алмайсыз.', 'danger')
        return redirect(url_for('admin_users'))

    db = get_db()
    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()
    flash('Пайдаланушы өшірілді.', 'success')
    return redirect(url_for('admin_users'))


# ── Manager: Categories & Podcasts CRUD ─────────────────────────

@app.route('/manager/categories')
@role_required('manager', 'admin')
def manager_categories():
    db = get_db()
    categories = db.execute('SELECT * FROM categories ORDER BY name').fetchall()
    return render_template('manager/categories.html', categories=categories)


@app.route('/manager/categories/add', methods=['POST'])
@role_required('manager', 'admin')
def manager_category_add():
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    if not name:
        flash('Санат атауы қажет.', 'warning')
        return redirect(url_for('manager_categories'))

    db = get_db()
    try:
        db.execute('INSERT INTO categories (name, description) VALUES (?,?)', (name, description))
        db.commit()
        flash('Санат қосылды.', 'success')
    except sqlite3.IntegrityError:
        flash('Бұл санат бар.', 'danger')
    return redirect(url_for('manager_categories'))


@app.route('/manager/categories/<int:cat_id>/edit', methods=['POST'])
@role_required('manager', 'admin')
def manager_category_edit(cat_id):
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    db = get_db()
    db.execute('UPDATE categories SET name=?, description=? WHERE id=?', (name, description, cat_id))
    db.commit()
    flash('Санат жаңартылды.', 'success')
    return redirect(url_for('manager_categories'))


@app.route('/manager/categories/<int:cat_id>/delete', methods=['POST'])
@role_required('manager', 'admin')
def manager_category_delete(cat_id):
    db = get_db()
    db.execute('DELETE FROM categories WHERE id = ?', (cat_id,))
    db.commit()
    flash('Санат өшірілді.', 'success')
    return redirect(url_for('manager_categories'))


@app.route('/manager/podcasts')
@role_required('manager', 'admin')
def manager_podcasts():
    db = get_db()
    podcasts = db.execute('''
        SELECT p.*, c.name as category_name, u.username as author_name
        FROM podcasts p
        LEFT JOIN categories c ON p.category_id = c.id
        LEFT JOIN users u ON p.author_id = u.id
        ORDER BY p.created_at DESC
    ''').fetchall()
    categories = db.execute('SELECT * FROM categories ORDER BY name').fetchall()
    employees = db.execute("SELECT id, username FROM users WHERE role = 'employee'").fetchall()
    return render_template('manager/podcasts.html', podcasts=podcasts,
                           categories=categories, employees=employees)


@app.route('/manager/podcasts/add', methods=['POST'])
@role_required('manager', 'admin')
def manager_podcast_add():
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    category_id = request.form.get('category_id') or None
    author_id = request.form.get('author_id')

    if not title or not author_id:
        flash('Атау және автор қажет.', 'warning')
        return redirect(url_for('manager_podcasts'))

    db = get_db()
    db.execute(
        'INSERT INTO podcasts (title, description, category_id, author_id, created_at) VALUES (?,?,?,?,?)',
        (title, description, category_id, author_id, datetime.now().isoformat())
    )
    db.commit()
    flash('Подкаст қосылды.', 'success')
    return redirect(url_for('manager_podcasts'))


@app.route('/manager/podcasts/<int:podcast_id>/edit', methods=['POST'])
@role_required('manager', 'admin')
def manager_podcast_edit(podcast_id):
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    category_id = request.form.get('category_id') or None

    db = get_db()
    db.execute(
        'UPDATE podcasts SET title=?, description=?, category_id=? WHERE id=?',
        (title, description, category_id, podcast_id)
    )
    db.commit()
    flash('Подкаст жаңартылды.', 'success')
    return redirect(url_for('manager_podcasts'))


@app.route('/manager/podcasts/<int:podcast_id>/delete', methods=['POST'])
@role_required('manager', 'admin')
def manager_podcast_delete(podcast_id):
    db = get_db()
    db.execute('DELETE FROM podcasts WHERE id = ?', (podcast_id,))
    db.commit()
    flash('Подкаст өшірілді.', 'success')
    return redirect(url_for('manager_podcasts'))


# ── Moderator: Episode approval ─────────────────────────────────

@app.route('/moderator/pending')
@role_required('moderator', 'admin')
def moderator_pending():
    db = get_db()
    episodes = db.execute('''
        SELECT e.*, p.title as podcast_title, u.username as author_name
        FROM episodes e
        JOIN podcasts p ON e.podcast_id = p.id
        JOIN users u ON p.author_id = u.id
        WHERE e.status = 'pending'
        ORDER BY e.created_at ASC
    ''').fetchall()
    return render_template('moderator/pending.html', episodes=episodes)


@app.route('/moderator/episode/<int:episode_id>/<action>', methods=['POST'])
@role_required('moderator', 'admin')
def moderator_action(episode_id, action):
    if action not in ('approved', 'rejected'):
        abort(400)

    db = get_db()
    db.execute('UPDATE episodes SET status = ? WHERE id = ?', (action, episode_id))
    db.commit()
    label = 'мақұлданды' if action == 'approved' else 'қабылданбады'
    flash(f'Эпизод {label}.', 'success')
    return redirect(url_for('moderator_pending'))


# ── Employee: Upload & stats ────────────────────────────────────

@app.route('/employee/dashboard')
@role_required('employee', 'admin')
def employee_dashboard():
    db = get_db()
    user_id = session['user_id'] if session['role'] == 'employee' else None

    if user_id:
        podcasts = db.execute(
            'SELECT * FROM podcasts WHERE author_id = ? ORDER BY created_at DESC', (user_id,)
        ).fetchall()
        stats = db.execute('''
            SELECT
                COUNT(e.id) as total_episodes,
                SUM(CASE WHEN e.status = 'approved' THEN 1 ELSE 0 END) as approved,
                SUM(CASE WHEN e.status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN e.status = 'rejected' THEN 1 ELSE 0 END) as rejected,
                COALESCE(SUM(e.views), 0) as total_views
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE p.author_id = ?
        ''', (user_id,)).fetchone()
    else:
        podcasts = db.execute('SELECT * FROM podcasts ORDER BY created_at DESC LIMIT 10').fetchall()
        stats = db.execute('''
            SELECT COUNT(*) as total_episodes,
                   SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) as approved,
                   SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
                   SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) as rejected,
                   COALESCE(SUM(views), 0) as total_views
            FROM episodes
        ''').fetchone()

    categories = db.execute('SELECT * FROM categories ORDER BY name').fetchall()
    return render_template('employee/dashboard.html', podcasts=podcasts, stats=stats, categories=categories)


@app.route('/employee/upload', methods=['GET', 'POST'])
@role_required('employee', 'admin')
def employee_upload():
    db = get_db()
    user_id = session['user_id']
    podcasts = db.execute(
        'SELECT * FROM podcasts WHERE author_id = ?', (user_id,)
    ).fetchall()

    if request.method == 'POST':
        podcast_id = request.form.get('podcast_id')
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        duration = request.form.get('duration', '').strip()
        audio = request.files.get('audio_file')

        if not podcast_id or not title:
            flash('Подкаст және атау қажет.', 'warning')
            return render_template('employee/upload.html', podcasts=podcasts)

        filename = None
        if audio and audio.filename and allowed_file(audio.filename):
            filename = secure_filename(f'{secrets.token_hex(8)}_{audio.filename}')
            audio.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        db.execute(
            'INSERT INTO episodes (podcast_id, title, description, audio_file, duration, status, created_at) VALUES (?,?,?,?,?,?,?)',
            (podcast_id, title, description, filename, duration, 'pending', datetime.now().isoformat())
        )
        db.commit()
        flash('Эпизод жүктелді. Модератор тексеруді күтіңіз.', 'success')
        return redirect(url_for('employee_dashboard'))

    return render_template('employee/upload.html', podcasts=podcasts)


@app.route('/employee/podcast/add', methods=['POST'])
@role_required('employee', 'admin')
def employee_podcast_add():
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    category_id = request.form.get('category_id') or None

    if not title:
        flash('Подкаст атауы қажет.', 'warning')
        return redirect(url_for('employee_dashboard'))

    db = get_db()
    categories = db.execute('SELECT id FROM categories WHERE id = ?', (category_id,)).fetchone() if category_id else True

    db.execute(
        'INSERT INTO podcasts (title, description, category_id, author_id, created_at) VALUES (?,?,?,?,?)',
        (title, description, category_id if categories else None, session['user_id'], datetime.now().isoformat())
    )
    db.commit()
    flash('Жаңа подкаст арнасы құрылды.', 'success')
    return redirect(url_for('employee_dashboard'))


# ── API endpoint for iTunes (AJAX) ──────────────────────────────

@app.route('/api/trending')
def api_trending():
    return jsonify(fetch_itunes_trending())


# ── Error handlers ──────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403


@app.errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404


if __name__ == '__main__':
    app.run(debug=True, port=5000)
