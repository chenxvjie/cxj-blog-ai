"""Local-only gateway test. Temporary sessions and quota counters are cleaned up."""
import hashlib
import hmac
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import psycopg
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict


def main():
    env = dotenv_values('.env')
    backend_env = dotenv_values('../cxj-blog-backend/.env')
    dsn = env['DATABASE_ADMIN_URL']
    params = conninfo_to_dict(dsn)
    if params.get('host') != '127.0.0.1' or params.get('dbname') != 'cxj_blog':
        raise SystemExit('Only local cxj_blog is allowed')
    day = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    guest = 'guest:' + hmac.new(backend_env['AI_QUOTA_SECRET'].encode(), b'127.0.0.1', hashlib.sha256).hexdigest()
    token_hashes = []
    subjects = [guest]
    with psycopg.connect(dsn, autocommit=True) as db:
        user = db.execute("SELECT id FROM sys_user WHERE email='rag-quota@example.test' AND deleted_at IS NULL").fetchone()
        if user:
            user_id = user[0]
        else:
            user_id = db.execute("INSERT INTO sys_user(email,nickname,role,status) VALUES ('rag-quota@example.test','RAG额度测试用户','USER','ACTIVE') RETURNING id").fetchone()[0]
        admin = db.execute("SELECT id FROM sys_user WHERE role='ADMIN' AND status='ACTIVE' AND deleted_at IS NULL").fetchone()
        temporary_admin = None
        if not admin:
            if db.execute("SELECT 1 FROM sys_user WHERE lower(email)='1158189673@qq.com' AND deleted_at IS NULL").fetchone():
                raise SystemExit('Existing disabled administrator; do not modify it for testing')
            temporary_admin = db.execute("INSERT INTO sys_user(email,nickname,role,status) VALUES ('1158189673@qq.com','RAG额度测试管理员','ADMIN','ACTIVE') RETURNING id").fetchone()[0]
            admin = (temporary_admin,)
        subjects.append('user:' + str(user_id))
        for subject in subjects:
            row = db.execute('SELECT used FROM ai_daily_usage WHERE subject=%s AND usage_day=%s', (subject, day)).fetchone()
            if row and row[0]:
                raise SystemExit('Existing quota usage detected; do not overwrite counters')
        tokens = []
        for identity in [user_id, admin[0]]:
            token = secrets.token_urlsafe(32)
            hashed = hashlib.sha256(token.encode()).hexdigest()
            db.execute("INSERT INTO auth_session(token_hash,user_id,expires_at) VALUES (%s,%s,now()+interval '10 minutes')", (hashed, identity))
            token_hashes.append(hashed)
            tokens.append(token)
        private_id = db.execute("SELECT id FROM blog_post WHERE slug='rag-demo-private' AND deleted_at IS NULL").fetchone()[0]
        try:
            def ask(token):
                headers = {'X-Blog-Request': '1'}
                if token:
                    headers['Authorization'] = 'Bearer ' + token
                with httpx.Client(timeout=90, trust_env=False) as client:
                    response = client.post('http://127.0.0.1:8080/api/v1/ai/ask', headers=headers, json={'question': '请回答这篇文章的内容', 'postId': private_id})
                    if response.status_code == 200:
                        assert response.json()['data']['answer']['grounded'] is False
                    return response.status_code
            for label, token, attempts, accepted in [('guest', None, 11, 10), ('user', tokens[0], 21, 20), ('admin', tokens[1], 3, 3)]:
                with ThreadPoolExecutor(max_workers=6) as pool:
                    results = list(pool.map(ask, [token] * attempts))
                assert results.count(200) == accepted and results.count(429) == attempts-accepted, results
                print(label, 'accepted=', results.count(200), 'blocked=', results.count(429))
            with httpx.Client(timeout=5, trust_env=False) as client:
                quota = client.get('http://127.0.0.1:8080/api/v1/ai/quota', headers={'Authorization': 'Bearer ' + tokens[1]}).json()['data']
                assert quota['unlimited'] is True
            print('Concurrent guest/user caps and administrator exemption passed')
        finally:
            for hashed in token_hashes:
                db.execute('DELETE FROM auth_session WHERE token_hash=%s', (hashed,))
            for subject in subjects:
                db.execute('DELETE FROM ai_usage_reservation WHERE subject=%s AND usage_day=%s', (subject, day))
                db.execute('DELETE FROM ai_daily_usage WHERE subject=%s AND usage_day=%s', (subject, day))
            if temporary_admin:
                db.execute('DELETE FROM sys_user WHERE id=%s', (temporary_admin,))


if __name__ == '__main__':
    main()
