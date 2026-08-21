"""Выдача заказчику доступа в кабинет.

Кабинет заводит исполнитель, а не заказчик сам: свободной регистрации
здесь нет и не нужно. Клиентов единицы, каждый — событие, и «зарегистрируйтесь,
подтвердите почту» на этом объёме только мешает.

Правило про пароль одно и оно не обсуждается: **он показывается ровно
один раз**. В базе лежит хеш, и «посмотреть пароль заказчика» потом нельзя.
Это не недоделка, а единственный правильный способ хранить пароли:
потерялся — выдаётся новый, это одна кнопка.
"""
import logging
import re
import secrets

from django.contrib.auth.models import User
from django.urls import reverse

logger = logging.getLogger(__name__)

# Пароль диктуют голосом или пересылают в мессенджер. Символы, которые
# на слух и на вид неразличимы, из алфавита выброшены: «единица или эль»
# — это лишний звонок, а звонок стоит дороже двух лишних букв в пароле.
ALPHABET = 'abcdefghijkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ23456789'

TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
}


def make_password(length=10):
    """Пароль, который можно продиктовать по телефону."""
    return ''.join(secrets.choice(ALPHABET) for _ in range(length))


def translit(text):
    """«Соколова» -> «sokolova». Всё непонятное выбрасываем."""
    out = []
    for char in (text or '').lower():
        out.append(TRANSLIT.get(char, char))
    return re.sub(r'[^a-z0-9]', '', ''.join(out))


def make_login(name):
    """Логин из имени: короткий, произносимый, свободный.

    Не почта: у заказчика её может не быть вовсе или он может её не помнить,
    а войти ему всё равно нужно. Логин же диктуется по телефону за пять
    секунд и записывается на бумажке без ошибок.

    Совпадения разводим числом, а не случайными буквами: «sokolova2»
    диктуется так же легко, как «sokolova», а «sokolova7f» — уже нет.
    """
    base = translit(name)[:20] or 'client'
    if not User.objects.filter(username=base).exists():
        return base
    for suffix in range(2, 100):
        candidate = f'{base}{suffix}'
        if not User.objects.filter(username=candidate).exists():
            return candidate
    # Сто однофамильцев — случай, до которого мы не доживём, но падать
    # на нём всё равно нельзя.
    return f'{base}{secrets.randbelow(9000) + 1000}'


def issue(client, request=None):
    """Завести (или перевыдать) доступ. Возвращает словарь для показа.

    Повторный вызов на том же клиенте не плодит вторую учётную запись,
    а меняет пароль у существующей: «выдать доступ» и «потерялся пароль» —
    это одна и та же кнопка, и разбираться, какая из них сейчас нужна,
    человек не должен.
    """
    password = make_password()
    user = client.user

    if user is None:
        user = User.objects.create_user(
            username=make_login(client.name),
            email=client.email or '',
            password=password,
            first_name=client.name[:150],
        )
        client.user = user
        client.save(update_fields=['user', 'updated_at'])
        logger.info('Кабинет: заведён доступ «%s» для клиента %s',
                    user.username, client.pk)
    else:
        user.set_password(password)
        if client.email:
            user.email = client.email
        # Отключённый доступ включается обратно вместе с новым паролем:
        # раз пароль выдают заново, значит человека снова пускают.
        user.is_active = True
        user.save()
        logger.info('Кабинет: пароль перевыдан для «%s»', user.username)

    path = reverse('cabinet')
    url = request.build_absolute_uri(path) if request else path
    first = (client.name or '').split()[0] if client.name else ''
    greeting = f'{first}, здравствуйте!' if first else 'Здравствуйте!'

    return {
        'login': user.username,
        'password': password,
        'url': url,
        # Готовое сообщение, а не три поля для переписывания. Доступ
        # отправляют в мессенджер, и собирать текст руками — это лишняя
        # минута и шанс перепутать символ в пароле.
        'text': (
            f'{greeting} Открыл вам кабинет по проекту.\n\n'
            f'Вход: {url}\n'
            f'Логин: {user.username}\n'
            f'Пароль: {password}\n\n'
            'В кабинете видно, на каком этапе работа, что сделано и что '
            'нужно от вас. Пароль можно поменять внутри, в любой момент.'
        ),
    }


def revoke(client):
    """Закрыть доступ, не удаляя учётную запись.

    Удалять нельзя: за ней стоят отметки о выполненных задачах — кто
    и когда что закрыл. Стереть это одной кнопкой значит потерять историю
    проекта ровно тогда, когда она понадобится.
    """
    user = client.user
    if user is None or not user.is_active:
        return False
    user.is_active = False
    user.save(update_fields=['is_active'])
    logger.info('Кабинет: доступ «%s» закрыт', user.username)
    return True
