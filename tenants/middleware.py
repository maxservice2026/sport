from django.http import HttpResponseNotFound
from django.utils.deprecation import MiddlewareMixin

from .models import Tenant
from .threadlocal import set_current_tenant, clear_current_tenant


TENANT_QUERY_PARAM = 'tenant'
TENANT_COOKIE_NAME = 'tenant'
DEFAULT_TENANT_SLUG = 'default'


def _normalize_slug(value: str) -> str:
    return (value or '').strip().lower()


class TenantMiddleware(MiddlewareMixin):
    """
    Určí tenant pro request.

    Chování (podobné jako v kartotéce):
    - Pokud je v URL `?tenant=<slug>`, použije se tento tenant a uloží se do cookie.
    - Pokud parametr není, zkusí se tenant cookie (kvůli pohodlí – není potřeba mít parametr v každém URL).
    - Pokud tenant neexistuje, vrátí 404 (nechceme, aby kdokoliv z internetu vytvářel nové tenanty jen návštěvou URL).
    - Pokud není parametr ani cookie, použije se výchozí tenant `default` (vytvoří se automaticky).
    """

    def process_request(self, request):
        clear_current_tenant()

        raw_slug = request.GET.get(TENANT_QUERY_PARAM)
        cookie_slug = request.COOKIES.get(TENANT_COOKIE_NAME)
        slug = _normalize_slug(raw_slug) or _normalize_slug(cookie_slug)

        if not slug:
            tenant, _ = Tenant.objects.get_or_create(
                slug=DEFAULT_TENANT_SLUG,
                defaults={'name': 'Hlavní tenant', 'active': True},
            )
            set_current_tenant(tenant)
            request.tenant = tenant
            return None

        if slug == DEFAULT_TENANT_SLUG:
            tenant, _ = Tenant.objects.get_or_create(
                slug=DEFAULT_TENANT_SLUG,
                defaults={'name': 'Hlavní tenant', 'active': True},
            )
            set_current_tenant(tenant)
            request.tenant = tenant
            request._tenant_cookie_to_set = DEFAULT_TENANT_SLUG if raw_slug else None
            return None

        tenant = Tenant.objects.filter(slug=slug, active=True).first()
        if not tenant:
            return HttpResponseNotFound('Tenant nenalezen.')

        set_current_tenant(tenant)
        request.tenant = tenant
        request._tenant_cookie_to_set = slug if raw_slug else None
        return None

    def process_response(self, request, response):
        slug = getattr(request, '_tenant_cookie_to_set', None)
        if slug:
            response.set_cookie(
                TENANT_COOKIE_NAME,
                slug,
                max_age=60 * 60 * 24 * 365,  # 1 rok
                samesite='Lax',
            )
        return response

