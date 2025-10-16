from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin


# Create your models here.
class UserManager(BaseUserManager):
    def create_user(self, email, name, phone, location, country, password=None, role='user'):
        if not email:
            raise ValueError('Users must have an email address')
        email = self.normalize_email(email)
        user = self.model(
            email=email,
            name=name,
            phone=phone,
            location=location,
            country=country,
            role=role,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, name, phone, location, country, password=None, role='user'):
        if not email:
            raise ValueError('User must have an email address')
        email = self.normalize_email(email)
        user = self.create_user(
            email,
            name=name,
            phone=phone,
            location=location,
            country=country,
            password=password,
            role = 'admin',
        )
        user.is_staff = True
        user.is_superuser = True
        user.save(using=self._db)
        return user

class User (AbstractBaseUser, PermissionsMixin):
    ROLE_CHOICES = (
        ('user', 'User'),
        ('admin', 'Admin'),
        ('internal_admin', 'Internal Admin'),
    )
    AUTH_PROVIDER_CHOICES = (
        ('local', 'Local'),
        ('google', 'Google'),
    )

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20,blank=True, null=True)
    location = models.CharField(max_length=100,blank=True, null=True)
    country = models.CharField(max_length=100,blank=True, null=True)
    role = models.CharField(max_length=15, choices=ROLE_CHOICES, default='user')
    auth_provider = models.CharField(max_length=20, choices=AUTH_PROVIDER_CHOICES, default='local')


    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default = False)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name', 'phone', 'location', 'country']

    def __str__(self):
        return self.email

class RoleChangeLog(models.Model):
    changed_by = models.ForeignKey(User, on_delete=models.CASCADE,related_name="role_changes_made")
    changed_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="role_changes_recieved")
    old_role = models.CharField(max_length=15)
    new_role = models.CharField(max_length=15)
    timestamp = models.DateTimeField(auto_now_add = True)

    def __str__(self):
        return f"{self.changed_by.email}"