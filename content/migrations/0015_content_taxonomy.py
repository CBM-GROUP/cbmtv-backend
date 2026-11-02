from django.db import migrations, models
import django.db.models.deletion


def classify_unambiguous_legacy(apps, schema_editor):
    Content = apps.get_model('content', 'Content')
    Genre = apps.get_model('content', 'Genre')
    Season = apps.get_model('content', 'Season')
    alias = schema_editor.connection.alias
    with_seasons = set(Season.objects.using(alias).values_list('content_id', flat=True).distinct())
    pending = []
    for title in Content.objects.using(alias).all().iterator(chunk_size=500):
        kind = (title.content_type or '').lower()
        if kind == 'movie':
            title.category, title.format = 'movies', 'standalone'
        elif kind in ('documentary', 'animations'):
            title.category = 'documentaries' if kind == 'documentary' else 'animations'
            title.format = 'series' if title.pk in with_seasons else 'standalone'
        elif kind in ('series', 'miniseries'):
            title.format = 'series'
        else:
            # music/original and unknown historical values need editorial review.
            continue
        pending.append(title)
        if len(pending) == 500:
            Content.objects.using(alias).bulk_update(pending, ['category', 'format'])
            pending.clear()
    if pending:
        Content.objects.using(alias).bulk_update(pending, ['category', 'format'])

    # Link only a single clear legacy label on an already classified title.
    # Keep compound strings and ambiguous legacy types intact for staff review.
    through = Content.genres.through
    flags = {
        'movies': 'for_movies', 'tv_shows': 'for_tv_shows',
        'documentaries': 'for_documentaries', 'animations': 'for_animations',
    }
    rows = Content.objects.using(alias).exclude(category__isnull=True).exclude(genre__isnull=True).exclude(genre='')
    for title in rows.iterator(chunk_size=500):
        label = title.genre.strip()
        if not label or any(separator in label for separator in (',', ';', '/')):
            continue
        flag = flags[title.category]
        genre, _ = Genre.objects.using(alias).get_or_create(
            name_key=label.casefold(), defaults={'name': label, flag: True},
        )
        if not getattr(genre, flag):
            Genre.objects.using(alias).filter(pk=genre.pk).update(**{flag: True})
        through.objects.using(alias).get_or_create(content_id=title.pk, genre_id=genre.pk)


class Migration(migrations.Migration):
    dependencies = [('content', '0014_hero_settings_and_advert_placement')]

    operations = [
        migrations.CreateModel(
            name='Genre',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('name_key', models.CharField(editable=False, max_length=400, unique=True)),
                ('description', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('for_movies', models.BooleanField(default=False)),
                ('for_tv_shows', models.BooleanField(default=False)),
                ('for_documentaries', models.BooleanField(default=False)),
                ('for_animations', models.BooleanField(default=False)),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='children', to='content.genre')),
            ],
        ),
        migrations.AddField(model_name='content', name='category', field=models.CharField(blank=True, choices=[('movies', 'Movies'), ('tv_shows', 'TV Shows'), ('documentaries', 'Documentaries'), ('animations', 'Animations')], db_index=True, max_length=20, null=True)),
        migrations.AddField(model_name='content', name='format', field=models.CharField(blank=True, choices=[('standalone', 'Standalone'), ('series', 'Series')], db_index=True, max_length=20, null=True)),
        migrations.AddField(model_name='content', name='genres', field=models.ManyToManyField(blank=True, related_name='titles', to='content.genre')),
        migrations.AlterField(model_name='content', name='content_type', field=models.CharField(choices=[('movie', 'Movie'), ('music', 'Music'), ('series', 'Series'), ('miniseries', 'Miniseries'), ('original', 'Original'), ('documentary', 'Documentary'), ('animations', 'Animations'), ('tv_show', 'TV Show')], max_length=20)),
        migrations.RunPython(classify_unambiguous_legacy, migrations.RunPython.noop),
    ]
