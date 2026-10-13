from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('accounts', '0009_passwordresetcode')]

    operations = [
        migrations.CreateModel(
            name='DashboardRole',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80, unique=True)),
                ('permissions', models.JSONField(default=list)),
            ],
        ),
        migrations.AddField(
            model_name='user',
            name='dashboard_role',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='accounts.dashboardrole'),
        ),
        migrations.AddField(
            model_name='user',
            name='must_change_password',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='user',
            name='temporary_pin_expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='temporary_pin_attempts',
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
