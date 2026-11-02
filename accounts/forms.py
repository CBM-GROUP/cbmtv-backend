from django.contrib.auth.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from django.db import transaction

from .models import User
from .sessions import revoke_sessions


class AccountCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('email', 'name', 'phone', 'location', 'country')


class AccountChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = '__all__'


class AccountPasswordChangeForm(AdminPasswordChangeForm):
    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            revoke_sessions(user)
        return user
