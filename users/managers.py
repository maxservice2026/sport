from django.contrib.auth.base_user import BaseUserManager


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_queryset(self):
        from tenants.threadlocal import get_current_tenant

        qs = super().get_queryset()
        tenant = get_current_tenant()
        if tenant:
            return qs.filter(tenant=tenant)
        return qs

    def get_by_natural_key(self, username):
        """
        Django auth backend volá `get_by_natural_key(USERNAME_FIELD)`.
        Protože email už nebude globálně unikátní (unikátní bude kombinace tenant+email),
        musíme uživatele dohledat v kontextu aktuálního tenantu.
        """
        from tenants.threadlocal import get_current_tenant

        tenant = get_current_tenant()
        # username je globálně unikátní (syntetické "<tenant>:<email>").
        return super().get_queryset().get(**{self.model.USERNAME_FIELD: username})

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email je povinný.')
        email = self.normalize_email(email)
        if not extra_fields.get('tenant_id') and not extra_fields.get('tenant'):
            from tenants.threadlocal import get_current_tenant

            tenant = get_current_tenant()
            if tenant:
                extra_fields['tenant'] = tenant
        user = self.model(email=email, **extra_fields)
        if user.tenant and user.email:
            user.username = self.model.build_username(user.tenant.slug, user.email)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'admin')

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser musí mít is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser musí mít is_superuser=True.')

        return self.create_user(email, password, **extra_fields)
