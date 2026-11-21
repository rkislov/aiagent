"""Public connection templates; never contain credentials."""
PRESETS = {
    'openai': {
        'title': 'ChatGPT / OpenAI API',
        'key_url': 'https://platform.openai.com/api-keys',
        'config': {'type': 'openai', 'base_url': 'https://api.openai.com/v1', 'api_key_env': None,
                   'api_key': '', 'models': ['gpt-4.1-mini', 'gpt-4o-mini']},
    },
    'deepseek': {
        'title': 'DeepSeek API',
        'key_url': 'https://platform.deepseek.com/api_keys',
        'config': {'type': 'openai', 'base_url': 'https://api.deepseek.com', 'api_key_env': None,
                   'api_key': '', 'models': ['deepseek-flash', 'deepseek-v4-pro']},
    },
}
