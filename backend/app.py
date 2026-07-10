"""
明石高専学生食堂システム - バックエンドAPI (Flask)
内部設計書のテーブル定義・データフローダイアグラムに基づく実装。

実行方法(ローカル):
    pip install -r requirements.txt
    python app.py

環境変数:
    DATABASE_URL   PostgreSQL接続文字列(未設定時はSQLiteをローカルに作成)
    SECRET_KEY     トークン署名用の秘密鍵
    ADMIN_PASSWORD 管理者ログイン用パスワード
    ALLOWED_ORIGIN CORSを許可するオリジン(例: https://<user>.github.io)
    RESEND_API_KEY メール送信サービス(Resend)のAPIキー。未設定時はメール送信をスキップしログ出力のみ行う
    FROM_EMAIL     送信元アドレス(独自ドメイン未検証の場合は既定の onboarding@resend.dev のままでよい)
    APP_BASE_URL   このバックエンド自身の公開URL(確認メール内のリンク生成に使用。例: https://gakushoku-api.onrender.com)
"""

import csv
import io
import os
from datetime import datetime

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

# ------------------------------------------------------------------
# アプリ初期化
# ------------------------------------------------------------------
app = Flask(__name__)

db_url = os.environ.get("DATABASE_URL", "sqlite:///gakushoku.db")
if db_url.startswith("postgres://"):
    # RenderのDATABASE_URLはpostgres://で始まるがSQLAlchemyはpostgresql://を要求する
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# Neon(サーバーレスPostgres)はアイドル時にコンピュートをスリープさせるため、
# 起動直後の最初の接続や、久しぶりのリクエストで接続が切れることがある。
# pool_pre_ping で死んだ接続を使う前に検知し、pool_recycle で定期的に張り直す。
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 280,
    "connect_args": {"connect_timeout": 10},
}

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGIN}})

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin-pass-change-me")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000")

db = SQLAlchemy(app)
serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
TOKEN_MAX_AGE = 60 * 60 * 12  # 12時間

CROWD_LABELS = {0.0: "空席あり", 0.5: "やや混雑", 1.0: "満席"}
CROWD_VALUES = {v: k for k, v in CROWD_LABELS.items()}


# ------------------------------------------------------------------
# モデル定義(内部設計書のテーブルに準拠。※は要求機能のための拡張)
# ------------------------------------------------------------------
class User(db.Model):
    """ユーザーテーブル: 学生用メールアドレス、パスワードを保持する"""
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(6), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)  # ※varchar(12)から拡張(ハッシュ値を保存するため)
    is_verified = db.Column(db.Boolean, default=False)  # ※拡張: メールアドレス確認用

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)


class Menu(db.Model):
    """メニュー・在庫テーブル: メニュー名、口数、売り切れステータスを保持する"""
    __tablename__ = "menus"
    id = db.Column(db.Integer, primary_key=True)
    menu_name = db.Column(db.String(40), nullable=False)
    category = db.Column(db.String(20), default="")            # ※拡張: ランキング/表示用
    calorie = db.Column(db.Integer, default=0)
    price = db.Column(db.Integer, default=0)
    initial_stock = db.Column(db.Integer, default=0)            # 口数
    date = db.Column(db.String(10), nullable=True)              # NULL=常設メニュー、値あり=その日限定の日替わりメニュー
    soldout_status = db.Column(db.Boolean, default=False)       # 初期状態は「販売中」(False)
    reporter_id = db.Column(db.String(6), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "menu_name": self.menu_name,
            "category": self.category,
            "calorie": self.calorie,
            "price": self.price,
            "initial_stock": self.initial_stock,
            "date": self.date,
            "soldout_status": self.soldout_status,
            "reporter_id": self.reporter_id,
        }


class Congestion(db.Model):
    """混雑状況テーブル: 混雑ステータス(満席・空席)および最終更新日時を保持する"""
    __tablename__ = "congestion"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    crowd_status = db.Column(db.Float, default=0.0)  # 0.0=空席あり 0.5=やや混雑 1.0=満席
    reporter_id = db.Column(db.String(6), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "date": self.date.isoformat(),
            "crowd_status": self.crowd_status,
            "label": CROWD_LABELS.get(self.crowd_status, "空席あり"),
            "reporter_id": self.reporter_id,
        }


class Review(db.Model):
    """レビューテーブル: メニュー名、評価スコア、コメント、学籍番号を保持する"""
    __tablename__ = "reviews"
    id = db.Column(db.Integer, primary_key=True)
    menu_name = db.Column(db.String(40), nullable=False)
    review_score = db.Column(db.Integer, nullable=False)
    review_msg = db.Column(db.String(400), default="")
    review_tag = db.Column(db.String(150), default="")  # ※char(6)から拡張(複数タグをカンマ区切りで保存)
    reviewer_id = db.Column(db.String(6), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "menu_name": self.menu_name,
            "review_score": self.review_score,
            "review_msg": self.review_msg,
            "review_tag": [t for t in self.review_tag.split(",") if t] if self.review_tag else [],
            "reviewer_id": self.reviewer_id,
            "created_at": self.created_at.isoformat(),
        }


# ------------------------------------------------------------------
# 認証ユーティリティ(1.0 アカウント認証)
# ------------------------------------------------------------------
def issue_token(role, identity):
    return serializer.dumps({"role": role, "identity": identity})


def decode_token(token):
    try:
        return serializer.loads(token, max_age=TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def get_auth_payload():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return decode_token(auth[7:])


def require_role(role):
    payload = get_auth_payload()
    if not payload or payload.get("role") != role:
        return None
    return payload


def send_verification_email(to_email, token):
    """Resend経由で確認メールを送信する。APIキー未設定時はログ出力のみ。"""
    verify_url = f"{APP_BASE_URL}/api/auth/verify/{token}"

    if not RESEND_API_KEY:
        app.logger.warning("RESEND_API_KEY未設定のためメール送信をスキップしました。確認URL: %s", verify_url)
        return

    try:
        requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": FROM_EMAIL,
                "to": [to_email],
                "subject": "【明石高専 学食ナビ】メールアドレスの確認",
                "html": f"""
                    <div style="font-family: sans-serif; line-height: 1.7;">
                      <p>明石高専 学食ナビへのご登録ありがとうございます。</p>
                      <p>以下のボタンをクリックして、登録を完了してください。</p>
                      <p><a href="{verify_url}" style="display:inline-block; background:#1F6F5C; color:#fff; padding:12px 24px; border-radius:8px; text-decoration:none;">登録を完了する</a></p>
                      <p>リンクの有効期限は24時間です。心当たりがない場合はこのメールを破棄してください。</p>
                    </div>
                """,
            },
            timeout=10,
        )
    except requests.RequestException as e:
        app.logger.error("確認メールの送信に失敗しました: %s", e)


# ------------------------------------------------------------------
# ルート: ヘルスチェック
# ------------------------------------------------------------------
@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


# ------------------------------------------------------------------
# 1.0 アカウント認証
# ------------------------------------------------------------------
@app.post("/api/auth/register")
def register():
    """学籍番号+メールアドレス+パスワードでアカウントを作成し、確認メールを送信する。
    メール内のリンクをクリックするまでログインはできない。"""
    data = request.get_json(force=True) or {}
    student_id = str(data.get("student_id", "")).strip()
    email = str(data.get("email", "")).strip()
    password = str(data.get("password", ""))

    if len(student_id) not in (5, 6) or len(password) < 4:
        return jsonify({"error": "学籍番号は5桁または6桁、パスワードは4文字以上で入力してください"}), 400
    if not email or "@" not in email:
        return jsonify({"error": "有効なメールアドレスを入力してください"}), 400
    if User.query.filter_by(student_id=student_id).first():
        return jsonify({"error": "この学籍番号は既に登録されています"}), 409
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "このメールアドレスは既に登録されています"}), 409

    user = User(student_id=student_id, email=email, password_hash=generate_password_hash(password), is_verified=False)
    db.session.add(user)
    db.session.commit()

    token = serializer.dumps({"purpose": "verify_email", "user_id": user.id}, salt="email-verify")
    send_verification_email(email, token)

    return jsonify({"message": "確認メールを送信しました。メール内のリンクをクリックして登録を完了してください。"}), 201


@app.get("/api/auth/verify/<token>")
def verify_email(token):
    """確認メール内のリンク先。クリックされるとアカウントを有効化する。"""
    try:
        data = serializer.loads(token, salt="email-verify", max_age=60 * 60 * 24)  # 24時間有効
    except SignatureExpired:
        return _verify_result_page("リンクの有効期限が切れています", "お手数ですが、もう一度登録をやり直してください。", ok=False)
    except BadSignature:
        return _verify_result_page("無効なリンクです", "URLが正しいかご確認ください。", ok=False)

    user = User.query.get(data.get("user_id"))
    if not user:
        return _verify_result_page("ユーザーが見つかりません", "アカウントが削除された可能性があります。", ok=False)

    user.is_verified = True
    db.session.commit()
    return _verify_result_page("登録が完了しました", "アプリに戻ってログインしてください。", ok=True)


def _verify_result_page(title, message, ok=True):
    color = "#1F6F5C" if ok else "#B5432E"
    icon = "✅" if ok else "⚠️"
    return f"""
    <html><head><meta charset="utf-8"><title>{title}</title></head>
    <body style="font-family: sans-serif; text-align:center; padding:60px 20px; background:#F7F1E1;">
      <h1 style="color:{color};">{icon} {title}</h1>
      <p style="color:#4a2f18;">{message}</p>
    </body></html>
    """, (200 if ok else 400)


@app.post("/api/auth/resend-verification")
def resend_verification():
    data = request.get_json(force=True) or {}
    student_id = str(data.get("student_id", "")).strip()
    user = User.query.filter_by(student_id=student_id).first()
    if not user:
        return jsonify({"error": "ユーザーが見つかりません"}), 404
    if user.is_verified:
        return jsonify({"message": "このアカウントは既に確認済みです"})
    if not user.email:
        return jsonify({"error": "登録済みのメールアドレスがありません"}), 400

    token = serializer.dumps({"purpose": "verify_email", "user_id": user.id}, salt="email-verify")
    send_verification_email(user.email, token)
    return jsonify({"message": "確認メールを再送信しました"})


@app.post("/api/auth/login")
def login_student():
    """照合: 学生用メールアドレス(または学籍番号) + パスワード"""
    data = request.get_json(force=True) or {}
    student_id = str(data.get("student_id", "")).strip()
    password = str(data.get("password", ""))

    user = User.query.filter_by(student_id=student_id).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "学籍番号またはパスワードが正しくありません"}), 401
    if not user.is_verified:
        return jsonify({"error": "メールアドレスの確認がまだ完了していません。届いたメールのリンクをクリックしてください。"}), 403

    token = issue_token("student", user.student_id)
    return jsonify({"token": token, "role": "student", "student_id": user.student_id})


@app.post("/api/auth/admin-login")
def login_admin():
    data = request.get_json(force=True) or {}
    password = str(data.get("password", ""))
    if password != ADMIN_PASSWORD:
        return jsonify({"error": "パスワードが正しくありません"}), 401
    token = issue_token("admin", "admin")
    return jsonify({"token": token, "role": "admin"})


# ------------------------------------------------------------------
# 2.3 メニュー・在庫情報生成 (学生向け出力)
# ------------------------------------------------------------------
@app.get("/api/menus")
def list_menus():
    """date=YYYY-MM-DD を指定すると、その日限定メニュー+常設メニューのみ返す。
    指定なしの場合は全件返す(管理画面での一覧表示用)。"""
    date_param = request.args.get("date")
    query = Menu.query
    if date_param:
        query = query.filter(db.or_(Menu.date.is_(None), Menu.date == "", Menu.date == date_param))

    menus = query.order_by(Menu.id.asc()).all()
    result = []
    for m in menus:
        rs = Review.query.filter_by(menu_name=m.menu_name).all()
        avg = round(sum(r.review_score for r in rs) / len(rs), 2) if rs else None
        d = m.to_dict()
        d["avg_rating"] = avg
        d["review_count"] = len(rs)
        result.append(d)
    return jsonify(result)


# ------------------------------------------------------------------
# 2.1 メニュー一括登録(管理者・CSV)
# ------------------------------------------------------------------
@app.post("/api/admin/menus/bulk")
def bulk_upload_menus():
    if not require_role("admin"):
        return jsonify({"error": "権限がありません"}), 401

    if "file" not in request.files:
        return jsonify({"error": "CSVファイルを添付してください"}), 400

    file = request.files["file"]
    stream = io.StringIO(file.stream.read().decode("utf-8-sig"))
    reader = csv.DictReader(stream)

    created, updated = 0, 0
    for row in reader:
        name = (row.get("menu_name") or "").strip()
        if not name:
            continue
        date_value = (row.get("date") or "").strip() or None

        menu = Menu.query.filter_by(menu_name=name, date=date_value).first()
        if menu is None:
            menu = Menu(menu_name=name, date=date_value, soldout_status=False)
            db.session.add(menu)
            created += 1
        else:
            updated += 1
        menu.category = (row.get("category") or menu.category or "").strip()
        menu.price = int(row.get("price") or menu.price or 0)
        menu.calorie = int(row.get("calorie") or menu.calorie or 0)
        menu.initial_stock = int(row.get("initial_stock") or menu.initial_stock or 0)
        # 一括登録時、売り切れステータスは初期状態(販売中)とする
        menu.soldout_status = False

    db.session.commit()
    return jsonify({"message": f"{created}件を新規作成、{updated}件を更新しました"})


# ------------------------------------------------------------------
# 管理者: メニュー個別追加・編集
# ------------------------------------------------------------------
@app.post("/api/admin/menus")
def add_menu():
    if not require_role("admin"):
        return jsonify({"error": "権限がありません"}), 401
    data = request.get_json(force=True) or {}
    name = str(data.get("menu_name", "")).strip()
    if not name:
        return jsonify({"error": "メニュー名は必須です"}), 400

    menu = Menu(
        menu_name=name,
        category=str(data.get("category", "")).strip(),
        price=int(data.get("price") or 0),
        calorie=int(data.get("calorie") or 0),
        initial_stock=int(data.get("initial_stock") or 0),
        date=(str(data.get("date")).strip() or None) if data.get("date") else None,
        soldout_status=not bool(data.get("on_sale", True)),
    )
    db.session.add(menu)
    db.session.commit()
    return jsonify(menu.to_dict()), 201


@app.patch("/api/admin/menus/<int:menu_id>")
def edit_menu(menu_id):
    if not require_role("admin"):
        return jsonify({"error": "権限がありません"}), 401
    menu = Menu.query.get_or_404(menu_id)
    data = request.get_json(force=True) or {}

    if "category" in data:
        menu.category = str(data["category"]).strip()
    if "price" in data:
        menu.price = int(data["price"])
    if "calorie" in data:
        menu.calorie = int(data["calorie"])
    if "initial_stock" in data:
        menu.initial_stock = int(data["initial_stock"])
    if "date" in data:
        menu.date = str(data["date"]).strip() or None
    if "on_sale" in data:
        menu.soldout_status = not bool(data["on_sale"])

    db.session.commit()
    return jsonify(menu.to_dict())


@app.delete("/api/admin/menus")
def delete_all_menus():
    if not require_role("admin"):
        return jsonify({"error": "権限がありません"}), 401
    count = Menu.query.delete()
    db.session.commit()
    return jsonify({"message": f"{count}件のメニューを全て削除しました"})


@app.delete("/api/admin/menus/<int:menu_id>")
def delete_menu(menu_id):
    if not require_role("admin"):
        return jsonify({"error": "権限がありません"}), 401
    menu = Menu.query.get_or_404(menu_id)
    db.session.delete(menu)
    db.session.commit()
    return jsonify({"message": "削除しました"})


# ------------------------------------------------------------------
# 2.2 売り切れステータス処理(学生入力)
# ------------------------------------------------------------------
@app.post("/api/menus/<int:menu_id>/soldout")
def report_soldout(menu_id):
    payload = require_role("student")
    if not payload:
        return jsonify({"error": "ログインが必要です"}), 401

    menu = Menu.query.get_or_404(menu_id)
    data = request.get_json(force=True) or {}
    status = bool(data.get("soldout", True))

    menu.soldout_status = status
    menu.reporter_id = payload["identity"]
    db.session.commit()
    return jsonify(menu.to_dict())


# ------------------------------------------------------------------
# 3.1〜3.3 混雑報告受付・判定・配信
# ------------------------------------------------------------------
@app.get("/api/congestion")
def get_congestion():
    latest = Congestion.query.order_by(Congestion.date.desc()).first()
    if latest is None:
        return jsonify({"crowd_status": 0.0, "label": "空席あり", "date": None, "reporter_id": None})
    return jsonify(latest.to_dict())


@app.post("/api/congestion/report")
def report_congestion():
    payload = require_role("student")
    if not payload:
        return jsonify({"error": "ログインが必要です"}), 401

    data = request.get_json(force=True) or {}
    label = str(data.get("status", "空席あり"))
    value = CROWD_VALUES.get(label, 0.0)

    record = Congestion(crowd_status=value, reporter_id=payload["identity"], date=datetime.utcnow())
    db.session.add(record)
    db.session.commit()
    return jsonify(record.to_dict())


# ------------------------------------------------------------------
# 4.0 レビュー管理
# ------------------------------------------------------------------
@app.post("/api/reviews")
def submit_review():
    payload = require_role("student")
    if not payload:
        return jsonify({"error": "ログインが必要です"}), 401

    data = request.get_json(force=True) or {}
    menu_name = str(data.get("menu_name", "")).strip()
    score = int(data.get("review_score", 0))
    msg = str(data.get("review_msg", "")).strip()
    tags = data.get("review_tag") or []
    if isinstance(tags, list):
        tags = ",".join(tags)

    if not menu_name or not (1 <= score <= 5):
        return jsonify({"error": "メニューと評価(1〜5)を指定してください"}), 400

    review = Review(
        menu_name=menu_name,
        review_score=score,
        review_msg=msg,
        review_tag=tags,
        reviewer_id=payload["identity"],
    )
    db.session.add(review)
    db.session.commit()
    return jsonify(review.to_dict()), 201


@app.get("/api/menus/<int:menu_id>/reviews")
def public_menu_reviews(menu_id):
    """学生向け: 指定メニューのレビュー一覧を匿名(学籍番号を除く)で返す"""
    menu = Menu.query.get_or_404(menu_id)
    reviews = Review.query.filter_by(menu_name=menu.menu_name).order_by(Review.created_at.desc()).all()
    result = []
    for r in reviews:
        d = r.to_dict()
        d.pop("reviewer_id", None)
        result.append(d)
    return jsonify({"menu_name": menu.menu_name, "reviews": result})


@app.get("/api/reviews")
def list_reviews():
    if not require_role("admin"):
        return jsonify({"error": "権限がありません"}), 401
    reviews = Review.query.order_by(Review.created_at.desc()).all()
    return jsonify([r.to_dict() for r in reviews])


@app.delete("/api/admin/reviews/<int:review_id>")
def delete_review(review_id):
    if not require_role("admin"):
        return jsonify({"error": "権限がありません"}), 401
    review = Review.query.get_or_404(review_id)
    db.session.delete(review)
    db.session.commit()
    return jsonify({"message": "削除しました"})


# ------------------------------------------------------------------
# 初期データ投入(サンプルメニュー)
# ------------------------------------------------------------------
def seed_data():
    if Menu.query.count() > 0:
        return
    samples = [
        ("アジフライ", "定食", 654, 430, True),
        ("回鍋肉", "定食", 610, 430, True),
        ("鶏もものレモンペッパーグリル", "定食", 662, 430, True),
        ("ポークソテーBBQソース", "定食", 776, 430, True),
        ("和風おろしハンバーグ丼", "丼", 601, 380, True),
        ("親子丼", "丼", 638, 380, True),
        ("豚プルコギ丼", "丼", 717, 380, True),
        ("イカ天丼", "丼", 730, 380, True),
        ("日替わり定食", "定食", 790, 550, True),
        ("カツカレー", "カレー", 960, 520, True),
        ("唐揚げ丼", "丼", 860, 480, True),
        ("味噌ラーメン", "麺", 710, 430, True),
        ("きつねうどん", "麺", 510, 320, True),
        ("焼き魚定食", "定食", 690, 580, False),
    ]
    for name, cat, cal, price, on_sale in samples:
        db.session.add(Menu(
            menu_name=name, category=cat, calorie=cal, price=price,
            initial_stock=50, date=None, soldout_status=not on_sale,
        ))
    db.session.commit()


with app.app_context():
    # Neonのコンピュートがスリープから起きる途中だと最初の接続が失敗することがあるため、
    # 少し待ってからリトライする(最大5回)。
    import time
    from sqlalchemy.exc import OperationalError

    for attempt in range(5):
        try:
            db.create_all()
            seed_data()
            break
        except OperationalError as e:
            app.logger.warning("DB接続に失敗しました(%s回目): %s", attempt + 1, e)
            if attempt == 4:
                raise
            time.sleep(3)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
