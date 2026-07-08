# 明石高専学生食堂システム

要求定義書・外部/内部設計書に基づくプロトタイプです。
- **フロントエンド**: `docs/` (GitHub Pagesで公開)
- **バックエンド**: `backend/` (Renderで公開、Flask + PostgreSQL)

## 1. リポジトリの準備

1. このフォルダ一式をGitHubの新しいリポジトリにpushする。
   ```
   git init
   git add .
   git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/<your-account>/<your-repo>.git
   git push -u origin main
   ```

## 2. バックエンドをRenderにデプロイ

### 2-1. Blueprintで一括作成(推奨)
1. https://dashboard.render.com/ にログイン(クレジットカード不要)。
2. **New > Blueprint** を選択し、pushしたGitHubリポジトリを接続する。
   ルート直下の `render.yaml` が自動検出され、Webサービス(`gakushoku-api`)と無料PostgreSQL(`gakushoku-db`)が同時に作成される。
3. 作成時に以下の環境変数の入力を求められるので設定する。
   - `ADMIN_PASSWORD`: 管理者ログイン用パスワード(自分で決める)
   - `ALLOWED_ORIGIN`: フロントエンドのURL(例: `https://<your-account>.github.io`)
   - `SECRET_KEY` は自動生成される。
4. デプロイ完了後、発行されたURL(例: `https://gakushoku-api.onrender.com`)を控える。

### 2-2. 手動で作成する場合
1. **New > Web Service** でリポジトリを接続し、Root Directoryを `backend` に設定。
2. Build Command: `pip install -r requirements.txt`
3. Start Command: `gunicorn app:app`
4. Instance Type: **Free**
5. **New > PostgreSQL** で無料DBを作成し、接続文字列を Web Service の `DATABASE_URL` に設定。
6. `SECRET_KEY` / `ADMIN_PASSWORD` / `ALLOWED_ORIGIN` を環境変数に追加。

### 2-3. 無料プランの注意点
- Webサービスは15分アクセスがないとスリープし、次回アクセス時に30〜60秒ほど起動待ちが発生する。
- 無料PostgreSQLは作成から**30日で自動的に有効期限が切れる**。長期運用する場合は期限前に有料プランへの変更、または新しい無料DBへの作り直しが必要。

## 3. フロントエンドをGitHub Pagesに公開

1. `docs/config.js` を開き、Renderで発行されたURLに書き換える。
   ```js
   const API_BASE_URL = "https://gakushoku-api.onrender.com";
   ```
2. 変更をcommit & pushする。
3. GitHubリポジトリの **Settings > Pages** を開く。
4. **Source**: `Deploy from a branch`、**Branch**: `main` / `docs` フォルダを選択して **Save**。
5. 数分後、`https://<your-account>.github.io/<your-repo>/` で公開される。

## 4. 動作確認の流れ

1. サイトを開き「学生」タブから「アカウントをお持ちでない方はこちら」で学籍番号・パスワードを登録してログイン。
2. メニュー確認画面でランキングが表示されることを確認(初回アクセスはRenderのスリープ復帰で数十秒かかる場合あり)。
3. 「管理者」タブで、Renderの環境変数に設定した`ADMIN_PASSWORD`を入力してログイン。
4. 管理者メニュー編集画面から `sample_menus.csv` をアップロードし、一括登録を確認。

## 5. 設計書との差分(実装上の必要な拡張)

- パスワード列: `varchar(12)` ではハッシュ値が収まらないため、十分な長さの列に拡張。
- メニューテーブルに `popularity`(人気度)列を追加。ランキング機能(人気順表示)に必須なため。
- レビューの `review_tag`: `char(6)` では複数タグ・日本語ラベルが収まらないため、可変長文字列でカンマ区切り保存に拡張。
- 管理者専用テーブルは設計に存在しないため、環境変数 `ADMIN_PASSWORD` による簡易ログイン方式とした。
- ログイン方式は「学生用メールアドレス」に代えて、内部設計書のテーブル定義に合わせ `student_id`(学籍番号)を主キーとして使用。メールアドレスは任意項目として保持。
