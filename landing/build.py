"""Какой коммит сейчас выложен.

Нужен, чтобы выкладку можно было проверить снаружи: страница называет
свой коммит, и проверка после пуша ждёт, пока на сайте появится именно
он. Без этого «автодеплой работает» оставалось бы на слово — вебхук
ответил 202, а выкатилось ли, неизвестно.

Читаем файлы .git напрямую, а не через `git rev-parse`. gunicorn работает
не от того пользователя, которому принадлежит репозиторий, и git в таком
случае отказывается отвечать («dubious ownership») — молча вернул бы
пустоту, и проверка решила бы, что выкладки не было.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build_id(root=ROOT):
    """Короткий хеш выложенного коммита или пустая строка, если не узнать."""
    git = Path(root) / '.git'
    try:
        head = (git / 'HEAD').read_text(encoding='utf-8').strip()
        if not head.startswith('ref: '):
            return head[:7]                      # отделённая голова — сам хеш
        ref = head[len('ref: '):]
        loose = git / ref
        if loose.exists():
            return loose.read_text(encoding='utf-8').strip()[:7]
        # После git gc ссылки ветки уезжают в packed-refs.
        for line in (git / 'packed-refs').read_text(encoding='utf-8').splitlines():
            if line.endswith(' ' + ref):
                return line.split(' ', 1)[0][:7]
    except OSError:
        pass
    return ''


def build_branch(root=ROOT):
    """Ветка, с которой выложен сайт. Пусто — отделённая голова или не узнать.

    Проверка после пуша по ней понимает, касается ли её этот пуш: если
    сайт выкладывается с другой ветки, ждать на нём нового коммита
    бессмысленно, и это не ошибка.
    """
    try:
        head = (Path(root) / '.git' / 'HEAD').read_text(encoding='utf-8').strip()
    except OSError:
        return ''
    prefix = 'ref: refs/heads/'
    return head[len(prefix):] if head.startswith(prefix) else ''
