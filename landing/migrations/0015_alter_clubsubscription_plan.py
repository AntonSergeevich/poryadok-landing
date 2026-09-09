# Вступление в клуб стало бесплатным: появился тариф «Свободный вход»,
# он же теперь стоит по умолчанию. Прежние платные подписки никуда не
# деваются — те, кто успел оплатить, дохаживают свой срок.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("landing", "0014_work_tznak"),
    ]

    operations = [
        migrations.AlterField(
            model_name="clubsubscription",
            name="plan",
            field=models.CharField(
                choices=[
                    ("free", "Свободный вход"),
                    ("month", "Месяц"),
                    ("quarter", "Три месяца"),
                    ("year", "Год"),
                    ("gift", "Доступ клиента (бесплатно)"),
                ],
                default="free",
                max_length=16,
                verbose_name="тариф",
            ),
        ),
    ]
