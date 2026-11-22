"""Bounded inline images; never fetch user-supplied remote image URLs."""
import base64
import binascii
import io
import warnings

from PIL import Image


def validate_images(values):
    total = 0
    for value in values:
        if len(value) > 400_100:
            raise ValueError("Каждое фото должно быть не больше 300 КБ")
        try:
            header, encoded = value.split(",", 1)
            formats = {"data:image/jpeg;base64": "JPEG", "data:image/png;base64": "PNG", "data:image/webp;base64": "WEBP"}
            if header not in formats:
                raise ValueError("Разрешены только JPEG, PNG и WebP")
            raw = base64.b64decode(encoded, validate=True)
            total += len(raw)
            if len(raw) > 300_000 or total > 1_200_000:
                raise ValueError("Превышен размер фотографий")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format != formats[header] or image.width * image.height > 16_000_000:
                        raise ValueError("Неверный формат или слишком большое разрешение фото")
                    image.verify()
        except (OSError, binascii.Error, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError("Повреждённое или слишком большое изображение") from exc
    return values
