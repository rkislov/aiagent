"""Credential-scoped discovery against official model-list endpoints."""
import httpx
import json

ALLOWED_BASES = {'https://api.openai.com/v1', 'https://api.deepseek.com', 'https://api.deepseek.com/v1'}


def list_models(base_url: str, key: str) -> list[str]:
    base_url = base_url.rstrip('/')
    if base_url not in ALLOWED_BASES:
        raise ValueError('Автозагрузка доступна для официальных адресов OpenAI и DeepSeek')
    if not key:
        raise ValueError('Введите API-ключ')
    try:
        with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
            with client.stream('GET', base_url + '/models', headers={'Authorization': 'Bearer ' + key}) as response:
                if response.status_code != 200:
                    raise ValueError(f'Не удалось загрузить модели: HTTP {response.status_code}. Проверьте ключ и права доступа.')
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 2_000_000:
                        raise ValueError('Слишком большой ответ списка моделей')
        data = json.loads(body)
        rows = data.get('data') if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError('Некорректный список моделей от сервиса')
        models = sorted({r['id'] for r in rows if isinstance(r, dict) and isinstance(r.get('id'), str) and 0 < len(r['id']) <= 256})
        if not models:
            raise ValueError('Сервис не вернул доступных моделей. Прежний список сохранён.')
        return models
    except (httpx.HTTPError, json.JSONDecodeError, UnicodeError):
        raise ValueError('Не удалось загрузить модели: ошибка сети, таймаут или некорректный ответ сервиса') from None
