"""Query the running local AI service; reads its internal token without printing it."""
import argparse
import sys

import httpx

from app.embeddings import Settings


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('question')
    parser.add_argument('--post-id', type=int)
    args = parser.parse_args()
    settings = Settings()
    with httpx.Client(timeout=90, trust_env=False) as client:
        result = client.post('http://127.0.0.1:8000/internal/ask',
            json={'question': args.question, 'post_id': args.post_id},
            headers={'Authorization': 'Bearer ' + settings.ai_internal_token.get_secret_value()})
    if result.status_code != 200:
        print('问答请求失败，HTTP', result.status_code)
        raise SystemExit(1)
    body = result.json()
    print(body['answer'])
    for source in body['sources']:
        print(f"[{source['id']}] {source['title']} / {source['heading']} -> {source['url']}")


if __name__ == '__main__':
    main()
